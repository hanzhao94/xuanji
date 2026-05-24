"""
xuanji A2A协议 — Agent-to-Agent通信

A2A (Agent-to-Agent) 协议：Agent之间发现、注册、调用和订阅的标准协议。
基于JSON-RPC 2.0 over HTTP，零外部依赖。

协议操作：
- register: Agent向注册中心注册自身能力
- discover: 按能力/名称搜索其他Agent
- invoke: 同步/异步调用其他Agent
- subscribe: 事件订阅+回调
- result: 异步调用结果获取

用法:
    # 服务端
    server = A2AServer(port=8080)
    server.on_invoke("calculator", lambda params: {"result": _safe_eval_math(params["expr"])})
    server.start()
    
    # 客户端
    client = A2AClient("http://localhost:8080")
    result = client.invoke("calculator", {"expr": "2+3"})
    
    # 发现
    registry = A2ARegistry()
    registry.register("my-agent", "http://localhost:8080", capabilities=["calculator"])
    agents = registry.discover(capability="calculator")
"""

import json
import logging
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, urlparse, urljoin



def _safe_eval_math(expr: str):
    """安全的数学表达式求值，替代eval()。只允许加减乘除幂取模。"""
    import ast, operator
    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.Mod: operator.mod,
    }
    def _eval(node):
        if isinstance(node, (ast.Num, ast.Constant)):
            val = node.n if hasattr(node, 'n') else node.value
            if not isinstance(val, (int, float)):
                raise ValueError(f'不允许: {type(val).__name__}')
            return val
        if isinstance(node, ast.BinOp):
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            return ops[type(node.op)](_eval(node.operand))
        raise ValueError(f'不允许: {ast.dump(node)}')
    tree = ast.parse(expr.strip(), mode='eval')
    return _eval(tree.body)
logger = logging.getLogger(__name__)


# ─── 异常 ──────────────────────────────────────────────────

class A2AError(Exception):
    """A2A协议错误"""
    def __init__(self, code: int = -1, message: str = "", data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"A2A Error {code}: {message}")


# ─── 注册中心 ──────────────────────────────────────────────

class A2ARegistry:
    """Agent注册中心（内存实现）
    
    管理Agent注册、发现、心跳。
    """
    
    def __init__(self, heartbeat_timeout: float = 60.0):
        self._agents: Dict[str, Dict] = {}
        self._heartbeat_timeout = heartbeat_timeout
        self._lock = threading.Lock()
    
    def register(self, name: str, endpoint: str,
                 capabilities: Optional[List[str]] = None,
                 version: str = "1.0.0",
                 metadata: Optional[Dict] = None) -> str:
        """注册Agent
        
        Args:
            name: Agent名称
            endpoint: HTTP端点URL
            capabilities: 能力列表
            version: 版本号
            metadata: 额外元数据
        
        Returns:
            注册ID
        """
        reg_id = f"{name}:{uuid.uuid4().hex[:8]}"
        
        with self._lock:
            self._agents[reg_id] = {
                "reg_id": reg_id,
                "name": name,
                "endpoint": endpoint,
                "capabilities": capabilities or [],
                "version": version,
                "metadata": metadata or {},
                "registered_at": time.time(),
                "last_heartbeat": time.time(),
                "status": "active",
            }
        
        logger.info(f"Agent注册: {name} @ {endpoint} (id={reg_id})")
        return reg_id
    
    def unregister(self, reg_id: str) -> bool:
        """注销Agent"""
        with self._lock:
            if reg_id in self._agents:
                del self._agents[reg_id]
                logger.info(f"Agent注销: {reg_id}")
                return True
        return False
    
    def heartbeat(self, reg_id: str) -> bool:
        """Agent心跳"""
        with self._lock:
            agent = self._agents.get(reg_id)
            if agent:
                agent["last_heartbeat"] = time.time()
                agent["status"] = "active"
                return True
        return False
    
    def discover(self, name: Optional[str] = None,
                 capability: Optional[str] = None,
                 status: str = "active") -> List[Dict]:
        """发现Agent
        
        Args:
            name: 按名称过滤
            capability: 按能力过滤
            status: 状态过滤
        
        Returns:
            匹配的Agent列表
        """
        now = time.time()
        with self._lock:
            results = []
            for agent in self._agents.values():
                # 心跳超时检查
                if now - agent["last_heartbeat"] > self._heartbeat_timeout:
                    agent["status"] = "stale"
                
                if agent["status"] != status:
                    continue
                if name and agent["name"] != name:
                    continue
                if capability and capability not in agent["capabilities"]:
                    continue
                results.append(dict(agent))
        return results
    
    def get_agent(self, reg_id: str) -> Optional[Dict]:
        """获取单个Agent信息"""
        with self._lock:
            agent = self._agents.get(reg_id)
            return dict(agent) if agent else None
    
    def list_all(self) -> List[Dict]:
        """列出所有Agent"""
        with self._lock:
            return [dict(a) for a in self._agents.values()]
    
    def cleanup_stale(self, timeout: Optional[float] = None) -> int:
        """清理超时Agent
        
        Returns:
            清理数量
        """
        t = timeout or self._heartbeat_timeout
        now = time.time()
        removed = 0
        with self._lock:
            stale_ids = [
                rid for rid, a in self._agents.items()
                if now - a["last_heartbeat"] > t
            ]
            for rid in stale_ids:
                del self._agents[rid]
                removed += 1
        return removed
    
    def stats(self) -> Dict:
        """统计信息"""
        with self._lock:
            agents = list(self._agents.values())
        return {
            "total": len(agents),
            "active": sum(1 for a in agents if a["status"] == "active"),
            "stale": sum(1 for a in agents if a["status"] == "stale"),
        }


