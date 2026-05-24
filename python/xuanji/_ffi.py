"""
xuanji C底座 FFI 绑定

通过ctypes加载C底座动态库，封装所有C函数为Python可调用接口。
如果C库不存在，自动降级为纯Python fallback实现。

用法:
    from xuanji._ffi import ffi
    
    # 自动选择：有C底座用C底座，没有用纯Python
    bus = ffi.bus_create(1024)
    ffi.bus_publish(bus, msg)
"""

import ctypes
import ctypes.util
import os
import platform
import struct
import sys
import threading
import time
from typing import Optional


# ============================================================
# 常量（与C头文件同步）
# ============================================================

OA_MSG_MAX_PAYLOAD = 65536

# 错误码
OA_OK = 0
OA_ERR_NOMEM = -1
OA_ERR_INVALID = -2
OA_ERR_TIMEOUT = -3
OA_ERR_FULL = -4
OA_ERR_EMPTY = -5
OA_ERR_DENIED = -6
OA_ERR_DEAD = -7
OA_ERR_EXISTS = -8
OA_ERR_NOTFOUND = -9
OA_ERR_PLATFORM = -10

# 资源类型
OA_RES_FILE = 1
OA_RES_GPU = 2
OA_RES_PORT = 3
OA_RES_SCREEN = 4
OA_RES_MOUSE = 5
OA_RES_KEYBOARD = 6
OA_RES_MIC = 7
OA_RES_SPEAKER = 8

# 健康等级
OA_HEALTH_GREEN = 0
OA_HEALTH_YELLOW = 1
OA_HEALTH_ORANGE = 2
OA_HEALTH_RED = 3

# 文件操作
OA_FS_READ = 1
OA_FS_WRITE = 2
OA_FS_DELETE = 4
OA_FS_EXEC = 8


# ============================================================
# C结构体映射
# ============================================================

class OaMsg(ctypes.Structure):
    """消息结构体 — 对应 oa_msg_t"""
    _fields_ = [
        ("from_agent", ctypes.c_uint32),
        ("to_agent", ctypes.c_uint32),
        ("msg_type", ctypes.c_uint32),
        ("priority", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint64),
        ("trace_id", ctypes.c_uint64),
        ("payload_len", ctypes.c_uint32),
        ("payload", ctypes.c_uint8 * OA_MSG_MAX_PAYLOAD),
    ]


class OaTask(ctypes.Structure):
    """任务结构体 — 对应 oa_task_t"""
    _fields_ = [
        ("task_id", ctypes.c_uint64),
        ("agent_id", ctypes.c_uint32),
        ("priority", ctypes.c_uint32),
        ("deadline", ctypes.c_uint64),
        ("payload_len", ctypes.c_uint32),
        ("payload", ctypes.c_uint8 * 4096),
    ]


class OaLease(ctypes.Structure):
    """租约结构体 — 对应 oa_lease_t"""
    _fields_ = [
        ("lease_id", ctypes.c_uint64),
        ("agent_id", ctypes.c_uint32),
        ("resource_type", ctypes.c_uint32),
        ("granted_at", ctypes.c_uint64),
        ("expires_at", ctypes.c_uint64),
        ("resource_name", ctypes.c_char * 256),
    ]


class OaHealth(ctypes.Structure):
    """健康快照 — 对应 oa_health_t"""
    _fields_ = [
        ("cpu_percent", ctypes.c_float),
        ("mem_percent", ctypes.c_float),
        ("disk_percent", ctypes.c_float),
        ("gpu_mem_percent", ctypes.c_float),
        ("agent_count", ctypes.c_uint32),
        ("agent_healthy", ctypes.c_uint32),
        ("tasks_total", ctypes.c_uint32),
        ("tasks_failed", ctypes.c_uint32),
        ("uptime_ms", ctypes.c_uint64),
    ]


# ============================================================
# 错误处理
# ============================================================

_ERROR_NAMES = {
    OA_OK: "OK",
    OA_ERR_NOMEM: "NOMEM",
    OA_ERR_INVALID: "INVALID",
    OA_ERR_TIMEOUT: "TIMEOUT",
    OA_ERR_FULL: "FULL",
    OA_ERR_EMPTY: "EMPTY",
    OA_ERR_DENIED: "DENIED",
    OA_ERR_DEAD: "DEAD",
    OA_ERR_EXISTS: "EXISTS",
    OA_ERR_NOTFOUND: "NOTFOUND",
    OA_ERR_PLATFORM: "PLATFORM",
}


