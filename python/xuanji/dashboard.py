"""
xuanji 健康面板（Web UI）

提供HTTP服务器和Web界面，用于监控Agent状态、指标和日志。
单文件实现，HTML/CSS/JS内联在Python字符串中。
零外部依赖，仅使用标准库。
"""

import json
import threading
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from collections import deque

logger = logging.getLogger(__name__)


# ─── HTML模板 ───────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>xuanji Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f172a; color: #e2e8f0; min-height: 100vh; }
.header { background: linear-gradient(135deg, #1e293b, #334155);
           padding: 20px 30px; border-bottom: 1px solid #475569;
           display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 24px; color: #38bdf8; }
.header .status { display: flex; align-items: center; gap: 8px; }
.header .dot { width: 10px; height: 10px; border-radius: 50%;
               background: #22c55e; animation: pulse 2s infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 20px; margin-bottom: 20px; }
.card { background: #1e293b; border-radius: 12px; padding: 20px;
        border: 1px solid #334155; transition: border-color 0.3s; }
.card:hover { border-color: #38bdf8; }
.card h2 { font-size: 14px; color: #94a3b8; text-transform: uppercase;
           letter-spacing: 1px; margin-bottom: 12px; }
.card .value { font-size: 36px; font-weight: 700; color: #f1f5f9; }
.card .sub { font-size: 13px; color: #64748b; margin-top: 4px; }
.agents-table { width: 100%; border-collapse: collapse; }
.agents-table th, .agents-table td { padding: 10px 14px; text-align: left;
    border-bottom: 1px solid #334155; font-size: 14px; }
.agents-table th { color: #94a3b8; font-weight: 600; }
.badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.badge-running { background: #064e3b; color: #34d399; }
.badge-idle { background: #1e293b; color: #94a3b8; }
.badge-error { background: #450a0a; color: #f87171; }
.logs-box { background: #0f172a; border-radius: 8px; padding: 12px;
            max-height: 400px; overflow-y: auto; font-family: 'Cascadia Code', monospace;
            font-size: 13px; line-height: 1.6; }
.log-line { padding: 2px 0; }
.log-info { color: #38bdf8; }
.log-warn { color: #fbbf24; }
.log-error { color: #f87171; }
.log-debug { color: #64748b; }
.refresh-info { text-align: center; color: #475569; font-size: 12px; padding: 16px; }
</style>
</head>
<body>
<div class="header">
  <h1>🔥 xuanji Dashboard</h1>
  <div class="status"><div class="dot"></div><span id="uptime">启动中...</span></div>
</div>
<div class="container">
  <div class="grid">
    <div class="card"><h2>运行中的Agent</h2>
      <div class="value" id="agent-count">-</div>
      <div class="sub" id="agent-sub"></div></div>
    <div class="card"><h2>总请求数</h2>
      <div class="value" id="total-requests">-</div>
      <div class="sub" id="req-sub"></div></div>
    <div class="card"><h2>平均响应时间</h2>
      <div class="value" id="avg-latency">-</div>
      <div class="sub" id="latency-sub"></div></div>
    <div class="card"><h2>错误率</h2>
      <div class="value" id="error-rate">-</div>
      <div class="sub" id="error-sub"></div></div>
  </div>
  <div class="card" style="margin-bottom:20px;">
    <h2>Agent 列表</h2>
    <table class="agents-table">
      <thead><tr><th>名称</th><th>状态</th><th>请求数</th><th>最近活跃</th></tr></thead>
      <tbody id="agents-body"><tr><td colspan="4" style="color:#64748b">加载中...</td></tr></tbody>
    </table>
  </div>
  <div class="card">
    <h2>最近日志</h2>
    <div class="logs-box" id="logs-box">加载中...</div>
  </div>
  <div class="refresh-info">每 5 秒自动刷新</div>
</div>
<script>
function fmt(ts) {
  if (!ts) return '-';
  var d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN');
}
function badge(s) {
  var cls = {'running':'badge-running','idle':'badge-idle','error':'badge-error'}[s] || 'badge-idle';
  return '<span class="badge '+cls+'">'+s+'</span>';
}
function refresh() {
  fetch('/api/status').then(r=>r.json()).then(d=>{
    document.getElementById('uptime').textContent = '运行 ' + d.uptime_str;
    document.getElementById('total-requests').textContent = d.total_requests || 0;
    document.getElementById('avg-latency').textContent = (d.avg_latency_ms||0).toFixed(1)+'ms';
    document.getElementById('error-rate').textContent = (d.error_rate||0).toFixed(1)+'%';
    document.getElementById('agent-count').textContent = d.agent_count || 0;
  }).catch(()=>{});
  fetch('/api/agents').then(r=>r.json()).then(agents=>{
    var tb = document.getElementById('agents-body');
    if(!agents.length){ tb.innerHTML='<tr><td colspan="4" style="color:#64748b">暂无Agent</td></tr>'; return; }
    tb.innerHTML = agents.map(a =>
      '<tr><td>'+a.name+'</td><td>'+badge(a.status)+'</td><td>'+
      (a.requests||0)+'</td><td>'+fmt(a.last_active)+'</td></tr>'
    ).join('');
  }).catch(()=>{});
  fetch('/api/logs').then(r=>r.json()).then(logs=>{
    var box = document.getElementById('logs-box');
    if(!logs.length){ box.innerHTML='<div style="color:#64748b">暂无日志</div>'; return; }
    box.innerHTML = logs.map(l=>{
      var cls = {'INFO':'log-info','WARN':'log-warn','WARNING':'log-warn',
                 'ERROR':'log-error','DEBUG':'log-debug'}[l.level]||'log-info';
      return '<div class="log-line '+cls+'">['+fmt(l.time)+'] ['+l.level+'] '+l.message+'</div>';
    }).join('');
    box.scrollTop = box.scrollHeight;
  }).catch(()=>{});
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


# ─── 数据存储 ───────────────────────────────────────────────

class DashboardStore:
    """面板数据存储（内存）"""

    def __init__(self, max_logs: int = 200):
        self._start_time = time.time()
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._metrics: Dict[str, Any] = {
            "total_requests": 0,
            "total_errors": 0,
            "latencies": deque(maxlen=1000),
        }
        self._logs: deque = deque(maxlen=max_logs)
        self._lock = threading.Lock()

    # ── Agent管理 ──

    def register_agent(self, name: str, status: str = "idle", **extra) -> None:
        with self._lock:
            self._agents[name] = {
                "name": name,
                "status": status,
                "requests": 0,
                "last_active": time.time(),
                **extra,
            }

    def update_agent(self, name: str, **fields) -> None:
        with self._lock:
            if name in self._agents:
                self._agents[name].update(fields)
                self._agents[name]["last_active"] = time.time()

    def remove_agent(self, name: str) -> None:
        with self._lock:
            self._agents.pop(name, None)

    # ── 指标 ──

    def record_request(self, agent: str = "", latency_ms: float = 0, error: bool = False) -> None:
        with self._lock:
            self._metrics["total_requests"] += 1
            if error:
                self._metrics["total_errors"] += 1
            if latency_ms > 0:
                self._metrics["latencies"].append(latency_ms)
            if agent and agent in self._agents:
                self._agents[agent]["requests"] = self._agents[agent].get("requests", 0) + 1
                self._agents[agent]["last_active"] = time.time()

    # ── 日志 ──

    def add_log(self, level: str, message: str) -> None:
        with self._lock:
            self._logs.append({
                "time": time.time(),
                "level": level.upper(),
                "message": message,
            })

    # ── 查询 ──

    def get_status(self) -> Dict:
        with self._lock:
            elapsed = time.time() - self._start_time
            total = self._metrics["total_requests"]
            errors = self._metrics["total_errors"]
            lats = list(self._metrics["latencies"])
            avg_lat = sum(lats) / len(lats) if lats else 0
            err_rate = (errors / total * 100) if total > 0 else 0

            hours = int(elapsed // 3600)
            mins = int((elapsed % 3600) // 60)
            secs = int(elapsed % 60)
            uptime_str = f"{hours}h {mins}m {secs}s"

            return {
                "uptime": elapsed,
                "uptime_str": uptime_str,
                "agent_count": len(self._agents),
                "total_requests": total,
                "total_errors": errors,
                "avg_latency_ms": round(avg_lat, 2),
                "error_rate": round(err_rate, 2),
                "start_time": self._start_time,
            }

    def get_agents(self) -> List[Dict]:
        with self._lock:
            return list(self._agents.values())

    def get_metrics(self) -> Dict:
        with self._lock:
            lats = list(self._metrics["latencies"])
            return {
                "total_requests": self._metrics["total_requests"],
                "total_errors": self._metrics["total_errors"],
                "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else 0,
                "p50_latency_ms": round(sorted(lats)[len(lats) // 2], 2) if lats else 0,
                "p99_latency_ms": round(sorted(lats)[int(len(lats) * 0.99)], 2) if lats else 0,
                "latency_samples": len(lats),
            }

    def get_logs(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            return list(self._logs)[-limit:]


# ─── HTTP处理器 ──────────────────────────────────────────────

class _DashboardHandler(BaseHTTPRequestHandler):
    """HTTP请求处理器"""

    store: DashboardStore = None  # 由Dashboard类设置

    def log_message(self, format, *args):
        """静默HTTP日志"""
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")

        if path == "" or path == "/":
            self._send_html(DASHBOARD_HTML)
        elif path == "/api/status":
            self._send_json(self.store.get_status())
        elif path == "/api/agents":
            self._send_json(self.store.get_agents())
        elif path == "/api/metrics":
            self._send_json(self.store.get_metrics())
        elif path == "/api/logs":
            self._send_json(self.store.get_logs())
        else:
            self._send_json({"error": "Not Found"}, 404)


# ─── Dashboard主类 ──────────────────────────────────────────

class Dashboard:
    """xuanji 健康面板
    
    提供Web UI用于监控Agent状态、指标和日志。
    
    用法::
    
        dashboard = Dashboard()
        dashboard.start(port=8899)
        
        # 注册Agent
        dashboard.register_agent("assistant", status="running")
        
        # 记录请求
        dashboard.record_request("assistant", latency_ms=123.4)
        
        # 添加日志
        dashboard.log("INFO", "Agent启动成功")
        
        # 停止
        dashboard.stop()
    """

    def __init__(self, max_logs: int = 200):
        self.store = DashboardStore(max_logs=max_logs)
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self, port: int = 8899, host: str = "0.0.0.0") -> None:
        """启动Web服务
        
        Args:
            port: 监听端口，默认8899
            host: 绑定地址，默认0.0.0.0
        """
        if self._running:
            logger.warning("Dashboard已在运行中")
            return

        # 创建handler子类并绑定store
        handler = type("Handler", (_DashboardHandler,), {"store": self.store})

        self._server = HTTPServer((host, port), handler)
        self._running = True

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="dashboard-server",
        )
        self._thread.start()

        self.store.add_log("INFO", f"Dashboard启动于 http://{host}:{port}")
        logger.info(f"Dashboard启动于 http://{host}:{port}")

    def stop(self) -> None:
        """停止Web服务"""
        if self._server and self._running:
            self._running = False
            self._server.shutdown()
            self.store.add_log("INFO", "Dashboard已停止")
            logger.info("Dashboard已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── 代理方法 ──

    def register_agent(self, name: str, status: str = "idle", **extra) -> None:
        """注册Agent到面板"""
        self.store.register_agent(name, status, **extra)

    def update_agent(self, name: str, **fields) -> None:
        """更新Agent状态"""
        self.store.update_agent(name, **fields)

    def remove_agent(self, name: str) -> None:
        """从面板移除Agent"""
        self.store.remove_agent(name)

    def record_request(self, agent: str = "", latency_ms: float = 0, error: bool = False) -> None:
        """记录一次请求"""
        self.store.record_request(agent, latency_ms, error)

    def log(self, level: str, message: str) -> None:
        """添加日志"""
        self.store.add_log(level, message)

    def get_status(self) -> Dict:
        """获取状态数据"""
        return self.store.get_status()

    def get_agents(self) -> List[Dict]:
        """获取Agent列表"""
        return self.store.get_agents()

    def get_metrics(self) -> Dict:
        """获取指标数据"""
        return self.store.get_metrics()

    def get_logs(self, limit: int = 100) -> List[Dict]:
        """获取最近日志"""
        return self.store.get_logs(limit)