# ─── JSON-RPC 工具 ─────────────────────────────────────────

def _make_request(method: str, params: Any = None, req_id: int = 1) -> Dict:
    """构建JSON-RPC请求"""
    req = {
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id,
    }
    if params is not None:
        req["params"] = params
    return req


def _parse_response(data: bytes) -> Dict:
    """解析JSON-RPC响应"""
    resp = json.loads(data)
    if "error" in resp:
        err = resp["error"]
        raise A2AError(
            code=err.get("code", -1),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )
    return resp.get("result", {})


def _http_post(url: str, body: Dict, timeout: float = 30.0) -> Dict:
    """发送HTTP POST请求"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=timeout)
        return _parse_response(resp.read())
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            err_data = json.loads(body_text)
            raise A2AError(code=e.code, message=err_data.get("error", {}).get("message", body_text))
        except (json.JSONDecodeError, ValueError):
            raise A2AError(code=e.code, message=body_text)
    except URLError as e:
        raise A2AError(code=-1, message=f"网络错误: {e}")


# ─── A2A 服务端 ────────────────────────────────────────────

class A2AServer:
    """A2A协议服务端
    
    提供JSON-RPC over HTTP接口，支持invoke/subscribe等操作。
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080,
                 name: str = "xuanji", version: str = "1.0.0"):
        self.host = host
        self.port = port
        self.name = name
        self.version = version
        self._handlers: Dict[str, Callable] = {}
        self._subscriptions: Dict[str, List[Callable]] = {}
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._request_id = 0
    
    def on_invoke(self, method: str, handler: Callable) -> Callable:
        """注册invoke处理器（装饰器）
        
        处理器签名: handler(params: Dict) -> Any
        """
        self._handlers[method] = handler
        logger.debug(f"注册invoke处理器: {method}")
        return handler
    
    def on_subscribe(self, event: str, callback: Callable) -> None:
        """注册事件订阅回调
        
        回调签名: callback(event_data: Dict) -> None
        """
        with self._lock:
            if event not in self._subscriptions:
                self._subscriptions[event] = []
            self._subscriptions[event].append(callback)
    
    def emit(self, event: str, data: Any) -> int:
        """发射事件给订阅者
        
        Returns:
            通知的订阅者数量
        """
        with self._lock:
            callbacks = list(self._subscriptions.get(event, []))
        
        count = 0
        for cb in callbacks:
            try:
                cb(data)
                count += 1
            except Exception as e:
                logger.error(f"事件回调异常 ({event}): {e}")
        return count
    
    def start(self, blocking: bool = False) -> None:
        """启动A2A服务器
        
        Args:
            blocking: 是否阻塞当前线程
        """
        if self._running:
            logger.warning("A2A服务器已在运行")
            return
        
        server_ref = self
        
        class _Handler(BaseHTTPRequestHandler):
            """内部HTTP处理器"""
            
            def do_POST(self):
                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    req = json.loads(body)
                    
                    method = req.get("method", "")
                    params = req.get("params", {})
                    req_id = req.get("id", 0)
                    
                    # 路由到处理器
                    if method in server_ref._handlers:
                        result = server_ref._handlers[method](params)
                        response = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": result,
                        }
                    else:
                        response = {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"方法未找到: {method}",
                            },
                        }
                    
                    resp_body = json.dumps(response, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
                
                except A2AError as e:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req.get("id", 0),
                        "error": {"code": e.code, "message": e.message},
                    }
                    resp_body = json.dumps(response).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(resp_body)
                
                except Exception as e:
                    logger.error(f"A2A请求处理异常: {e}")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode())
            
            def do_GET(self):
                """健康检查和元数据"""
                if self.path == "/health":
                    body = json.dumps({"status": "ok", "name": server_ref.name}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/metadata":
                    body = json.dumps({
                        "name": server_ref.name,
                        "version": server_ref.version,
                        "methods": list(server_ref._handlers.keys()),
                    }, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, format, *args):
                """静默默认日志"""
                pass
        
        self._server = HTTPServer((self.host, self.port), _Handler)
        self._running = True
        
        if blocking:
            logger.info(f"A2A服务器启动于 http://{self.host}:{self.port}")
            self._server.serve_forever()
        else:
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="a2a-server",
            )
            self._thread.start()
            logger.info(f"A2A服务器启动于 http://{self.host}:{self.port}")
    
    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server = None
        logger.info("A2A服务器已停止")
    
    @property
    def running(self) -> bool:
        return self._running