class OaError(Exception):
    """xuanji C底座错误"""
    
    def __init__(self, code: int, func: str = ""):
        self.code = code
        self.func = func
        name = _ERROR_NAMES.get(code, f"UNKNOWN({code})")
        super().__init__(f"[{func}] OA_ERR_{name} ({code})")


def _check(code: int, func: str = "") -> int:
    """检查返回值，非OA_OK抛异常"""
    if code != OA_OK:
        raise OaError(code, func)
    return code


# ============================================================
# C库加载
# ============================================================

def _find_library() -> Optional[str]:
    """在多个路径中查找C底座动态库"""
    system = platform.system()
    
    if system == "Windows":
        lib_name = "xuanji.dll"
    elif system == "Darwin":
        lib_name = "libxuanji.dylib"
    else:
        lib_name = "libxuanji.so"
    
    # 搜索路径（优先级从高到低）
    search_dirs = []
    
    # 1. 环境变量指定
    env_path = os.environ.get("xuanji_LIB_PATH")
    if env_path:
        search_dirs.append(env_path)
    
    # 2. 项目build目录
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))
    search_dirs.extend([
        os.path.join(project_root, "build"),
        os.path.join(project_root, "build", "Release"),
        os.path.join(project_root, "build", "Debug"),
        os.path.join(project_root, "core", "build"),
        os.path.join(project_root, "core", "build", "Release"),
        os.path.join(project_root, "core", "build", "Debug"),
    ])
    
    # 3. 系统安装路径
    if system != "Windows":
        search_dirs.extend([
            "/usr/local/lib",
            "/usr/lib",
        ])
    
    # 4. 当前目录
    search_dirs.append(os.getcwd())
    
    for d in search_dirs:
        path = os.path.join(d, lib_name)
        if os.path.isfile(path):
            return path
    
    # 5. 系统默认搜索
    found = ctypes.util.find_library("xuanji")
    return found


def _load_library() -> Optional[ctypes.CDLL]:
    """加载C底座动态库"""
    path = _find_library()
    if not path:
        return None
    
    try:
        lib = ctypes.CDLL(path)
        _bind_functions(lib)
        return lib
    except OSError:
        return None


