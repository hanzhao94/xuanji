"""
xuanji 资源仲裁器

管理多Agent环境下的共享资源分配与调度：
- 操控令牌：屏幕/鼠标/键盘/麦克风是独占资源，同时只能一个Agent使用
- 令牌排队：申请 → 排队 → 获得 → 超时自动释放
- 优先级抢占：P0用户指令 > P1安全 > P2工程 > P3创作 > P4后台
- GPU配额：按Agent分配显存上限
- 端口分区：每个Agent分配独立端口段

零外部依赖，纯标准库实现。
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xuanji.arbiter")


# ============================================================
# 优先级定义
# ============================================================

class ResourcePriority(IntEnum):
    """资源申请优先级（数值越小优先级越高）"""
    P0_USER = 0          # 用户直接指令（最高）
    P1_SAFETY = 1        # 安全相关
    P2_ENGINEERING = 2   # 工程任务
    P3_CREATIVE = 3      # 创作任务
    P4_BACKGROUND = 4    # 后台任务（最低）


# ============================================================
# 资源类型定义
# ============================================================

class ResourceType(IntEnum):
    """资源类型"""
    SCREEN = 1        # 屏幕（独占）
    MOUSE = 2         # 鼠标（独占）
    KEYBOARD = 3      # 键盘（独占）
    MICROPHONE = 4    # 麦克风（独占）
    SPEAKER = 5       # 扬声器（独占）
    GPU = 6           # GPU显存（配额制）
    PORT = 7          # 端口（分区制）
    FILE = 8          # 文件锁（独占）


# 独占型资源集合 — 同一时间只能有一个Agent持有
EXCLUSIVE_RESOURCES = frozenset({
    ResourceType.SCREEN,
    ResourceType.MOUSE,
    ResourceType.KEYBOARD,
    ResourceType.MICROPHONE,
    ResourceType.SPEAKER,
    ResourceType.FILE,
})


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ResourceLease:
    """资源租约 — 代表一个Agent对某资源的持有权"""
    lease_id: int
    agent_id: int
    agent_name: str
    resource_type: ResourceType
    resource_name: str
    priority: ResourcePriority
    granted_at: float           # monotonic时间戳
    expires_at: float           # monotonic时间戳
    data: Dict = field(default_factory=dict)   # 扩展数据（如GPU配额MB）


@dataclass
class ResourceRequest:
    """资源申请"""
    agent_id: int
    agent_name: str
    resource_type: ResourceType
    resource_name: str
    priority: ResourcePriority
    timeout_sec: float = 30.0
    requested_at: float = 0.0
    data: Dict = field(default_factory=dict)

    def __post_init__(self):
        if self.requested_at == 0.0:
            self.requested_at = time.monotonic()


# ============================================================
# 资源仲裁器
# ============================================================

class ResourceArbiter:
    """资源仲裁器 — 多Agent共享资源的中央调度

    核心机制：
    1. 独占资源（屏幕/鼠标/键盘/麦克风/文件锁）：同时只能一个Agent持有
    2. 排队机制：资源被占时自动排队，按优先级+时间排序
    3. 优先级抢占：高优先级请求可抢占低优先级的租约
    4. 超时自动释放：租约到期自动回收，队列中下一个自动提升
    5. GPU配额：每个Agent有独立的显存上限
    6. 端口分区：每个Agent分配独立的端口范围
    """

    # 默认租约时长（秒）
    DEFAULT_LEASE_SEC = 300   # 5分钟

    def __init__(self):
        self._lock = threading.RLock()  # 可重入锁，get_status()内部会调用get_gpu_usage()等方法
        self._next_lease_id = 1

        # 活跃租约: lease_id → ResourceLease
        self._leases: Dict[int, ResourceLease] = {}

        # 排队队列: (resource_type, resource_name) → [ResourceRequest, ...]
        self._queues: Dict[Tuple[int, str], List[ResourceRequest]] = {}

        # GPU配额管理
        self._gpu_quotas: Dict[int, int] = {}    # agent_id → max_vram_mb
        self._gpu_used: Dict[int, int] = {}       # agent_id → current_used_mb

        # 端口分区管理
        self._port_ranges: Dict[int, Tuple[int, int]] = {}  # agent_id → (start, end)

        # 清理线程
        self._running = False
        self._cleanup_thread: Optional[threading.Thread] = None

    # ============================================================
    # 资源申请
    # ============================================================

    def request(self, req: ResourceRequest,
                lease_sec: float = 0) -> Optional[ResourceLease]:
        """申请资源

        如果资源可用 → 立即返回租约
        如果资源被占 → 加入排队，返回None
        高优先级可抢占低优先级

        Args:
            req: 资源申请
            lease_sec: 租约时长（秒），0=使用默认值

        Returns:
            ResourceLease 或 None（排队中）
        """
        if lease_sec <= 0:
            lease_sec = self.DEFAULT_LEASE_SEC

        with self._lock:
            if req.resource_type in EXCLUSIVE_RESOURCES:
                res_key = (int(req.resource_type), req.resource_name)
                return self._try_acquire_exclusive(req, res_key, lease_sec)
            elif req.resource_type == ResourceType.GPU:
                return self._try_acquire_gpu(req, lease_sec)
            elif req.resource_type == ResourceType.PORT:
                return self._try_acquire_port(req, lease_sec)
            else:
                # 未知类型按独占处理
                res_key = (int(req.resource_type), req.resource_name)
                return self._try_acquire_exclusive(req, res_key, lease_sec)

    def _try_acquire_exclusive(self, req: ResourceRequest,
                               res_key: Tuple[int, str],
                               lease_sec: float) -> Optional[ResourceLease]:
        """尝试获取独占资源"""
        current = self._find_active_lease(res_key)

        if current is None:
            # 资源空闲 → 直接授权
            return self._grant_lease(req, lease_sec)

        # 检查当前租约是否过期
        now = time.monotonic()
        if now >= current.expires_at:
            # 已过期 → 回收并授权
            self._revoke_lease_internal(current.lease_id)
            return self._grant_lease(req, lease_sec)

        # 优先级抢占检查（数值越小优先级越高）
        if req.priority < current.priority:
            logger.info(
                f"资源抢占: [{req.agent_name}](P{req.priority}) "
                f"抢占 [{current.agent_name}](P{current.priority}) "
                f"的 {req.resource_name}"
            )
            self._revoke_lease_internal(current.lease_id)
            return self._grant_lease(req, lease_sec)

        # 同等或低优先级 → 排队
        queue = self._queues.setdefault(res_key, [])

        # 避免重复排队
        for existing in queue:
            if existing.agent_id == req.agent_id:
                return None

        queue.append(req)
        # 按优先级排序，同优先级按请求时间排序
        queue.sort(key=lambda r: (r.priority, r.requested_at))

        logger.debug(
            f"[{req.agent_name}] 排队等待 {req.resource_name}，"
            f"位置={len(queue)}"
        )
        return None

    def _try_acquire_gpu(self, req: ResourceRequest,
                         lease_sec: float) -> Optional[ResourceLease]:
        """尝试获取GPU配额"""
        agent_id = req.agent_id
        requested_mb = req.data.get("vram_mb", 0)

        quota = self._gpu_quotas.get(agent_id, 0)
        used = self._gpu_used.get(agent_id, 0)

        if quota <= 0:
            logger.warning(f"[{req.agent_name}] 没有GPU配额")
            return None

        if used + requested_mb > quota:
            logger.warning(
                f"[{req.agent_name}] GPU配额不足: "
                f"已用{used}MB + 申请{requested_mb}MB > 配额{quota}MB"
            )
            return None

        # 扣减配额并授权
        self._gpu_used[agent_id] = used + requested_mb
        lease = self._grant_lease(req, lease_sec)
        if lease:
            lease.data["vram_mb"] = requested_mb
        return lease

    def _try_acquire_port(self, req: ResourceRequest,
                          lease_sec: float) -> Optional[ResourceLease]:
        """尝试获取端口"""
        agent_id = req.agent_id
        port_range = self._port_ranges.get(agent_id)

        if not port_range:
            logger.warning(f"[{req.agent_name}] 没有端口分配")
            return None

        requested_port = req.data.get("port", 0)
        start, end = port_range

        if requested_port < start or requested_port > end:
            logger.warning(
                f"[{req.agent_name}] 端口 {requested_port} "
                f"不在分配范围 [{start}-{end}]"
            )
            return None

        return self._grant_lease(req, lease_sec)

    # ============================================================
    # 资源释放
    # ============================================================

    def release(self, lease_id: int) -> bool:
        """主动释放资源

        释放后自动提升队列中的下一个请求。

        Args:
            lease_id: 租约ID

        Returns:
            是否成功释放
        """
        with self._lock:
            return self._release_and_promote(lease_id)

    def revoke_agent(self, agent_id: int) -> int:
        """回收某Agent的所有资源

        Agent崩溃或停止时调用，释放其全部租约并从排队队列中移除。

        Args:
            agent_id: Agent ID

        Returns:
            回收的租约数量
        """
        with self._lock:
            # 释放所有租约
            to_release = [
                lid for lid, lease in self._leases.items()
                if lease.agent_id == agent_id
            ]
            for lid in to_release:
                self._release_and_promote(lid)

            # 从排队队列中移除
            for key in list(self._queues.keys()):
                self._queues[key] = [
                    r for r in self._queues[key]
                    if r.agent_id != agent_id
                ]
                if not self._queues[key]:
                    del self._queues[key]

            # 清理GPU使用量
            self._gpu_used.pop(agent_id, None)

            return len(to_release)

    def renew(self, lease_id: int, extra_sec: float = 0) -> bool:
        """续约

        Args:
            lease_id: 租约ID
            extra_sec: 延长秒数，0=使用默认值

        Returns:
            是否成功
        """
        if extra_sec <= 0:
            extra_sec = self.DEFAULT_LEASE_SEC

        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                return False
            lease.expires_at = time.monotonic() + extra_sec
            return True

    # ============================================================
    # 内部方法
    # ============================================================

    def _grant_lease(self, req: ResourceRequest,
                     lease_sec: float) -> ResourceLease:
        """授予租约"""
        now = time.monotonic()
        lease_id = self._next_lease_id
        self._next_lease_id += 1

        lease = ResourceLease(
            lease_id=lease_id,
            agent_id=req.agent_id,
            agent_name=req.agent_name,
            resource_type=req.resource_type,
            resource_name=req.resource_name,
            priority=req.priority,
            granted_at=now,
            expires_at=now + lease_sec,
            data=dict(req.data),
        )

        self._leases[lease_id] = lease

        logger.debug(
            f"授予租约 #{lease_id}: [{req.agent_name}] → "
            f"{req.resource_name} ({lease_sec:.0f}s)"
        )
        return lease

    def _release_and_promote(self, lease_id: int) -> bool:
        """释放租约并自动提升队列中的下一个请求"""
        lease = self._leases.pop(lease_id, None)
        if not lease:
            return False

        # GPU配额归还
        if lease.resource_type == ResourceType.GPU:
            vram = lease.data.get("vram_mb", 0)
            used = self._gpu_used.get(lease.agent_id, 0)
            self._gpu_used[lease.agent_id] = max(0, used - vram)

        # 检查排队队列，提升下一个
        res_key = (int(lease.resource_type), lease.resource_name)
        queue = self._queues.get(res_key, [])

        if queue:
            next_req = queue.pop(0)
            if not queue:
                del self._queues[res_key]

            new_lease = self._grant_lease(next_req, self.DEFAULT_LEASE_SEC)
            logger.info(
                f"队列提升: [{next_req.agent_name}] 获得 "
                f"{next_req.resource_name} (租约#{new_lease.lease_id})"
            )

        return True

    def _revoke_lease_internal(self, lease_id: int):
        """内部回收租约（不触发队列提升）"""
        lease = self._leases.pop(lease_id, None)
        if lease and lease.resource_type == ResourceType.GPU:
            vram = lease.data.get("vram_mb", 0)
            used = self._gpu_used.get(lease.agent_id, 0)
            self._gpu_used[lease.agent_id] = max(0, used - vram)

    def _find_active_lease(self, res_key: Tuple[int, str]) -> Optional[ResourceLease]:
        """查找某资源的活跃租约"""
        res_type, res_name = res_key
        for lease in self._leases.values():
            if (int(lease.resource_type) == res_type and
                    lease.resource_name == res_name):
                return lease
        return None

    # ============================================================
    # GPU配额管理
    # ============================================================

    def set_gpu_quota(self, agent_id: int, max_vram_mb: int):
        """设置Agent的GPU显存配额

        Args:
            agent_id: Agent ID
            max_vram_mb: 最大显存（MB）
        """
        with self._lock:
            self._gpu_quotas[agent_id] = max_vram_mb
            self._gpu_used.setdefault(agent_id, 0)

    def get_gpu_usage(self) -> Dict[int, Dict]:
        """获取所有Agent的GPU使用情况

        Returns:
            {agent_id: {"quota_mb": ..., "used_mb": ..., "free_mb": ..., "usage_pct": ...}}
        """
        with self._lock:
            result = {}
            for agent_id, quota in self._gpu_quotas.items():
                used = self._gpu_used.get(agent_id, 0)
                result[agent_id] = {
                    "quota_mb": quota,
                    "used_mb": used,
                    "free_mb": quota - used,
                    "usage_pct": round(used / quota * 100, 1) if quota > 0 else 0,
                }
            return result

    # ============================================================
    # 端口分区管理
    # ============================================================

    def set_port_range(self, agent_id: int, start: int, end: int):
        """设置Agent的端口分区

        Args:
            agent_id: Agent ID
            start: 起始端口（含）
            end: 结束端口（含）
        """
        with self._lock:
            self._port_ranges[agent_id] = (start, end)

    def get_port_range(self, agent_id: int) -> Optional[Tuple[int, int]]:
        """获取Agent的端口分区

        Returns:
            (start_port, end_port) 或 None
        """
        return self._port_ranges.get(agent_id)

    def allocate_port_ranges(self, agent_ids: List[int],
                             base_port: int = 10000,
                             ports_per_agent: int = 100):
        """批量自动分配端口分区

        Args:
            agent_ids: Agent ID列表
            base_port: 起始端口
            ports_per_agent: 每个Agent分配的端口数
        """
        with self._lock:
            for i, agent_id in enumerate(agent_ids):
                start = base_port + i * ports_per_agent
                end = start + ports_per_agent - 1
                self._port_ranges[agent_id] = (start, end)

    # ============================================================
    # 自动清理（过期租约）
    # ============================================================

    def start_cleanup(self, interval: float = 30.0):
        """启动自动清理线程

        Args:
            interval: 清理间隔（秒）
        """
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            args=(interval,),
            daemon=True,
            name="arbiter-cleanup",
        )
        self._cleanup_thread.start()

    def _cleanup_loop(self, interval: float):
        """清理循环"""
        while self._running:
            try:
                self.cleanup_expired()
            except Exception as e:
                logger.error(f"清理异常: {e}")
            time.sleep(interval)

    def cleanup_expired(self) -> int:
        """清理过期租约

        过期的租约自动释放，队列中的下一个请求自动提升。

        Returns:
            清理数量
        """
        now = time.monotonic()
        with self._lock:
            expired = [
                lid for lid, lease in self._leases.items()
                if now >= lease.expires_at
            ]

            for lid in expired:
                lease = self._leases.get(lid)
                if lease:
                    logger.info(
                        f"租约过期: #{lid} [{lease.agent_name}] "
                        f"→ {lease.resource_name}"
                    )
                self._release_and_promote(lid)

            return len(expired)

    def stop(self):
        """停止仲裁器清理线程"""
        self._running = False

    # ============================================================
    # 状态查询
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        """获取仲裁器完整状态

        Returns:
            包含活跃租约、排队请求、GPU使用、端口分配的状态字典
        """
        with self._lock:
            now = time.monotonic()
            return {
                "active_leases": len(self._leases),
                "queued_requests": sum(
                    len(q) for q in self._queues.values()
                ),
                "leases": {
                    lid: {
                        "agent": lease.agent_name,
                        "agent_id": lease.agent_id,
                        "resource": lease.resource_name,
                        "type": ResourceType(lease.resource_type).name,
                        "priority": f"P{lease.priority}",
                        "remaining_sec": round(
                            max(0, lease.expires_at - now), 1
                        ),
                    }
                    for lid, lease in self._leases.items()
                },
                "queues": {
                    f"{ResourceType(k[0]).name}:{k[1]}": [
                        {
                            "agent": r.agent_name,
                            "priority": f"P{r.priority}",
                            "waited_sec": round(now - r.requested_at, 1),
                        }
                        for r in v
                    ]
                    for k, v in self._queues.items()
                },
                "gpu": self.get_gpu_usage(),
                "ports": {
                    str(aid): f"{s}-{e}"
                    for aid, (s, e) in self._port_ranges.items()
                },
            }

    def get_agent_resources(self, agent_id: int) -> Dict[str, Any]:
        """查询某Agent持有的所有资源

        Args:
            agent_id: Agent ID

        Returns:
            {"leases": [...], "gpu": {...}, "port_range": ...}
        """
        with self._lock:
            leases = [
                {
                    "lease_id": lid,
                    "resource": lease.resource_name,
                    "type": ResourceType(lease.resource_type).name,
                    "remaining_sec": round(
                        max(0, lease.expires_at - time.monotonic()), 1
                    ),
                }
                for lid, lease in self._leases.items()
                if lease.agent_id == agent_id
            ]

            gpu = None
            if agent_id in self._gpu_quotas:
                quota = self._gpu_quotas[agent_id]
                used = self._gpu_used.get(agent_id, 0)
                gpu = {
                    "quota_mb": quota,
                    "used_mb": used,
                    "free_mb": quota - used,
                }

            port_range = self._port_ranges.get(agent_id)

            return {
                "leases": leases,
                "gpu": gpu,
                "port_range": f"{port_range[0]}-{port_range[1]}"
                if port_range else None,
            }