# ─── A2A 客户端 ────────────────────────────────────────────

class A2AClient:
    """A2A协议客户端
    
    通过HTTP调用远程Agent。
    """
    
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_id = 0
        self._lock = threading.Lock()
        self._pending: Dict[int, Dict] = {}  # 异步调用结果缓存
    
    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id
    
    def invoke(self, method: str, params: Any = None,
               timeout: Optional[float] = None) -> Any:
        """同步调用远程Agent
        
        Args:
            method: 方法名
            params: 参数
            timeout: 超时秒数
        
        Returns:
            调用结果
        """
        req = _make_request(method, params, self._next_id())
        url = urljoin(self.base_url + "/", "/invoke")
        result = _http_post(url, req, timeout=timeout or self.timeout)
        return result
    
    def invoke_async(self, method: str, params: Any = None) -> str:
        """异步调用远程Agent
        
        Args:
            method: 方法名
            params: 参数
        
        Returns:
            任务ID（用于后续获取结果）
        """
        req = _make_request(method, params, self._next_id())
        url = urljoin(self.base_url + "/", "/invoke_async")
        result = _http_post(url, req, timeout=self.timeout)
        task_id = result.get("task_id", str(uuid.uuid4()))
        self._pending[task_id] = {"status": "pending", "created_at": time.time()}
        return task_id
    
    def get_result(self, task_id: str) -> Any:
        """获取异步调用结果
        
        Args:
            task_id: 任务ID
        
        Returns:
            结果数据
        
        Raises:
            A2AError: 任务未完成或失败
        """
        url = urljoin(self.base_url + "/", "/result")
        req = _make_request("get_result", {"task_id": task_id}, self._next_id())
        result = _http_post(url, req, timeout=self.timeout)
        
        if result.get("status") == "done":
            if task_id in self._pending:
                self._pending[task_id]["status"] = "done"
            return result.get("data")
        elif result.get("status") == "error":
            raise A2AError(-1, result.get("error", "任务失败"))
        else:
            raise A2AError(-1, "任务未完成")
    
    def wait_result(self, task_id: str, poll_interval: float = 0.5,
                    max_wait: float = 120.0) -> Any:
        """轮询等待异步结果
        
        Args:
            task_id: 任务ID
            poll_interval: 轮询间隔秒数
            max_wait: 最大等待秒数
        
        Returns:
            结果数据
        """
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                return self.get_result(task_id)
            except A2AError as e:
                if "未完成" in e.message:
                    time.sleep(poll_interval)
                else:
                    raise
        raise A2AError(-1, f"等待结果超时({max_wait}s): {task_id}")
    
    def subscribe(self, event: str, callback_url: str) -> str:
        """订阅事件
        
        Args:
            event: 事件名
            callback_url: 回调URL（服务端POST到此URL推送事件）
        
        Returns:
            订阅ID
        """
        req = _make_request("subscribe", {
            "event": event,
            "callback_url": callback_url,
        }, self._next_id())
        url = urljoin(self.base_url + "/", "/subscribe")
        result = _http_post(url, req, timeout=self.timeout)
        return result.get("subscription_id", "")
    
    def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅"""
        req = _make_request("unsubscribe", {
            "subscription_id": subscription_id,
        }, self._next_id())
        url = urljoin(self.base_url + "/", "/unsubscribe")
        try:
            _http_post(url, req, timeout=self.timeout)
            return True
        except A2AError:
            return False
    
    def discover(self, capability: Optional[str] = None,
                 name: Optional[str] = None) -> List[Dict]:
        """发现Agent（通过注册中心）
        
        Args:
            capability: 按能力过滤
            name: 按名称过滤
        
        Returns:
            Agent列表
        """
        params = {}
        if capability:
            params["capability"] = capability
        if name:
            params["name"] = name
        
        req = _make_request("discover", params, self._next_id())
        url = urljoin(self.base_url + "/", "/discover")
        result = _http_post(url, req, timeout=self.timeout)
        return result.get("agents", [])
    
    def get_metadata(self) -> Dict:
        """获取远程Agent元数据"""
        try:
            url = urljoin(self.base_url + "/", "/metadata")
            req = Request(url, headers={"Accept": "application/json"})
            resp = urlopen(req, timeout=self.timeout)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"获取元数据失败: {e}")
            return {}
    
    def __repr__(self):
        return f"<A2AClient {self.base_url}>"
