"""
xuanji 进程隔离模块

每个Agent运行在独立Python进程中：
- 工作目录隔离（自动创建）
- 心跳监控：30秒WARNING / 120秒重启
- 崩溃自动重启（三枪规则：最多3次）
- PID管理与僵尸进程清理
- 通过stdin/stdout JSON-line协议通信

零外部依赖，纯标准库实现。
"""

import asyncio
import importlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("xuanji.isolation")


# ============================================================
# 状态枚举
# ============================================================

class AgentStatus(IntEnum):
    """Agent进程状态"""
    INIT = 0         # 初始化中
    RUNNING = 1      # 正常运行
    WARNING = 2      # 心跳超时警告
    RESTARTING = 3   # 重启中
    DEAD = 4         # 已死亡（超过重启次数）
    STOPPED = 5      # 正常停止


# ============================================================
# Agent进程信息
# ============================================================

@dataclass
class AgentInfo:
    """Agent进程元数据"""
    name: str
    agent_id: int
    plugin_module: str       # 模块路径 e.g. "agents.my_agent"
    plugin_class: str        # 类名 e.g. "MyAgent"
    workdir: str
    config: Dict = field(default_factory=dict)
    status: AgentStatus = AgentStatus.INIT
    pid: int = 0
    restart_count: int = 0
    max_restarts: int = 3    # 三枪规则
    last_heartbeat: float = 0.0
    start_time: float = 0.0
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stderr_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# ============================================================
# Agent进程管理器
# ============================================================

