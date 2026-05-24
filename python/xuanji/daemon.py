"""
xuanji 守护进程 + HTTP API

让 xuanji 作为后台服务运行，CLI 通过 HTTP 控制：
    xuanji start          启动守护进程
    xuanji stop           停止
    xuanji status         查看状态
    xuanji agent "任务"   派发任务
    xuanji chat           交互对话
    xuanji tasks          查看任务列表
"""

import asyncio
import http.server
import io
import json
import os
import socketserver
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import logging
logger = logging.getLogger("xuanji.daemon")

# ─────────────────────────────────────────────
# PID 文件管理
# ─────────────────────────────────────────────

DAEMON_DIR = Path.home() / ".xuanji"
DAEMON_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = DAEMON_DIR / "daemon.pid"
LOG_FILE = DAEMON_DIR / "daemon.log"
TASK_DB = DAEMON_DIR / "tasks.json"


def _read_pid() -> Optional[int]:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except Exception:
            return None
    return None


def _write_pid(pid: int):
    PID_FILE.write_text(str(pid))


def _remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def _is_running(pid: int) -> bool:
    """检查进程是否在运行"""
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_INFORMATION = 0x0400
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if h == 0:
            return False
        kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────
# 任务数据库
# ─────────────────────────────────────────────

class TaskDB:
    """简单任务数据库（JSON文件）"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: Dict[str, dict] = {}
        self._next_id = 1
        self._load()
    
    def _load(self):
        if TASK_DB.exists():
            try:
                data = json.loads(TASK_DB.read_text(encoding="utf-8"))
                self._tasks = data.get("tasks", {})
                self._next_id = data.get("next_id", 1)
            except Exception:
                pass
    
    def _save(self):
        try:
            data = {"tasks": self._tasks, "next_id": self._next_id}
            TASK_DB.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"TaskDB save failed: {e}")
    
    def create(self, task: str, persona: str = "auto", config: dict = None) -> str:
        with self._lock:
            task_id = f"T{self._next_id:04d}"
            self._next_id += 1
            self._tasks[task_id] = {
                "id": task_id,
                "task": task,
                "persona": persona,
                "status": "pending",
                "result": "",
                "error": "",
                "steps": [],
                "created_at": time.time(),
                "started_at": 0,
                "finished_at": 0,
            }
            self._save()
            return task_id
    
    def update(self, task_id: str, **kwargs):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(kwargs)
                self._save()
    
    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            return self._tasks.get(task_id)
    
    def list_all(self) -> List[dict]:
        with self._lock:
            return sorted(
                self._tasks.values(),
                key=lambda t: t["created_at"],
                reverse=True,
            )
    
    def get_pending(self) -> List[dict]:
        with self._lock:
            return [t for t in self._tasks.values() if t["status"] == "pending"]


# ─────────────────────────────────────────────
# XuanJi 核心引擎（在守护进程中）
# ─────────────────────────────────────────────

class XuanJiEngine:
    """守护进程中的核心引擎"""
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or "config.toml"
        self.config = {}
        self.task_db = TaskDB()
        self._llm_adapter = None
        self._llm_model = ""
        self._running = False
        self._worker_thread = None
        self._ready = False
    
    def initialize(self):
        """加载配置，初始化LLM"""
        # 加载配置
        self.config = self._load_config()
        llm_cfg = self.config.get("llm", {})
        
        # 自动检测LLM
        self._llm_adapter, self._llm_model = self._auto_detect_llm(llm_cfg)
        
        if self._llm_adapter:
            logger.info(f"LLM ready: {self._llm_model}")
            self._ready = True
        else:
            logger.warning("No LLM backend available")
            self._ready = False
    
    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            return self._parse_toml(self.config_path)
        # 尝试常见路径
        for p in [os.path.join(os.path.dirname(__file__), "..", "..", "config.toml"),
                  os.path.join(os.getcwd(), "config.toml")]:
            if os.path.exists(p):
                return self._parse_toml(p)
        return {}
    
    def _parse_toml(self, path: str) -> dict:
        try:
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            pass
        # 简单解析
        config = {}
        current = config
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    parts = section.split(".")
                    current = config
                    for part in parts:
                        current = current.setdefault(part, {})
                elif "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    current[key] = val
        return config
    
    def _auto_detect_llm(self, llm_cfg: dict):
        """自动检测可用的LLM"""
        import urllib.request
        
        # 1. Ollama（本地免费）
        ollama_url = "http://localhost:11434"
        if llm_cfg.get("ollama") or llm_cfg.get("ollama_config"):
            ollama_cfg = llm_cfg.get("ollama_config", {})
            if ollama_cfg:
                ollama_url = ollama_cfg.get("base_url", ollama_url)
        
        try:
            req = urllib.request.Request(f"{ollama_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    models = [m["name"] for m in data.get("models", [])]
                    if models:
                        from xuanji.llm.ollama import OllamaAdapter
                        model = self._pick_best_model(models)
                        adapter = OllamaAdapter("ollama", {
                            "base_url": ollama_url,
                            "model": model,
                        })
                        adapter._models = models
                        return adapter, model
        except Exception:
            pass
        
        # 2. 云API
        for key, (adapter_cls_name, default_model) in [
            ("DEEPSEEK_API_KEY", ("deepseek", "deepseek-chat")),
            ("DASHSCOPE_API_KEY", ("dashscope", "qwen-turbo")),
            ("ZHIPU_API_KEY", ("zhipu", "glm-4-flash")),
        ]:
            # 先查环境变量
            api_key = os.environ.get(key, "")
            # 再查config
            cfg_key = key.replace("_API_KEY", "").lower()
            if cfg_key == "dashscope":
                cfg_key = "dashscope"
            elif cfg_key == "zhipu":
                cfg_key = "zhipu"
            elif cfg_key == "deepseek":
                cfg_key = "deepseek"
            
            if not api_key:
                api_key = llm_cfg.get(cfg_key, "")
            
            if api_key and api_key not in ("", "sk-xxx", "token_here"):
                try:
                    from xuanji.llm import create_llm_from_config
                    adapter = create_llm_from_config({cfg_key: api_key, "default_model": default_model})
                    return adapter, default_model
                except Exception:
                    pass
        
        return None, ""
    
    @staticmethod
    def _pick_best_model(models: List[str]) -> str:
        prefs = ["qwen3.6", "qwen3.5", "qwen2.5", "gemma2", "gemma", "llama3", "llama"]
        for pref in prefs:
            for m in models:
                if pref in m.lower():
                    return m
        return models[0]
    
    async def _run_task(self, task_id: str):
        """执行单个任务"""
        task = self.task_db.get(task_id)
        if not task or task["status"] != "pending":
            return
        
        self.task_db.update(task_id, status="running", started_at=time.time())
        logger.info(f"[Task {task_id}] Running: {task['task'][:80]}")
        
        if not self._llm_adapter:
            self.task_db.update(
                task_id, status="error",
                error="No LLM backend available. Install Ollama or configure API key.",
                finished_at=time.time(),
            )
            return
        
        start = time.time()
        try:
            from xuanji.agent_runner import AgentRunner
            
            # 创建AgentRunner
            runner = AgentRunner(self._llm_adapter, model=self._llm_model, max_steps=20)
            
            # 执行任务
            result = await runner.run(task["task"])
            
            elapsed = time.time() - start
            steps_data = []
            for s in result.steps:
                steps_data.append({
                    "step": s.step_num,
                    "thought": s.thought[:200] if s.thought else "",
                    "action": s.action,
                    "observation": s.observation[:300] if s.observation else "",
                })
            
            self.task_db.update(
                task_id,
                status="done",
                result=result.answer,
                steps=steps_data,
                finished_at=time.time(),
            )
            logger.info(f"[Task {task_id}] Done in {elapsed:.1f}s")
            
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[Task {task_id}] Error: {e}")
            self.task_db.update(
                task_id,
                status="error",
                error=f"{type(e).__name__}: {str(e)}",
                finished_at=time.time(),
            )
    
    def _worker_loop(self):
        """后台任务执行循环"""
        while self._running:
            pending = self.task_db.get_pending()
            for task in pending:
                try:
                    asyncio.run(self._run_task(task["id"]))
                except Exception as e:
                    logger.error(f"Task worker error: {e}")
            time.sleep(1)
    
    def start(self):
        """启动引擎"""
        self.initialize()
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info(f"XuanJiEngine started, LLM={self._llm_model or 'none'}")
    
    def stop(self):
        """停止引擎"""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        logger.info("XuanJiEngine stopped")
    
    @property
    def is_ready(self) -> bool:
        return self._ready
    
    @property
    def llm_info(self) -> str:
        return f"{self._llm_model}" if self._llm_adapter else "none"


# ─────────────────────────────────────────────
# HTTP 服务器
# ─────────────────────────────────────────────

class DaemonHandler(http.server.BaseHTTPRequestHandler):
    """HTTP API 处理器"""
    
    # 会被DaemonServer注入
    engine: XuanJiEngine = None
    
    def log_message(self, format, *args):
        logger.debug(format % args)
    
    def _json_response(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        if path == "/api/status":
            self._handle_status()
        elif path == "/api/tasks":
            self._handle_list_tasks()
        elif path.startswith("/api/tasks/"):
            task_id = path.split("/")[-1]
            self._handle_get_task(task_id)
        elif path == "/api/personas":
            self._handle_personas()
        else:
            self._json_response(404, {"error": f"Not found: {path}"})
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json_response(400, {"error": "Invalid JSON"})
            return
        
        if path == "/api/tasks":
            self._handle_create_task(data)
        else:
            self._json_response(404, {"error": f"Not found: {path}"})
    
    def _handle_status(self):
        tasks = self.engine.task_db.list_all()
        running = [t for t in tasks if t["status"] == "running"]
        done = [t for t in tasks if t["status"] == "done"]
        errors = [t for t in tasks if t["status"] == "error"]
        
        self._json_response(200, {
            "version": "1.0.4",
            "ready": self.engine.is_ready,
            "llm": self.engine.llm_info,
            "tasks": {
                "total": len(tasks),
                "running": len(running),
                "done": len(done),
                "error": len(errors),
                "pending": len([t for t in tasks if t["status"] == "pending"]),
            },
        })
    
    def _handle_list_tasks(self):
        tasks = self.engine.task_db.list_all()
        # 只返回摘要
        summary = []
        for t in tasks[:50]:
            summary.append({
                "id": t["id"],
                "task": t["task"][:100],
                "status": t["status"],
                "persona": t["persona"],
                "created_at": t["created_at"],
            })
        self._json_response(200, {"tasks": summary})
    
    def _handle_get_task(self, task_id: str):
        task = self.engine.task_db.get(task_id)
        if not task:
            self._json_response(404, {"error": f"Task not found: {task_id}"})
            return
        self._json_response(200, {"task": task})
    
    def _handle_create_task(self, data: dict):
        task_text = data.get("task", "")
        if not task_text:
            self._json_response(400, {"error": "Missing 'task' field"})
            return
        
        persona = data.get("persona", "auto")
        task_id = self.engine.task_db.create(task_text, persona)
        self._json_response(201, {
            "task_id": task_id,
            "status": "pending",
            "message": f"Task {task_id} created",
        })
    
    def _handle_personas(self):
        try:
            from xuanji.personas import PersonaLibrary
            lib = PersonaLibrary()
            all_personas = lib.all()
            result = []
            for p in all_personas:
                result.append({
                    "id": p.id if hasattr(p, "id") else "",
                    "name_cn": getattr(p, "name_cn", ""),
                    "role": getattr(p, "role", ""),
                })
            self._json_response(200, {"personas": result, "count": len(result)})
        except Exception as e:
            self._json_response(200, {"personas": [], "error": str(e)})


class DaemonServer:
    """守护进程HTTP服务器"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 18790, config: str = None):
        self.host = host
        self.port = port
        self.config = config
        self.engine = XuanJiEngine(config)
        self.server = None
        self._thread = None
    
    def start(self):
        """启动守护进程"""
        self.engine.start()
        
        # 注入engine到handler
        DaemonHandler.engine = self.engine
        
        self.server = socketserver.ThreadingTCPServer(
            (self.host, self.port), DaemonHandler
        )
        self.server.daemon_threads = True
        
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        
        logger.info(f"Daemon listening on {self.host}:{self.port}")
        print(f"🚀 玄机守护进程启动")
        print(f"   📡 HTTP API: http://{self.host}:{self.port}")
        print(f"   🤖 LLM: {self.engine.llm_info}")
        print(f"   📊 状态: http://{self.host}:{self.port}/api/status")
        print()
    
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.engine.stop()
            logger.info("Daemon stopped")


