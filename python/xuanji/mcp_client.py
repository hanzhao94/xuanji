"""
xuanji MCP客户端

MCP (Model Context Protocol) — 标准工具协议。
通过stdio启动MCP Server进程，用JSON-RPC 2.0通信。

用法:
    from xuanji.mcp_client import MCPClient, MCPManager
    
    # 单个MCP Server
    client = MCPClient("filesystem")
    client.connect_stdio("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/home"])
    tools = client.list_tools()
    result = client.call_tool("read_file", {"path": "/home/test.txt"})
    client.disconnect()
    
    # 多个MCP Server（从配置自动创建）
    manager = MCPManager()
    manager.from_config({
        "filesystem": "npx -y @modelcontextprotocol/server-filesystem /home",
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    })
    all_tools = manager.get_all_tools()
    result = manager.call_tool("filesystem", "read_file", {"path": "/test.txt"})
    manager.disconnect_all()
"""

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class MCPError(Exception):
    """MCP协议错误"""
    def __init__(self, code: int = -1, message: str = "", data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP Error {code}: {message}")


class MCPToolInfo:
    """MCP工具信息"""
    
    __slots__ = ("name", "description", "input_schema", "server_name")
    
    def __init__(self, name: str = "", description: str = "", 
                 input_schema: Optional[Dict] = None, server_name: str = ""):
        self.name = name
        self.description = description
        self.input_schema = input_schema or {}
        self.server_name = server_name
    
    def __repr__(self):
        return f"<MCPTool '{self.name}' from={self.server_name}>"
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "server": self.server_name,
        }