class AgentProcess:
    """Agent进程管理器 — 管理所有Agent子进程的生命周期

    职责：
    - 启动Agent为独立Python进程
    - 心跳监控与健康检查
    - 崩溃自动重启（三枪规则）
    - 优雅关闭
    - 僵尸进程清理
    - PID追踪

    通信协议（stdin/stdout JSON行）：
    - 父→子: {"type": "init|task|message|shutdown", ...}
    - 子→父: {"type": "ready|heartbeat|result|message|error|log|stopped", ...}
    """

    # 心跳阈值（秒）
    HEARTBEAT_WARN_SEC = 30       # 30秒没心跳 → WARNING
    HEARTBEAT_RESTART_SEC = 120   # 120秒没心跳 → 重启

    def __init__(self, base_workdir: str = ".xuanji_work",
                 on_message: Optional[Callable] = None):
        """
        Args:
            base_workdir: Agent工作目录基础路径（每个Agent在其下有独立子目录）
            on_message: 收到Agent消息的回调 fn(agent_name: str, msg: dict)
        """
        self.base_workdir = os.path.abspath(base_workdir)
        self.on_message = on_message
        self._agents: Dict[str, AgentInfo] = {}   # name → AgentInfo
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    # ============================================================
    # 启动 / 停止 Agent
    # ============================================================

    def start_agent(self, name: str, agent_id: int,
                    plugin_module: str, plugin_class: str,
                    config: Optional[Dict] = None) -> AgentInfo:
        """启动一个Agent为独立子进程

        Args:
            name: Agent名称（唯一标识）
            agent_id: Agent ID（用于消息总线寻址）
            plugin_module: 插件模块路径 e.g. "agents.my_agent"
            plugin_class: 插件类名 e.g. "MyAgent"
            config: 传递给Agent的配置字典

        Returns:
            AgentInfo 进程元数据
        """
        # 创建隔离工作目录
        workdir = os.path.join(self.base_workdir, name)
        os.makedirs(workdir, exist_ok=True)

        info = AgentInfo(
            name=name,
            agent_id=agent_id,
            plugin_module=plugin_module,
            plugin_class=plugin_class,
            workdir=workdir,
            config=config or {},
        )

        with self._lock:
            # 如果同名Agent已存在且仍在运行，先停掉
            old = self._agents.get(name)
            if old and old.process and old.process.poll() is None:
                logger.warning(f"Agent [{name}] 已存在，先停止旧进程")
                self._kill_process(old)
            self._agents[name] = info

        self._launch_process(info)
        return info

    def _launch_process(self, info: AgentInfo) -> bool:
        """内部：启动Agent子进程"""
        try:
            # 构造初始化数据
            init_data = {
                "type": "init",
                "agent_id": info.agent_id,
                "name": info.name,
                "plugin_module": info.plugin_module,
                "plugin_class": info.plugin_class,
                "config": info.config,
                "workdir": info.workdir,
            }

            # 启动子进程：python -m xuanji.isolation --worker
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                [sys.executable, "-m", "xuanji.isolation", "--worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=info.workdir,
                creationflags=creation_flags,
            )

            info.process = proc
            info.pid = proc.pid
            info.status = AgentStatus.RUNNING
            info.last_heartbeat = time.monotonic()
            info.start_time = time.time()

            # 发送初始化命令
            self._send_to_agent(info, init_data)

            # 启动stdout读取线程
            reader = threading.Thread(
                target=self._reader_loop,
                args=(info,),
                daemon=True,
                name=f"reader-{info.name}",
            )
            reader.start()
            info._reader_thread = reader

            # 启动stderr读取线程（日志转发）
            stderr_reader = threading.Thread(
                target=self._stderr_loop,
                args=(info,),
                daemon=True,
                name=f"stderr-{info.name}",
            )
            stderr_reader.start()
            info._stderr_thread = stderr_reader

            logger.info(f"Agent [{info.name}] 已启动 PID={info.pid}")
            return True

        except Exception as e:
            logger.error(f"Agent [{info.name}] 启动失败: {e}")
            info.status = AgentStatus.DEAD
            return False

    def stop_agent(self, name: str, timeout: float = 10.0) -> bool:
        """优雅停止一个Agent

        先发送shutdown命令等待进程自行退出，
        超时后强制终止。

        Args:
            name: Agent名称
            timeout: 等待超时（秒）

        Returns:
            是否成功停止
        """
        info = self._agents.get(name)
        if not info or not info.process:
            return True

        # 发送优雅停止信号
        self._send_to_agent(info, {"type": "shutdown"})

        # 等待进程退出
        try:
            info.process.wait(timeout=timeout)
            info.status = AgentStatus.STOPPED
            logger.info(f"Agent [{name}] 已优雅停止")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Agent [{name}] 优雅停止超时，强制终止")
            self._kill_process(info)
            info.status = AgentStatus.STOPPED
            return True

    def stop_all(self, timeout: float = 15.0):
        """停止所有Agent

        Args:
            timeout: 总超时（秒），所有Agent共享这个时间窗口
        """
        self._running = False

        # 第一轮：发送停止信号
        for name, info in self._agents.items():
            if info.status in (AgentStatus.RUNNING, AgentStatus.WARNING):
                self._send_to_agent(info, {"type": "shutdown"})

        # 第二轮：等待退出
        deadline = time.monotonic() + timeout
        for name, info in self._agents.items():
            if info.process and info.process.poll() is None:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    info.process.wait(timeout=remaining)
                    info.status = AgentStatus.STOPPED
                except subprocess.TimeoutExpired:
                    self._kill_process(info)
                    info.status = AgentStatus.STOPPED

        logger.info("所有Agent已停止")

    # ============================================================
    # 通信
    # ============================================================

    def _send_to_agent(self, info: AgentInfo, data: dict) -> bool:
        """向Agent子进程发送JSON行消息"""
        try:
            if info.process and info.process.stdin:
                line = json.dumps(data, ensure_ascii=False) + "\n"
                info.process.stdin.write(line.encode("utf-8"))
                info.process.stdin.flush()
                return True
        except (BrokenPipeError, OSError) as e:
            logger.warning(f"Agent [{info.name}] 管道写入失败: {e}")
        return False

    def send_task(self, agent_name: str, task: Dict) -> bool:
        """向Agent发送任务

        Args:
            agent_name: 目标Agent名称
            task: 任务数据字典

        Returns:
            是否成功发送
        """
        info = self._agents.get(agent_name)
        if not info or info.status not in (AgentStatus.RUNNING, AgentStatus.WARNING):
            return False
        return self._send_to_agent(info, {"type": "task", "data": task})

    def send_message(self, agent_name: str, msg: Dict) -> bool:
        """向Agent发送消息

        Args:
            agent_name: 目标Agent名称
            msg: 消息数据字典

        Returns:
            是否成功发送
        """
        info = self._agents.get(agent_name)
        if not info or info.status not in (AgentStatus.RUNNING, AgentStatus.WARNING):
            return False
        return self._send_to_agent(info, {"type": "message", "data": msg})

    def _reader_loop(self, info: AgentInfo):
        """读取Agent stdout输出（独立线程）

        解析JSON行协议，处理心跳和消息回调。
        """
        proc = info.process
        if not proc or not proc.stdout:
            return

        try:
            for raw_line in iter(proc.stdout.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # 非JSON输出当日志处理
                    logger.debug(f"Agent [{info.name}] stdout: {line[:200]}")
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "heartbeat":
                    with info._lock:
                        info.last_heartbeat = time.monotonic()
                        if info.status == AgentStatus.WARNING:
                            info.status = AgentStatus.RUNNING
                            logger.info(f"Agent [{info.name}] 心跳恢复")

                elif msg_type == "ready":
                    logger.info(
                        f"Agent [{info.name}] 就绪 "
                        f"(PID={info.pid})"
                    )

                elif msg_type == "stopped":
                    logger.info(f"Agent [{info.name}] 已确认停止")

                else:
                    # result / message / error / log / 自定义
                    if self.on_message:
                        try:
                            self.on_message(info.name, msg)
                        except Exception as e:
                            logger.error(
                                f"Agent [{info.name}] 消息回调异常: {e}"
                            )

        except Exception as e:
            logger.debug(f"Agent [{info.name}] 读取线程退出: {e}")

    def _stderr_loop(self, info: AgentInfo):
        """读取Agent stderr输出并转发为日志"""
        proc = info.process
        if not proc or not proc.stderr:
            return
        try:
            for raw_line in iter(proc.stderr.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    logger.warning(f"Agent [{info.name}] stderr: {line[:500]}")
        except Exception:
            pass

    # ============================================================
    # 健康检查 / 心跳监控
    # ============================================================

    def check_health(self) -> Dict[str, Dict]:
        """检查所有Agent健康状态

        Returns:
            {agent_name: {"status": ..., "pid": ..., "heartbeat_age": ..., ...}}
        """
        now = time.monotonic()
        results = {}

        with self._lock:
            for name, info in self._agents.items():
                # 已停止/已死亡的Agent
                if info.status in (AgentStatus.DEAD, AgentStatus.STOPPED):
                    results[name] = {
                        "status": info.status.name,
                        "pid": info.pid,
                        "restart_count": info.restart_count,
                    }
                    continue

                # 检查进程是否意外退出
                if info.process and info.process.poll() is not None:
                    exit_code = info.process.returncode
                    logger.warning(
                        f"Agent [{name}] 进程已退出 exit_code={exit_code}"
                    )
                    self._handle_crash(info)
                    results[name] = {
                        "status": info.status.name,
                        "pid": info.pid,
                        "restart_count": info.restart_count,
                        "exit_code": exit_code,
                    }
                    continue

                # 心跳年龄检查
                elapsed = now - info.last_heartbeat

                if elapsed > self.HEARTBEAT_RESTART_SEC:
                    logger.error(
                        f"Agent [{name}] 心跳超时 {elapsed:.0f}s，触发重启"
                    )
                    self._handle_crash(info)
                elif elapsed > self.HEARTBEAT_WARN_SEC:
                    if info.status != AgentStatus.WARNING:
                        logger.warning(
                            f"Agent [{name}] 心跳超时 {elapsed:.0f}s"
                        )
                        info.status = AgentStatus.WARNING

                results[name] = {
                    "status": info.status.name,
                    "pid": info.pid,
                    "heartbeat_age": round(elapsed, 1),
                    "restart_count": info.restart_count,
                }

        return results

    def _handle_crash(self, info: AgentInfo):
        """处理Agent崩溃 — 三枪规则

        崩溃次数 ≤ max_restarts → 自动重启
        崩溃次数 > max_restarts → 宣告死亡，不再重启
        """
        # 清理旧进程
        self._kill_process(info)

        info.restart_count += 1

        if info.restart_count > info.max_restarts:
            # 三枪已毕
            info.status = AgentStatus.DEAD
            logger.error(
                f"Agent [{info.name}] 已崩溃 {info.restart_count} 次，"
                f"超过上限 {info.max_restarts}，停止重启"
            )
            return

        # 自动重启
        info.status = AgentStatus.RESTARTING
        logger.info(
            f"Agent [{info.name}] 正在重启 "
            f"({info.restart_count}/{info.max_restarts})"
        )

        # 短暂延迟避免重启风暴
        time.sleep(min(info.restart_count * 2, 10))

        self._launch_process(info)

    def start_monitor(self, interval: float = 10.0):
        """启动心跳监控线程

        Args:
            interval: 检查间隔（秒）
        """
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="agent-monitor",
        )
        self._monitor_thread.start()

    def _monitor_loop(self, interval: float):
        """心跳监控主循环"""
        while self._running:
            try:
                self.check_health()
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
            time.sleep(interval)

    # ============================================================
    # 进程管理
    # ============================================================

    def _kill_process(self, info: AgentInfo):
        """强制终止进程"""
        proc = info.process
        if not proc:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        finally:
            info.process = None

    def cleanup_zombies(self):
        """清理僵尸进程（已退出但未被回收的进程句柄）"""
        with self._lock:
            for name, info in self._agents.items():
                if info.process and info.process.poll() is not None:
                    info.process = None
                    if info.status not in (
                        AgentStatus.DEAD, AgentStatus.STOPPED
                    ):
                        info.status = AgentStatus.DEAD
                        logger.info(f"清理僵尸进程: Agent [{name}]")

    def get_pids(self) -> Dict[str, int]:
        """获取所有Agent的PID映射

        Returns:
            {agent_name: pid}
        """
        return {
            name: info.pid
            for name, info in self._agents.items()
            if info.pid > 0
        }

    # ============================================================
    # 状态查询
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        """获取所有Agent状态摘要"""
        summary = {
            "total": len(self._agents),
            "running": 0,
            "warning": 0,
            "dead": 0,
            "stopped": 0,
            "agents": {},
        }

        for name, info in self._agents.items():
            if info.status == AgentStatus.RUNNING:
                summary["running"] += 1
            elif info.status == AgentStatus.WARNING:
                summary["warning"] += 1
            elif info.status == AgentStatus.DEAD:
                summary["dead"] += 1
            elif info.status == AgentStatus.STOPPED:
                summary["stopped"] += 1

            summary["agents"][name] = {
                "status": info.status.name,
                "pid": info.pid,
                "agent_id": info.agent_id,
                "restart_count": info.restart_count,
                "workdir": info.workdir,
                "uptime": round(time.time() - info.start_time, 1)
                if info.start_time > 0 else 0,
            }

        return summary

    def get_agent_info(self, name: str) -> Optional[AgentInfo]:
        """获取单个Agent信息"""
        return self._agents.get(name)

    def list_agents(self) -> List[str]:
        """列出所有Agent名称"""
        return list(self._agents.keys())


# ============================================================
# Worker进程入口点
# ============================================================

def _worker_main():
    """Agent Worker 进程入口

    独立运行在子进程中，通过 stdin/stdout JSON-line 协议
    与父进程（Runtime）通信。

    协议：
      输入 (stdin):  {"type": "init|task|message|shutdown", ...}
      输出 (stdout): {"type": "ready|heartbeat|result|message|error|log|stopped", ...}

    心跳：每10秒自动发送一次 {"type": "heartbeat"}
    """
    agent = None
    agent_id = 0
    running = True

    def send(msg: dict):
        """向父进程发送JSON行消息"""
        try:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            sys.stdout.write(line)
            sys.stdout.flush()
        except Exception:
            pass

    def heartbeat_loop():
        """心跳发送线程 — 每10秒一次"""
        nonlocal running
        while running:
            send({"type": "heartbeat", "agent_id": agent_id, "ts": time.time()})
            time.sleep(10)

    # 心跳线程（init成功后启动）
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)

    try:
        # 从stdin逐行读取命令
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                send({"type": "error", "msg": f"无效JSON: {line[:100]}"})
                continue

            cmd_type = cmd.get("type", "")

            # ── init: 加载Agent插件 ──────────────
            if cmd_type == "init":
                agent_id = cmd.get("agent_id", 0)
                name = cmd.get("name", "unknown")
                module_path = cmd.get("plugin_module", "")
                class_name = cmd.get("plugin_class", "")
                config = cmd.get("config", {})
                workdir = cmd.get("workdir", ".")

                # 切换到隔离工作目录
                try:
                    os.makedirs(workdir, exist_ok=True)
                    os.chdir(workdir)
                except Exception:
                    pass

                try:
                    # 动态加载Agent插件模块
                    mod = importlib.import_module(module_path)
                    cls = getattr(mod, class_name)
                    agent = cls()
                    agent.on_load(config)

                    # 启动心跳
                    hb_thread.start()

                    send({
                        "type": "ready",
                        "agent_id": agent_id,
                        "name": name,
                        "pid": os.getpid(),
                    })

                except Exception as e:
                    send({
                        "type": "error",
                        "msg": f"加载Agent失败: {e}",
                        "agent_id": agent_id,
                    })
                    running = False
                    break

            # ── task: 执行任务 ──────────────
            elif cmd_type == "task":
                if not agent:
                    send({"type": "error", "msg": "Agent未初始化"})
                    continue
                try:
                    task_data = cmd.get("data", {})
                    result = asyncio.run(agent.on_task(task_data, {}))
                    send({
                        "type": "result",
                        "agent_id": agent_id,
                        "data": result,
                    })
                except Exception as e:
                    send({
                        "type": "error",
                        "agent_id": agent_id,
                        "msg": f"任务执行失败: {e}",
                    })

            # ── message: 处理消息 ──────────────
            elif cmd_type == "message":
                if not agent:
                    send({"type": "error", "msg": "Agent未初始化"})
                    continue
                try:
                    msg_data = cmd.get("data", {})
                    reply = asyncio.run(agent.on_message(msg_data, {}))
                    send({
                        "type": "message",
                        "agent_id": agent_id,
                        "data": reply,
                    })
                except Exception as e:
                    send({
                        "type": "error",
                        "agent_id": agent_id,
                        "msg": f"消息处理失败: {e}",
                    })

            # ── shutdown: 优雅关闭 ──────────────
            elif cmd_type == "shutdown":
                if agent:
                    try:
                        asyncio.run(agent.on_stop())
                    except Exception:
                        pass
                running = False
                send({"type": "stopped", "agent_id": agent_id})
                break

    except Exception as e:
        try:
            send({"type": "error", "msg": f"Worker异常退出: {e}"})
        except Exception:
            pass
    finally:
        running = False


# ============================================================
# 模块入口：python -m xuanji.isolation --worker
# ============================================================

# ============================================================
# 模块入口：python -m xuanji.isolation --worker
# 仅在直接运行本模块时触发，import时不执行
# ============================================================

if __name__ == "__main__":
    if "--worker" in sys.argv:
        _worker_main()
        sys.exit(0)
