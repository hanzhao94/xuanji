"""
xuanji 分身状态机 (SubagentFSM)

精细的Agent生命周期状态机 + 自动恢复引擎。

状态流转：
  SPAWNING → INITIALIZING → RUNNING → COMPLETED (终态)
                                   → FAILED → RETRYING → RUNNING (重试)
                                            → ESCALATED (终态，上报)

核心设计：
  - 严格状态转换验证（非法跳转直接报错）
  - 事件溯源（所有转换记录不可变事件）
  - 7种故障分类 + 对应恢复配方
  - 线程安全（每个FSM独立锁）
  - JSON持久化 + 原子写入

零外部依赖，纯Python标准库。

# 核心逻辑提炼自开源工程实践
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ============================================================
# 1. 状态枚举
# ============================================================

class SubagentState(Enum):
    """分身生命周期的7种状态"""
    SPAWNING = "spawning"
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"       # 终态
    FAILED = "failed"
    RETRYING = "retrying"
    ESCALATED = "escalated"       # 终态


# 合法状态转换矩阵
_VALID_TRANSITIONS: Dict[SubagentState, List[SubagentState]] = {
    SubagentState.SPAWNING:      [SubagentState.INITIALIZING, SubagentState.FAILED],
    SubagentState.INITIALIZING:  [SubagentState.RUNNING, SubagentState.FAILED],
    SubagentState.RUNNING:       [SubagentState.COMPLETED, SubagentState.FAILED],
    SubagentState.FAILED:        [SubagentState.RETRYING, SubagentState.ESCALATED],
    SubagentState.RETRYING:      [SubagentState.RUNNING, SubagentState.FAILED, SubagentState.ESCALATED],
    SubagentState.COMPLETED:     [],
    SubagentState.ESCALATED:     [],
}


# ============================================================
# 2. 事件记录
# ============================================================

@dataclass(frozen=True)
class SubagentEvent:
    """状态转换事件（不可变，用于事件溯源）"""
    seq: int
    timestamp: float
    from_state: SubagentState
    to_state: SubagentState
    detail: Optional[str] = None
    payload: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "detail": self.detail,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SubagentEvent:
        return cls(
            seq=d["seq"], timestamp=d["timestamp"],
            from_state=SubagentState(d["from_state"]),
            to_state=SubagentState(d["to_state"]),
            detail=d.get("detail"), payload=d.get("payload"),
        )


# ============================================================
# 3. 状态机
# ============================================================

class InvalidTransitionError(Exception):
    """非法状态转换"""
    pass


class SubagentFSM:
    """
    单个分身的有限状态机。

    严格转换验证 + 事件溯源 + 线程安全。
    """

    def __init__(self, task_id: str, task_description: str = ""):
        self.task_id = task_id
        self.task_description = task_description
        self._state = SubagentState.SPAWNING
        self._events: List[SubagentEvent] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._created_at = time.time()
        self._completed_at: Optional[float] = None
        self._result: Optional[str] = None
        self._error: Optional[str] = None
        self._retry_count = 0

    @property
    def state(self) -> SubagentState:
        return self._state

    @property
    def events(self) -> List[SubagentEvent]:
        return list(self._events)

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def completed_at(self) -> Optional[float]:
        return self._completed_at

    @property
    def duration(self) -> Optional[float]:
        if self._completed_at is not None:
            return self._completed_at - self._created_at
        return None

    @property
    def is_terminal(self) -> bool:
        return self._state in (SubagentState.COMPLETED, SubagentState.ESCALATED)

    def transition(self, to_state: SubagentState, detail: Optional[str] = None,
                   payload: Optional[dict] = None) -> SubagentEvent:
        """
        执行状态转换。非法转换抛出InvalidTransitionError。
        """
        with self._lock:
            valid = _VALID_TRANSITIONS.get(self._state, [])
            if to_state not in valid:
                raise InvalidTransitionError(
                    f"非法转换: {self._state.value} → {to_state.value} "
                    f"(task={self.task_id}, 合法: {[s.value for s in valid]})"
                )
            from_state = self._state
            self._seq += 1
            event = SubagentEvent(
                seq=self._seq, timestamp=time.time(),
                from_state=from_state, to_state=to_state,
                detail=detail, payload=payload,
            )
            self._events.append(event)
            self._state = to_state
            if to_state == SubagentState.RETRYING:
                self._retry_count += 1
            if to_state in (SubagentState.COMPLETED, SubagentState.ESCALATED):
                self._completed_at = time.time()
            return event

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_description": self.task_description,
            "state": self._state.value,
            "events": [e.to_dict() for e in self._events],
            "created_at": self._created_at,
            "completed_at": self._completed_at,
            "result": self._result,
            "error": self._error,
            "retry_count": self._retry_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SubagentFSM:
        fsm = cls(d["task_id"], d.get("task_description", ""))
        fsm._state = SubagentState(d["state"])
        fsm._events = [SubagentEvent.from_dict(e) for e in d.get("events", [])]
        fsm._seq = len(fsm._events)
        fsm._created_at = d.get("created_at", time.time())
        fsm._completed_at = d.get("completed_at")
        fsm._result = d.get("result")
        fsm._error = d.get("error")
        fsm._retry_count = d.get("retry_count", 0)
        return fsm


# ============================================================
# 4. 故障分类 + 恢复引擎
# ============================================================

class FailureClass(Enum):
    """故障分类（7种）"""
    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    TASK_ERROR = "task_error"
    QUALITY_FAIL = "quality_fail"
    RESOURCE_EXHAUST = "resource_exhaust"
    CONTEXT_OVERFLOW = "context_overflow"
    UNKNOWN = "unknown"


class RecoveryResult(Enum):
    """恢复结果"""
    RECOVERED = "recovered"
    RETRY_SCHEDULED = "retry_scheduled"
    ESCALATION_REQUIRED = "escalation_required"


@dataclass
class RecoveryRecipe:
    """恢复配方"""
    failure_class: FailureClass
    max_retries: int = 1
    recovery_action: str = ""
    escalation_policy: str = "alert_human"  # alert_human / log_and_continue / abort


# 默认恢复配方
_DEFAULT_RECIPES: Dict[FailureClass, RecoveryRecipe] = {
    FailureClass.TIMEOUT: RecoveryRecipe(
        FailureClass.TIMEOUT, max_retries=2,
        recovery_action="延长超时后重试"),
    FailureClass.MODEL_ERROR: RecoveryRecipe(
        FailureClass.MODEL_ERROR, max_retries=3,
        recovery_action="冷却后重试，可切换备用模型"),
    FailureClass.TASK_ERROR: RecoveryRecipe(
        FailureClass.TASK_ERROR, max_retries=1,
        recovery_action="简化任务参数后重试"),
    FailureClass.QUALITY_FAIL: RecoveryRecipe(
        FailureClass.QUALITY_FAIL, max_retries=2,
        recovery_action="提升质量提示词后重试",
        escalation_policy="log_and_continue"),
    FailureClass.RESOURCE_EXHAUST: RecoveryRecipe(
        FailureClass.RESOURCE_EXHAUST, max_retries=0,
        recovery_action="无法自动恢复",
        escalation_policy="abort"),
    FailureClass.CONTEXT_OVERFLOW: RecoveryRecipe(
        FailureClass.CONTEXT_OVERFLOW, max_retries=1,
        recovery_action="截断上下文或拆分任务后重试"),
    FailureClass.UNKNOWN: RecoveryRecipe(
        FailureClass.UNKNOWN, max_retries=1,
        recovery_action="通用重试"),
}


class RecoveryEngine:
    """
    自动恢复引擎 — 故障分类→恢复配方→自动决策。
    """

    def __init__(self, recipes: Optional[Dict[FailureClass, RecoveryRecipe]] = None):
        self._recipes = recipes or dict(_DEFAULT_RECIPES)

    def get_recipe(self, fc: FailureClass) -> RecoveryRecipe:
        return self._recipes.get(fc, _DEFAULT_RECIPES[FailureClass.UNKNOWN])

    def attempt_recovery(self, fsm: SubagentFSM, fc: FailureClass,
                         error: str) -> RecoveryResult:
        """
        尝试恢复失败的分身。

        流程：检查重试次数 → 未超限则RETRYING → 超限则ESCALATED
        """
        recipe = self.get_recipe(fc)
        if fsm.state != SubagentState.FAILED:
            return RecoveryResult.ESCALATION_REQUIRED

        if fsm.retry_count >= recipe.max_retries:
            fsm.transition(
                SubagentState.ESCALATED,
                detail=f"重试耗尽({fsm.retry_count}/{recipe.max_retries})",
                payload={"failure_class": fc.value, "error": error,
                         "escalation_policy": recipe.escalation_policy},
            )
            return RecoveryResult.ESCALATION_REQUIRED

        fsm.transition(
            SubagentState.RETRYING,
            detail=f"自动恢复: {recipe.recovery_action} "
                   f"(第{fsm.retry_count + 1}次, 最多{recipe.max_retries}次)",
            payload={"failure_class": fc.value, "error": error},
        )
        return RecoveryResult.RETRY_SCHEDULED


# ============================================================
# 5. 分身管理器
# ============================================================

class SubagentManager:
    """
    分身注册中心（线程安全）。

    管理所有分身的生命周期：注册、查询、统计、回调、持久化。
    """

    def __init__(self, state_file: Optional[str] = None, auto_persist: bool = True):
        self._fsms: Dict[str, SubagentFSM] = {}
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[str, SubagentEvent], None]] = []
        self._state_file = Path(state_file) if state_file else None
        self._auto_persist = auto_persist
        self._recovery = RecoveryEngine()
        if self._state_file:
            self._load_state()

    def register(self, task_id: str, task_description: str = "") -> SubagentFSM:
        """注册新分身"""
        with self._lock:
            if task_id in self._fsms:
                raise ValueError(f"任务ID已存在: {task_id}")
            fsm = SubagentFSM(task_id, task_description)
            self._fsms[task_id] = fsm
            if self._auto_persist:
                self._save_state_unlocked()
            return fsm

    def get(self, task_id: str) -> Optional[SubagentFSM]:
        with self._lock:
            return self._fsms.get(task_id)

    def list_active(self) -> List[SubagentFSM]:
        with self._lock:
            return [f for f in self._fsms.values() if not f.is_terminal]

    def list_by_state(self, state: SubagentState) -> List[SubagentFSM]:
        with self._lock:
            return [f for f in self._fsms.values() if f.state == state]

    def transition(self, task_id: str, to_state: SubagentState,
                   detail: str = None, payload: dict = None) -> SubagentEvent:
        """状态转换 + 回调 + 持久化"""
        with self._lock:
            fsm = self._fsms.get(task_id)
            if fsm is None:
                raise KeyError(f"任务不存在: {task_id}")
            event = fsm.transition(to_state, detail, payload)
            for cb in self._callbacks:
                try:
                    cb(task_id, event)
                except Exception:
                    pass
            if self._auto_persist:
                self._save_state_unlocked()
            return event

    def on_state_change(self, callback: Callable[[str, SubagentEvent], None]):
        """注册状态变更回调"""
        with self._lock:
            self._callbacks.append(callback)

    def get_stats(self) -> dict:
        """统计面板"""
        with self._lock:
            counts = {s.value: 0 for s in SubagentState}
            completed, failed_esc, durations = 0, 0, []
            for fsm in self._fsms.values():
                counts[fsm.state.value] += 1
                if fsm.state == SubagentState.COMPLETED:
                    completed += 1
                    if fsm.duration is not None:
                        durations.append(fsm.duration)
                elif fsm.state in (SubagentState.FAILED, SubagentState.ESCALATED):
                    failed_esc += 1
            finished = completed + failed_esc
            return {
                "total": len(self._fsms),
                "state_counts": counts,
                "success_rate": round(completed / finished * 100, 2) if finished else 0.0,
                "avg_duration_seconds": round(sum(durations) / len(durations), 2) if durations else 0.0,
                "active_count": sum(1 for f in self._fsms.values() if not f.is_terminal),
            }

    # ─── 持久化 ───

    def _save_state_unlocked(self):
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1, "saved_at": time.time(),
                "fsms": {tid: fsm.to_dict() for tid, fsm in self._fsms.items()},
            }
            tmp = self._state_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._state_file)
        except Exception:
            pass

    def save_state(self):
        with self._lock:
            self._save_state_unlocked()

    def _load_state(self):
        if not self._state_file or not self._state_file.exists():
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for tid, d in data.get("fsms", {}).items():
                self._fsms[tid] = SubagentFSM.from_dict(d)
        except Exception:
            pass


# ============================================================
# 6. 便捷接口
# ============================================================

def track_spawn(task_id: str, task: str,
                manager: Optional[SubagentManager] = None) -> SubagentFSM:
    """
    跟踪分身创建（自动推进到RUNNING）。

    Args:
        task_id: 任务ID
        task: 任务描述
        manager: 可选的管理器（None则创建临时FSM）

    Returns:
        SubagentFSM
    """
    if manager:
        fsm = manager.register(task_id, task)
        manager.transition(task_id, SubagentState.INITIALIZING, "开始初始化")
        manager.transition(task_id, SubagentState.RUNNING, "开始执行")
        return fsm
    else:
        fsm = SubagentFSM(task_id, task)
        fsm.transition(SubagentState.INITIALIZING, "开始初始化")
        fsm.transition(SubagentState.RUNNING, "开始执行")
        return fsm


def track_complete(task_id: str, result: str,
                   manager: Optional[SubagentManager] = None):
    """跟踪分身完成"""
    if manager:
        fsm = manager.get(task_id)
        if fsm:
            fsm._result = result
            manager.transition(task_id, SubagentState.COMPLETED, "完成",
                               {"result_preview": result[:200]})
    # 无manager时无法操作（需要FSM引用）


def track_failure(task_id: str, error: str, fc: FailureClass,
                  manager: Optional[SubagentManager] = None) -> RecoveryResult:
    """跟踪分身失败并尝试恢复"""
    if not manager:
        return RecoveryResult.ESCALATION_REQUIRED
    fsm = manager.get(task_id)
    if not fsm:
        raise KeyError(f"任务不存在: {task_id}")
    fsm._error = error
    if fsm.state != SubagentState.FAILED:
        manager.transition(task_id, SubagentState.FAILED, f"失败: {error[:100]}",
                           {"error": error, "failure_class": fc.value})
    return manager._recovery.attempt_recovery(fsm, fc, error)