# ─────────────────────────────────────────────
# CLI 命令
# ─────────────────────────────────────────────

API_BASE = "http://127.0.0.1:18790"


def _api_get(path: str) -> dict:
    import urllib.request
    try:
        req = urllib.request.Request(f"{API_BASE}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": f"Cannot connect to daemon: {e}"}


def _api_post(path: str, data: dict) -> dict:
    import urllib.request
    body = json.dumps(data).encode("utf-8")
    try:
        req = urllib.request.Request(f"{API_BASE}{path}", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": f"Cannot connect to daemon: {e}"}


def cmd_start(args):
    """启动守护进程"""
    # 检查是否已经在运行
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"✅ 守护进程已在运行 (PID {pid})")
        print(f"   📡 http://127.0.0.1:18790")
        status = _api_get("/api/status")
        if "error" not in status:
            print(f"   🤖 LLM: {status.get('llm', 'unknown')}")
            t = status.get("tasks", {})
            print(f"   📋 任务: {t.get('total', 0)}总 / {t.get('running', 0)}运行 / {t.get('done', 0)}完成")
        return
    
    # 清理旧PID
    _remove_pid()
    
    # 启动守护进程
    import subprocess
    daemon_script = os.path.join(os.path.dirname(__file__), "daemon.py")
    proc = subprocess.Popen(
        [sys.executable, daemon_script, "--daemon"],
        stdout=open(str(LOG_FILE), "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        cwd=os.getcwd(),
        creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
    )
    
    # 等待启动
    for i in range(10):
        time.sleep(0.5)
        try:
            _api_get("/api/status")
            _write_pid(proc.pid)
            print(f"✅ 守护进程已启动 (PID {proc.pid})")
            print(f"   📡 http://127.0.0.1:18790")
            return
        except Exception:
            pass
    
    print(f"❌ 守护进程启动超时，查看日志: {LOG_FILE}")


def cmd_stop(args):
    """停止守护进程"""
    pid = _read_pid()
    if not pid:
        print("❌ 守护进程未运行")
        return
    
    if not _is_running(pid):
        print("⚠️ 守护进程已不存在（进程已退出）")
        _remove_pid()
        return
    
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateProcess(kernel32.OpenProcess(1, False, pid), 0)
    except Exception:
        pass
    
    _remove_pid()
    print(f"✅ 守护进程已停止 (PID {pid})")


def cmd_status(args):
    """查看守护进程状态"""
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"✅ 守护进程运行中 (PID {pid})")
    else:
        print("❌ 守护进程未运行")
        print("   启动: xuanji start")
        return
    
    status = _api_get("/api/status")
    if "error" in status:
        print(f"⚠️ {status['error']}")
        return
    
    print(f"   版本: {status.get('version', '?')}")
    print(f"   LLM:  {status.get('llm', 'none')}")
    print(f"   就绪: {'✅' if status.get('ready') else '❌'}")
    
    t = status.get("tasks", {})
    print(f"   任务: {t.get('total', 0)}总 | {t.get('pending', 0)}待处理 | {t.get('running', 0)}运行 | {t.get('done', 0)}完成 | {t.get('error', 0)}失败")