def _bind_functions(lib: ctypes.CDLL):
    """绑定C函数签名"""
    
    # oa_version
    lib.oa_version.restype = ctypes.c_char_p
    lib.oa_version.argtypes = []
    
    # oa_init / oa_shutdown
    lib.oa_init.restype = ctypes.c_int
    lib.oa_init.argtypes = []
    lib.oa_shutdown.restype = None
    lib.oa_shutdown.argtypes = []
    
    # --- 消息总线 ---
    lib.oa_bus_create.restype = ctypes.c_void_p
    lib.oa_bus_create.argtypes = [ctypes.c_uint32]
    
    lib.oa_bus_destroy.restype = None
    lib.oa_bus_destroy.argtypes = [ctypes.c_void_p]
    
    lib.oa_bus_publish.restype = ctypes.c_int
    lib.oa_bus_publish.argtypes = [ctypes.c_void_p, ctypes.POINTER(OaMsg)]
    
    lib.oa_bus_receive.restype = ctypes.c_int
    lib.oa_bus_receive.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(OaMsg), ctypes.c_int
    ]
    
    lib.oa_bus_pending.restype = ctypes.c_uint32
    lib.oa_bus_pending.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    
    # --- 调度器 ---
    lib.oa_sched_create.restype = ctypes.c_void_p
    lib.oa_sched_create.argtypes = [ctypes.c_uint32]
    
    lib.oa_sched_destroy.restype = None
    lib.oa_sched_destroy.argtypes = [ctypes.c_void_p]
    
    lib.oa_sched_push.restype = ctypes.c_int
    lib.oa_sched_push.argtypes = [ctypes.c_void_p, ctypes.POINTER(OaTask)]
    
    lib.oa_sched_pop.restype = ctypes.c_int
    lib.oa_sched_pop.argtypes = [ctypes.c_void_p, ctypes.POINTER(OaTask)]
    
    lib.oa_sched_size.restype = ctypes.c_uint32
    lib.oa_sched_size.argtypes = [ctypes.c_void_p]
    
    # --- 资源管理器 ---
    lib.oa_res_create.restype = ctypes.c_void_p
    lib.oa_res_create.argtypes = []
    
    lib.oa_res_destroy.restype = None
    lib.oa_res_destroy.argtypes = [ctypes.c_void_p]
    
    lib.oa_res_acquire.restype = ctypes.c_int
    lib.oa_res_acquire.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
        ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(OaLease)
    ]
    
    lib.oa_res_release.restype = ctypes.c_int
    lib.oa_res_release.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    
    lib.oa_res_revoke.restype = ctypes.c_int
    lib.oa_res_revoke.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    
    # --- 进程管理 ---
    lib.oa_proc_spawn.restype = ctypes.c_void_p
    lib.oa_proc_spawn.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    
    lib.oa_proc_is_alive.restype = ctypes.c_bool
    lib.oa_proc_is_alive.argtypes = [ctypes.c_void_p]
    
    lib.oa_proc_wait.restype = ctypes.c_int
    lib.oa_proc_wait.argtypes = [ctypes.c_void_p, ctypes.c_int]
    
    lib.oa_proc_kill.restype = ctypes.c_bool
    lib.oa_proc_kill.argtypes = [ctypes.c_void_p]
    
    lib.oa_proc_pid.restype = ctypes.c_uint32
    lib.oa_proc_pid.argtypes = [ctypes.c_void_p]
    
    lib.oa_proc_free.restype = None
    lib.oa_proc_free.argtypes = [ctypes.c_void_p]
    
    lib.oa_proc_is_safe_cmd.restype = ctypes.c_bool
    lib.oa_proc_is_safe_cmd.argtypes = [ctypes.c_char_p]
    
    # --- 心跳检测 ---
    lib.oa_heart_create.restype = ctypes.c_void_p
    lib.oa_heart_create.argtypes = [ctypes.c_uint32]
    
    lib.oa_heart_destroy.restype = None
    lib.oa_heart_destroy.argtypes = [ctypes.c_void_p]
    
    lib.oa_heart_register.restype = ctypes.c_int
    lib.oa_heart_register.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    
    lib.oa_heart_beat.restype = ctypes.c_int
    lib.oa_heart_beat.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    
    lib.oa_heart_check.restype = ctypes.c_int
    lib.oa_heart_check.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    
    lib.oa_heart_snapshot.restype = OaHealth
    lib.oa_heart_snapshot.argtypes = [ctypes.c_void_p]
    
    # --- 文件安全 ---
    lib.oa_fs_check.restype = ctypes.c_bool
    lib.oa_fs_check.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int]
    
    lib.oa_fs_lock.restype = ctypes.c_bool
    lib.oa_fs_lock.argtypes = [ctypes.c_char_p, ctypes.c_int]
    
    lib.oa_fs_unlock.restype = None
    lib.oa_fs_unlock.argtypes = [ctypes.c_char_p]


# ============================================================
# 纯Python Fallback 实现
# ============================================================

class _FallbackBus:
    """纯Python消息总线 — 用list+Lock模拟无锁环形缓冲区"""
    
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # agent_id → [msg, ...]
        self._queues: dict = {}
        # 广播消息暂存
        self._broadcast: list = []
        self._subscribers: set = set()
    
    def publish(self, msg_dict: dict) -> int:
        with self._cond:
            to = msg_dict.get("to_agent", 0)
            if to == 0:
                # 广播
                for agent_id in self._subscribers:
                    if agent_id != msg_dict.get("from_agent", 0):
                        q = self._queues.setdefault(agent_id, [])
                        if len(q) >= self.capacity:
                            return OA_ERR_FULL
                        q.append(dict(msg_dict))
            else:
                q = self._queues.setdefault(to, [])
                if len(q) >= self.capacity:
                    return OA_ERR_FULL
                q.append(dict(msg_dict))
            self._cond.notify_all()
        return OA_OK
    
    def receive(self, agent_id: int, timeout_ms: int) -> Optional[dict]:
        deadline = time.monotonic() + timeout_ms / 1000.0 if timeout_ms > 0 else None
        with self._cond:
            self._subscribers.add(agent_id)
            while True:
                q = self._queues.get(agent_id, [])
                if q:
                    return q.pop(0)
                if deadline is None:
                    return None  # 非阻塞
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
    
    def pending(self, agent_id: int) -> int:
        with self._lock:
            return len(self._queues.get(agent_id, []))