class MCPClient:
    """单个MCP Server客户端
    
    通过subprocess启动MCP Server进程，
    通过stdin/stdout用JSON-RPC 2.0通信。
    """
    
    def __init__(self, name: str = ""):
        self.name = name
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._connected = False
        self._server_info: Dict = {}
        self._tools_cache: Optional[List[MCPToolInfo]] = None
    
    @property
    def connected(self) -> bool:
        return self._connected and self._process is not None and self._process.poll() is None
    
    # ============================================================
    # 连接
    # ============================================================
    
    def connect_stdio(self, command: str, args: Optional[List[str]] = None,
                      env: Optional[Dict[str, str]] = None,
                      cwd: Optional[str] = None,
                      timeout: float = 30.0) -> Dict:
        """启动MCP Server进程，通过stdio通信
        
        Args:
            command: 可执行文件（如 "npx", "python"）
            args: 参数列表
            env: 环境变量（合并到当前环境）
            cwd: 工作目录
            timeout: 初始化超时秒数
        
        Returns:
            Server的initialize响应
        
        Raises:
            MCPError: 连接或初始化失败
        """
        if self._connected:
            self.disconnect()
        
        cmd = [command] + (args or [])
        
        # 合并环境变量
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                cwd=cwd,
                bufsize=0,  # 无缓冲
            )
        except FileNotFoundError:
            raise MCPError(-1, f"命令不存在: {command}")
        except Exception as e:
            raise MCPError(-1, f"启动失败: {e}")
        
        # MCP初始化握手
        try:
            result = self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "xuanji",
                    "version": "0.1.0",
                },
            }, timeout=timeout)
            
            self._server_info = result
            self._connected = True
            
            # 发送initialized通知
            self._notify("notifications/initialized", {})
            
            return result
            
        except Exception as e:
            self.disconnect()
            raise MCPError(-1, f"初始化失败: {e}")
    
    # ============================================================
    # 工具操作
    # ============================================================
    
    def list_tools(self, force: bool = False) -> List[MCPToolInfo]:
        """列出MCP Server提供的工具
        
        Args:
            force: 强制刷新（否则用缓存）
        
        Returns:
            工具列表
        """
        if not self.connected:
            raise MCPError(-1, "未连接")
        
        if self._tools_cache is not None and not force:
            return self._tools_cache
        
        result = self._rpc("tools/list", {})
        tools = []
        
        for t in result.get("tools", []):
            tool = MCPToolInfo(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.name,
            )
            tools.append(tool)
        
        self._tools_cache = tools
        return tools
    
    def call_tool(self, tool_name: str, params: Optional[Dict] = None,
                  timeout: float = 60.0) -> Any:
        """调用MCP工具
        
        Args:
            tool_name: 工具名
            params: 调用参数
            timeout: 超时秒数
        
        Returns:
            工具执行结果
        
        Raises:
            MCPError: 调用失败
        """
        if not self.connected:
            raise MCPError(-1, "未连接")
        
        result = self._rpc("tools/call", {
            "name": tool_name,
            "arguments": params or {},
        }, timeout=timeout)
        
        # MCP工具返回content数组
        content = result.get("content", [])
        if not content:
            return result
        
        # 简化返回：单个文本内容直接返回文本
        if len(content) == 1 and content[0].get("type") == "text":
            return content[0].get("text", "")
        
        return content
    
    # ============================================================
    # 资源操作（可选）
    # ============================================================
    
    def list_resources(self) -> List[Dict]:
        """列出MCP Server提供的资源"""
        if not self.connected:
            raise MCPError(-1, "未连接")
        
        result = self._rpc("resources/list", {})
        return result.get("resources", [])
    
    def read_resource(self, uri: str) -> Any:
        """读取资源"""
        if not self.connected:
            raise MCPError(-1, "未连接")
        
        result = self._rpc("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        if contents and len(contents) == 1:
            return contents[0].get("text", contents[0])
        return contents
    
    # ============================================================
    # 断开连接
    # ============================================================
    
    def disconnect(self):
        """关闭MCP Server连接"""
        self._connected = False
        self._tools_cache = None
        
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except Exception:
                pass
            
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            
            self._process = None
    
    # ============================================================
    # JSON-RPC 通信
    # ============================================================
    
    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id
    
    def _rpc(self, method: str, params: Dict, timeout: float = 30.0) -> Dict:
        """发送JSON-RPC请求并等待响应
        
        Args:
            method: 方法名
            params: 参数
            timeout: 超时秒数
        
        Returns:
            响应的result字段
        
        Raises:
            MCPError: 通信或协议错误
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise MCPError(-1, "进程未启动")
        
        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        
        request_line = json.dumps(request, ensure_ascii=False) + "\n"
        
        try:
            self._process.stdin.write(request_line.encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._connected = False
            raise MCPError(-1, f"写入失败: {e}")
        
        # 读取响应（带超时）
        deadline = time.monotonic() + timeout
        
        while time.monotonic() < deadline:
            try:
                line = self._process.stdout.readline()
            except Exception as e:
                raise MCPError(-1, f"读取失败: {e}")
            
            if not line:
                # 进程退出了
                exit_code = self._process.poll()
                stderr_out = ""
                try:
                    stderr_out = self._process.stderr.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                self._connected = False
                raise MCPError(-1, f"MCP Server退出(code={exit_code}): {stderr_out}")
            
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                # 可能是Server的日志输出，跳过
                continue
            
            # 跳过通知（没有id的消息）
            if "id" not in response:
                continue
            
            # 检查是否是我们的响应
            if response.get("id") != req_id:
                continue
            
            # 错误响应
            if "error" in response:
                err = response["error"]
                raise MCPError(
                    code=err.get("code", -1),
                    message=err.get("message", "Unknown error"),
                    data=err.get("data"),
                )
            
            return response.get("result", {})
        
        raise MCPError(-1, f"请求超时({timeout}s): {method}")
    
    def _notify(self, method: str, params: Dict):
        """发送JSON-RPC通知（不需要响应）"""
        if not self._process or not self._process.stdin:
            return
        
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        
        line = json.dumps(notification, ensure_ascii=False) + "\n"
        
        try:
            self._process.stdin.write(line.encode("utf-8"))
            self._process.stdin.flush()
        except Exception:
            pass
    
    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass
    
    def __repr__(self):
        status = "🟢" if self.connected else "🔴"
        return f"<MCPClient {status} '{self.name}'>"


class MCPManager:
    """多MCP Server管理器
    
    统一管理多个MCP Server连接。
    从config.toml的[mcp]段自动创建连接。
    """
    
    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
    
    # ============================================================
    # 配置
    # ============================================================
    
    def from_config(self, config: Dict) -> List[str]:
        """从配置创建MCP连接
        
        支持两种配置格式：
        
        简写（字符串）：
            [mcp]
            filesystem = "npx -y @modelcontextprotocol/server-filesystem /home"
        
        详细（dict）：
            [mcp]
            github = {command = "npx", args = ["-y", "@modelcontextprotocol/server-github"]}
        
        Args:
            config: [mcp]段配置
        
        Returns:
            成功连接的Server名列表
        """
        connected = []
        
        for name, value in config.items():
            if isinstance(value, str):
                # 简写：整个命令字符串
                parts = value.split()
                if not parts:
                    continue
                command = parts[0]
                args = parts[1:]
            elif isinstance(value, dict):
                command = value.get("command", "")
                args = value.get("args", [])
                if not command:
                    continue
            else:
                continue
            
            client = MCPClient(name)
            try:
                client.connect_stdio(command, args)
                self._clients[name] = client
                connected.append(name)
            except MCPError:
                # 连接失败，记录但不阻塞
                pass
        
        return connected
    
    def add(self, name: str, command: str, args: Optional[List[str]] = None,
            env: Optional[Dict[str, str]] = None) -> bool:
        """手动添加MCP Server
        
        Args:
            name: Server名称
            command: 命令
            args: 参数
            env: 环境变量
        
        Returns:
            是否成功
        """
        client = MCPClient(name)
        try:
            client.connect_stdio(command, args, env=env)
            self._clients[name] = client
            return True
        except MCPError:
            return False
    
    # ============================================================
    # 工具操作
    # ============================================================
    
    def get_all_tools(self) -> List[MCPToolInfo]:
        """列出所有MCP Server的所有工具"""
        tools = []
        for client in self._clients.values():
            if client.connected:
                try:
                    tools.extend(client.list_tools())
                except MCPError:
                    pass
        return tools
    
    def get_tools(self, server_name: str) -> List[MCPToolInfo]:
        """列出指定Server的工具"""
        client = self._clients.get(server_name)
        if not client or not client.connected:
            return []
        try:
            return client.list_tools()
        except MCPError:
            return []
    
    def call_tool(self, server_name: str, tool_name: str,
                  params: Optional[Dict] = None, timeout: float = 60.0) -> Any:
        """调用指定Server的工具
        
        Args:
            server_name: Server名称
            tool_name: 工具名
            params: 参数
            timeout: 超时
        
        Returns:
            工具执行结果
        """
        client = self._clients.get(server_name)
        if not client:
            raise MCPError(-1, f"MCP Server不存在: {server_name}")
        if not client.connected:
            raise MCPError(-1, f"MCP Server未连接: {server_name}")
        
        return client.call_tool(tool_name, params, timeout=timeout)
    
    def find_and_call(self, tool_name: str, params: Optional[Dict] = None) -> Any:
        """自动查找并调用工具（在所有Server中搜索）
        
        Args:
            tool_name: 工具名
            params: 参数
        
        Returns:
            工具执行结果
        """
        for client in self._clients.values():
            if not client.connected:
                continue
            try:
                tools = client.list_tools()
                for t in tools:
                    if t.name == tool_name:
                        return client.call_tool(tool_name, params)
            except MCPError:
                continue
        
        raise MCPError(-1, f"工具未找到: {tool_name}")
    
    # ============================================================
    # 管理
    # ============================================================
    
    def get_client(self, name: str) -> Optional[MCPClient]:
        """获取指定的MCP客户端"""
        return self._clients.get(name)
    
    def list_servers(self) -> List[Dict]:
        """列出所有Server状态"""
        return [
            {
                "name": name,
                "connected": client.connected,
                "tools": len(client.list_tools()) if client.connected else 0,
            }
            for name, client in self._clients.items()
        ]
    
    def disconnect(self, name: str):
        """断开指定Server"""
        client = self._clients.pop(name, None)
        if client:
            client.disconnect()
    
    def disconnect_all(self):
        """断开所有Server"""
        for client in self._clients.values():
            client.disconnect()
        self._clients.clear()
    
    def reconnect(self, name: str) -> bool:
        """重连指定Server（需要先disconnect再重新from_config）"""
        client = self._clients.get(name)
        if not client:
            return False
        # 简单实现：先断开，但无法重连（缺少原始命令信息）
        # 完整实现需要保存原始配置
        return False
    
    def summary(self) -> Dict:
        """概览"""
        total = len(self._clients)
        connected = sum(1 for c in self._clients.values() if c.connected)
        total_tools = 0
        for c in self._clients.values():
            if c.connected:
                try:
                    total_tools += len(c.list_tools())
                except MCPError:
                    pass
        
        return {
            "total_servers": total,
            "connected": connected,
            "total_tools": total_tools,
            "servers": self.list_servers(),
        }
    
    def __del__(self):
        try:
            self.disconnect_all()
        except Exception:
            pass
    
    def __repr__(self):
        total = len(self._clients)
        connected = sum(1 for c in self._clients.values() if c.connected)
        return f"<MCPManager {connected}/{total} servers>"