def cmd_agent(args):
    """派发任务给Agent"""
    if not args:
        print("用法: xuanji agent \"任务描述\" [--persona 人格]")
        print()
        print("示例:")
        print('  xuanji agent "帮我查一下今天北京的天气"')
        print('  xuanji agent "写一个贪吃蛇游戏" --persona ai-engineer')
        print('  xuanji agent "设计一个仙侠小说大纲" --persona web-novel-writer')
        return
    
    # 解析参数
    task_text = []
    persona = "auto"
    i = 0
    while i < len(args):
        if args[i] == "--persona" and i + 1 < len(args):
            persona = args[i + 1]
            i += 2
        else:
            task_text.append(args[i])
            i += 1
    
    task_text = " ".join(task_text)
    
    # 检查守护进程
    status = _api_get("/api/status")
    if "error" in status:
        print(f"❌ 守护进程未运行")
        print("   先启动: xuanji start")
        return
    
    if not status.get("ready"):
        print(f"❌ 守护进程未就绪，LLM={status.get('llm', 'none')}")
        print("   请检查 Ollama 或配置 API Key")
        return
    
    # 创建任务
    resp = _api_post("/api/tasks", {"task": task_text, "persona": persona})
    if "error" in resp:
        print(f"❌ {resp['error']}")
        return
    
    task_id = resp["task_id"]
    print(f"📋 任务已提交: {task_id}")
    print(f"📊 查看状态: xuanji task {task_id}")
    print()
    
    # 轮询等待完成
    import time
    print("⏳ 等待执行...")
    while True:
        time.sleep(2)
        task_resp = _api_get(f"/api/tasks/{task_id}")
        task = task_resp.get("task", {})
        s = task.get("status", "")
        
        if s == "running":
            print(".", end="", flush=True)
        elif s == "done":
            print(f"\n\n✅ 任务完成\n")
            print(task.get("result", ""))
            elapsed = task.get("finished_at", 0) - task.get("started_at", 0)
            print(f"\n⏱ 耗时: {elapsed:.1f}s")
            if task.get("steps"):
                print(f"\n📋 执行过程 ({len(task['steps'])}步):")
                for step in task["steps"]:
                    print(f"  Step {step['step']}: {step['action']}")
            return
        elif s == "error":
            print(f"\n\n❌ 任务失败")
            print(task.get("error", ""))
            return