class _FallbackSched:
    """纯Python调度器 — 用排序list模拟优先级队列"""
    
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._lock = threading.Lock()
        self._tasks: list = []  # (priority, task_dict)
    
    def push(self, task_dict: dict) -> int:
        with self._lock:
            if len(self._tasks) >= self.capacity:
                return OA_ERR_FULL
            priority = task_dict.get("priority", 0)
            self._tasks.append((priority, task_dict))
            self._tasks.sort(key=lambda x: x[0])
        return OA_OK
    
    def pop(self) -> Optional[dict]:
        with self._lock:
            if not self._tasks:
                return None
            return self._tasks.pop(0)[1]
    
    def size(self) -> int:
        with self._lock:
            return len(self._tasks)


class _FallbackRes:
    """纯Python资源管理器"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._leases: dict = {}  # lease_id → lease_dict
        self._next_id = 1
    
    def acquire(self, agent_id: int, res_type: int, name: str,
                timeout_ms: int) -> Optional[dict]:
        with self._lock:
            # 检查是否已被占用（独占类型）
            if res_type in (OA_RES_SCREEN, OA_RES_MOUSE, OA_RES_KEYBOARD,
                            OA_RES_MIC, OA_RES_SPEAKER):
                for lease in self._leases.values():
                    if (lease["resource_type"] == res_type and
                            lease["resource_name"] == name and
                            lease["agent_id"] != agent_id):
                        now_ms = int(time.time() * 1000)
                        if lease["expires_at"] > now_ms:
                            return None  # 被占用
            
            lease_id = self._next_id
            self._next_id += 1
            now_ms = int(time.time() * 1000)
            lease = {
                "lease_id": lease_id,
                "agent_id": agent_id,
                "resource_type": res_type,
                "granted_at": now_ms,
                "expires_at": now_ms + 300_000,  # 默认5分钟
                "resource_name": name,
            }
            self._leases[lease_id] = lease
            return dict(lease)
    
    def release(self, lease_id: int) -> int:
        with self._lock:
            if lease_id in self._leases:
                del self._leases[lease_id]
                return OA_OK
            return OA_ERR_NOTFOUND
    
    def revoke(self, agent_id: int) -> int:
        with self._lock:
            to_remove = [k for k, v in self._leases.items()
                         if v["agent_id"] == agent_id]
            for k in to_remove:
                del self._leases[k]
            return OA_OK


class _FallbackHeart:
    """纯Python心跳检测"""
    
    def __init__(self, max_agents: int):
        self.max_agents = max_agents
        self._lock = threading.Lock()
        self._agents: dict = {}  # agent_id → last_beat_time
        self._start_time = time.monotonic()
    
    def register(self, agent_id: int) -> int:
        with self._lock:
            if len(self._agents) >= self.max_agents:
                return OA_ERR_FULL
            self._agents[agent_id] = time.monotonic()
            return OA_OK
    
    def beat(self, agent_id: int) -> int:
        with self._lock:
            if agent_id not in self._agents:
                return OA_ERR_NOTFOUND
            self._agents[agent_id] = time.monotonic()
            return OA_OK
    
    def check(self, agent_id: int) -> int:
        with self._lock:
            if agent_id not in self._agents:
                return OA_HEALTH_RED
            elapsed = time.monotonic() - self._agents[agent_id]
            if elapsed < 5:
                return OA_HEALTH_GREEN
            elif elapsed < 15:
                return OA_HEALTH_YELLOW
            elif elapsed < 30:
                return OA_HEALTH_ORANGE
            else:
                return OA_HEALTH_RED
    
    def snapshot(self) -> dict:
        with self._lock:
            healthy = sum(
                1 for t in self._agents.values()
                if time.monotonic() - t < 30
            )
            return {
                "cpu_percent": 0.0,
                "mem_percent": 0.0,
                "disk_percent": 0.0,
                "gpu_mem_percent": 0.0,
                "agent_count": len(self._agents),
                "agent_healthy": healthy,
                "tasks_total": 0,
                "tasks_failed": 0,
                "uptime_ms": int((time.monotonic() - self._start_time) * 1000),
            }


# ============================================================
# 统一FFI接口
# ============================================================

class FFI:
    """统一FFI接口 — 自动选择C底座或纯Python fallback
    
    使用方式完全一致，调用方无需关心底层实现。
    """
    
    def __init__(self):
        self._lib = _load_library()
        self._initialized = False
        
        if self._lib:
            self.backend = "native"
        else:
            self.backend = "fallback"
    
    @property
    def is_native(self) -> bool:
        """是否使用C底座"""
        return self.backend == "native"
    
    # --- 全局 ---
    
    def version(self) -> str:
        """获取版本号"""
        if self._lib:
            return self._lib.oa_version().decode("utf-8")
        return "0.1.0-python"
    
    def init(self) -> int:
        """初始化C底座"""
        if self._lib:
            code = self._lib.oa_init()
            _check(code, "oa_init")
            self._initialized = True
            return code
        self._initialized = True
        return OA_OK
    
    def shutdown(self):
        """关闭C底座"""
        if self._lib and self._initialized:
            self._lib.oa_shutdown()
        self._initialized = False
    
    # --- 消息总线 ---
    
    def bus_create(self, capacity: int = 1024):
        """创建消息总线"""
        if self._lib:
            ptr = self._lib.oa_bus_create(capacity)
            if not ptr:
                raise OaError(OA_ERR_NOMEM, "oa_bus_create")
            return ptr
        return _FallbackBus(capacity)
    
    def bus_destroy(self, bus):
        """销毁消息总线"""
        if self._lib:
            self._lib.oa_bus_destroy(bus)
        # fallback由GC回收
    
    def bus_publish(self, bus, msg: dict) -> int:
        """发布消息
        
        Args:
            bus: 总线句柄
            msg: 消息dict，字段: from_agent, to_agent, msg_type,
                 priority, payload (bytes)
        """
        if self._lib:
            c_msg = OaMsg()
            c_msg.from_agent = msg.get("from_agent", 0)
            c_msg.to_agent = msg.get("to_agent", 0)
            c_msg.msg_type = msg.get("msg_type", 0)
            c_msg.priority = msg.get("priority", 0)
            c_msg.timestamp = int(time.time() * 1_000_000)
            c_msg.trace_id = msg.get("trace_id", 0)
            payload = msg.get("payload", b"")
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            c_msg.payload_len = len(payload)
            ctypes.memmove(c_msg.payload, payload, min(len(payload), OA_MSG_MAX_PAYLOAD))
            code = self._lib.oa_bus_publish(bus, ctypes.byref(c_msg))
            return code
        
        # fallback
        fb_msg = dict(msg)
        if "timestamp" not in fb_msg:
            fb_msg["timestamp"] = int(time.time() * 1_000_000)
        return bus.publish(fb_msg)
    
    def bus_receive(self, bus, agent_id: int, timeout_ms: int = 0) -> Optional[dict]:
        """接收消息
        
        Args:
            bus: 总线句柄
            agent_id: 接收方Agent ID
            timeout_ms: 超时（0=非阻塞，>0=阻塞等待）
        
        Returns:
            消息dict，或None（超时/无消息）
        """
        if self._lib:
            c_msg = OaMsg()
            code = self._lib.oa_bus_receive(
                bus, agent_id, ctypes.byref(c_msg), timeout_ms
            )
            if code == OA_ERR_EMPTY or code == OA_ERR_TIMEOUT:
                return None
            _check(code, "oa_bus_receive")
            return {
                "from_agent": c_msg.from_agent,
                "to_agent": c_msg.to_agent,
                "msg_type": c_msg.msg_type,
                "priority": c_msg.priority,
                "timestamp": c_msg.timestamp,
                "trace_id": c_msg.trace_id,
                "payload": bytes(c_msg.payload[:c_msg.payload_len]),
            }
        
        return bus.receive(agent_id, timeout_ms)
    
    def bus_pending(self, bus, agent_id: int) -> int:
        """查询待处理消息数"""
        if self._lib:
            return self._lib.oa_bus_pending(bus, agent_id)
        return bus.pending(agent_id)
    
    # --- 调度器 ---
    
    def sched_create(self, capacity: int = 256):
        """创建调度器"""
        if self._lib:
            ptr = self._lib.oa_sched_create(capacity)
            if not ptr:
                raise OaError(OA_ERR_NOMEM, "oa_sched_create")
            return ptr
        return _FallbackSched(capacity)
    
    def sched_destroy(self, sched):
        """销毁调度器"""
        if self._lib:
            self._lib.oa_sched_destroy(sched)
    
    def sched_push(self, sched, task: dict) -> int:
        """推入任务"""
        if self._lib:
            c_task = OaTask()
            c_task.task_id = task.get("task_id", 0)
            c_task.agent_id = task.get("agent_id", 0)
            c_task.priority = task.get("priority", 0)
            c_task.deadline = task.get("deadline", 0)
            payload = task.get("payload", b"")
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            c_task.payload_len = len(payload)
            ctypes.memmove(c_task.payload, payload, min(len(payload), 4096))
            return self._lib.oa_sched_push(sched, ctypes.byref(c_task))
        return sched.push(task)
    
    def sched_pop(self, sched) -> Optional[dict]:
        """弹出最高优先级任务"""
        if self._lib:
            c_task = OaTask()
            code = self._lib.oa_sched_pop(sched, ctypes.byref(c_task))
            if code == OA_ERR_EMPTY:
                return None
            _check(code, "oa_sched_pop")
            return {
                "task_id": c_task.task_id,
                "agent_id": c_task.agent_id,
                "priority": c_task.priority,
                "deadline": c_task.deadline,
                "payload": bytes(c_task.payload[:c_task.payload_len]),
            }
        return sched.pop()
    
    def sched_size(self, sched) -> int:
        """查询队列大小"""
        if self._lib:
            return self._lib.oa_sched_size(sched)
        return sched.size()
    
    # --- 资源管理器 ---
    
    def res_create(self):
        """创建资源管理器"""
        if self._lib:
            ptr = self._lib.oa_res_create()
            if not ptr:
                raise OaError(OA_ERR_NOMEM, "oa_res_create")
            return ptr
        return _FallbackRes()
    
    def res_destroy(self, res):
        """销毁资源管理器"""
        if self._lib:
            self._lib.oa_res_destroy(res)
    
    def res_acquire(self, res, agent_id: int, res_type: int,
                    name: str, timeout_ms: int = 5000) -> Optional[dict]:
        """申请资源"""
        if self._lib:
            c_lease = OaLease()
            code = self._lib.oa_res_acquire(
                res, agent_id, res_type,
                name.encode("utf-8"), timeout_ms,
                ctypes.byref(c_lease)
            )
            if code == OA_ERR_TIMEOUT or code == OA_ERR_DENIED:
                return None
            _check(code, "oa_res_acquire")
            return {
                "lease_id": c_lease.lease_id,
                "agent_id": c_lease.agent_id,
                "resource_type": c_lease.resource_type,
                "granted_at": c_lease.granted_at,
                "expires_at": c_lease.expires_at,
                "resource_name": c_lease.resource_name.decode("utf-8").rstrip("\x00"),
            }
        return res.acquire(agent_id, res_type, name, timeout_ms)
    
    def res_release(self, res, lease_id: int) -> int:
        """释放资源"""
        if self._lib:
            return self._lib.oa_res_release(res, lease_id)
        return res.release(lease_id)
    
    def res_revoke(self, res, agent_id: int) -> int:
        """回收某Agent全部资源"""
        if self._lib:
            return self._lib.oa_res_revoke(res, agent_id)
        return res.revoke(agent_id)
    
    # --- 进程管理 ---
    
    def proc_spawn(self, cmd: str, workdir: str = "."):
        """启动进程"""
        if self._lib:
            ptr = self._lib.oa_proc_spawn(
                cmd.encode("utf-8"), workdir.encode("utf-8")
            )
            return ptr  # 可能为None
        # fallback: 用subprocess
        import subprocess
        try:
            proc = subprocess.Popen(
                cmd.split() if isinstance(cmd, str) else cmd, cwd=workdir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            return proc
        except Exception:
            return None
    
    def proc_is_alive(self, proc) -> bool:
        """检查进程是否存活"""
        if self._lib:
            if not proc:
                return False
            return self._lib.oa_proc_is_alive(proc)
        import subprocess
        if isinstance(proc, subprocess.Popen):
            return proc.poll() is None
        return False
    
    def proc_wait(self, proc, timeout_ms: int = -1) -> int:
        """等待进程结束"""
        if self._lib:
            return self._lib.oa_proc_wait(proc, timeout_ms)
        import subprocess
        if isinstance(proc, subprocess.Popen):
            timeout = timeout_ms / 1000.0 if timeout_ms > 0 else None
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return OA_ERR_TIMEOUT
        return OA_ERR_INVALID
    
    def proc_kill(self, proc) -> bool:
        """杀死进程"""
        if self._lib:
            if not proc:
                return False
            return self._lib.oa_proc_kill(proc)
        import subprocess
        if isinstance(proc, subprocess.Popen):
            proc.kill()
            return True
        return False
    
    def proc_pid(self, proc) -> int:
        """获取进程PID"""
        if self._lib:
            if not proc:
                return 0
            return self._lib.oa_proc_pid(proc)
        import subprocess
        if isinstance(proc, subprocess.Popen):
            return proc.pid
        return 0
    
    def proc_free(self, proc):
        """释放进程句柄"""
        if self._lib:
            if proc:
                self._lib.oa_proc_free(proc)
        # fallback: Popen自动GC
    
    def proc_is_safe_cmd(self, cmd: str) -> bool:
        """检查命令是否安全"""
        if self._lib:
            return self._lib.oa_proc_is_safe_cmd(cmd.encode("utf-8"))
        # fallback: 基础黑名单检查
        dangerous = [
            "rm -rf /", "mkfs", "dd if=", ":(){", "chmod -R 777 /",
            "format c:", "del /f /s /q c:", "shutdown",
        ]
        cmd_lower = cmd.lower().strip()
        return not any(d in cmd_lower for d in dangerous)
    
    # --- 心跳检测 ---
    
    def heart_create(self, max_agents: int = 64):
        """创建心跳检测器"""
        if self._lib:
            ptr = self._lib.oa_heart_create(max_agents)
            if not ptr:
                raise OaError(OA_ERR_NOMEM, "oa_heart_create")
            return ptr
        return _FallbackHeart(max_agents)
    
    def heart_destroy(self, heart):
        """销毁心跳检测器"""
        if self._lib:
            self._lib.oa_heart_destroy(heart)
    
    def heart_register(self, heart, agent_id: int) -> int:
        """注册Agent"""
        if self._lib:
            return self._lib.oa_heart_register(heart, agent_id)
        return heart.register(agent_id)
    
    def heart_beat(self, heart, agent_id: int) -> int:
        """发送心跳"""
        if self._lib:
            return self._lib.oa_heart_beat(heart, agent_id)
        return heart.beat(agent_id)
    
    def heart_check(self, heart, agent_id: int) -> int:
        """检查Agent健康"""
        if self._lib:
            return self._lib.oa_heart_check(heart, agent_id)
        return heart.check(agent_id)
    
    def heart_snapshot(self, heart) -> dict:
        """获取系统健康快照"""
        if self._lib:
            snap = self._lib.oa_heart_snapshot(heart)
            return {
                "cpu_percent": snap.cpu_percent,
                "mem_percent": snap.mem_percent,
                "disk_percent": snap.disk_percent,
                "gpu_mem_percent": snap.gpu_mem_percent,
                "agent_count": snap.agent_count,
                "agent_healthy": snap.agent_healthy,
                "tasks_total": snap.tasks_total,
                "tasks_failed": snap.tasks_failed,
                "uptime_ms": snap.uptime_ms,
            }
        return heart.snapshot()
    
    # --- 文件安全 ---
    
    def fs_check(self, agent_id: int, path: str, op: int) -> bool:
        """检查文件操作权限"""
        if self._lib:
            return self._lib.oa_fs_check(agent_id, path.encode("utf-8"), op)
        # fallback: 允许所有（开发模式）
        return True
    
    def fs_lock(self, path: str, timeout_ms: int = 5000) -> bool:
        """文件锁"""
        if self._lib:
            return self._lib.oa_fs_lock(path.encode("utf-8"), timeout_ms)
        # fallback: 简单锁实现
        return True
    
    def fs_unlock(self, path: str):
        """释放文件锁"""
        if self._lib:
            self._lib.oa_fs_unlock(path.encode("utf-8"))


# ============================================================
# 全局单例
# ============================================================

ffi = FFI()