def cmd_tasks(args):
    """查看任务列表"""
    resp = _api_get("/api/tasks")
    if "error" in resp:
        print(f"❌ {resp['error']}")
        print("   先启动: xuanji start")
        return
    
    tasks = resp.get("tasks", [])
    if not tasks:
        print("📭 暂无任务")
        return
    
    print(f"📋 任务列表 ({len(tasks)}个):\n")
    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "done": "✅",
        "error": "❌",
    }
    for t in tasks[:20]:
        icon = status_icons.get(t["status"], "?")
        print(f"  {icon} {t['id']}  [{t['status']}]  {t['task']}")


def cmd_task(args):
    """查看单个任务详情"""
    if not args:
        print("用法: xuanji task <任务ID>")
        return
    
    task_id = args[0]
    resp = _api_get(f"/api/tasks/{task_id}")
    if "error" in resp:
        print(f"❌ {resp['error']}")
        return
    
    t = resp.get("task", {})
    status_icons = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌"}
    icon = status_icons.get(t.get("status", ""), "?")
    
    print(f"{icon} {t['id']}  [{t['status']}]")
    print(f"   任务: {t['task']}")
    print(f"   人格: {t.get('persona', 'auto')}")
    
    if t.get("result"):
        print(f"\n📝 结果:\n{t['result']}")
    
    if t.get("error"):
        print(f"\n❌ 错误: {t['error']}")
    
    if t.get("steps"):
        print(f"\n📋 执行过程 ({len(t['steps'])}步):")
        for step in t["steps"]:
            print(f"  Step {step['step']}: {step['action']}")
            if step.get("thought"):
                print(f"    💭 {step['thought'][:100]}")


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

def daemon_main():
    """守护进程入口"""
    # Windows GBK fix
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    config = None
    for i, arg in enumerate(sys.argv):
        if arg == "--config" and i + 1 < len(sys.argv):
            config = sys.argv[i + 1]
    
    server = DaemonServer(config=config)
    server.start()
    _write_pid(os.getpid())
    
    # 主线程保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        _remove_pid()


if __name__ == "__main__":
    daemon_main()
