"""
xuanji 定时任务调度器

支持 cron 定时、一次性延迟、间隔重复任务。
后台线程运行，不阻塞主线程。

示例:
    scheduler = TaskScheduler()
    scheduler.start()

    # cron: 每天8点
    scheduler.schedule_cron("morning", "0 8 * * *", lambda: print("早上好!"))

    # 一次性: 10秒后
    scheduler.schedule_once("remind", 10, lambda: print("提醒!"))

    # 间隔: 每60秒
    scheduler.schedule_interval("heartbeat", 60, lambda: print("ping"))

    scheduler.list_jobs()
    scheduler.cancel("heartbeat")
    scheduler.stop()
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class JobType(Enum):
    """任务类型"""
    CRON = "cron"
    ONCE = "once"
    INTERVAL = "interval"


class JobStatus(Enum):
    """任务状态"""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Job:
    """定时任务

    Attributes:
        name: 任务名（唯一标识）
        job_type: 任务类型
        callback: 回调函数
        status: 当前状态
        cron_expr: cron 表达式（仅 CRON 类型）
        interval: 间隔秒数（仅 INTERVAL 类型）
        delay: 延迟秒数（仅 ONCE 类型）
        next_run: 下次执行时间戳
        last_run: 上次执行时间戳
        run_count: 已执行次数
        max_runs: 最大执行次数（0 = 无限）
        created_at: 创建时间
        error: 最近一次错误
    """
    name: str
    job_type: JobType = JobType.ONCE
    callback: Optional[Callable] = None
    status: JobStatus = JobStatus.ACTIVE
    cron_expr: str = ""
    interval: float = 0
    delay: float = 0
    next_run: float = 0
    last_run: float = 0
    run_count: int = 0
    max_runs: int = 0
    created_at: float = field(default_factory=time.time)
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Cron 表达式解析器
# ─────────────────────────────────────────────

class CronParser:
    """简单 cron 表达式解析器

    支持: 分 时 日 月 周
    字段值: * (任意), 数字, 逗号分隔, 范围(1-5), 步长(*/5)

    示例:
        "0 8 * * *"     → 每天8:00
        "*/5 * * * *"   → 每5分钟
        "0 9 * * 1"     → 每周一9:00
        "30 14 1 * *"   → 每月1号14:30
        "0 0 1,15 * *"  → 每月1号和15号0:00
    """

    @staticmethod
    def parse_field(field_str: str, min_val: int, max_val: int) -> List[int]:
        """解析单个 cron 字段

        Args:
            field_str: 字段字符串
            min_val: 最小值
            max_val: 最大值

        Returns:
            匹配的值列表
        """
        values = set()

        for part in field_str.split(","):
            part = part.strip()

            if part == "*":
                values.update(range(min_val, max_val + 1))
            elif "/" in part:
                # 步长: */5 或 1-10/2
                base, step_str = part.split("/", 1)
                step = int(step_str)
                if base == "*":
                    start = min_val
                    end = max_val
                elif "-" in base:
                    start, end = map(int, base.split("-", 1))
                else:
                    start = int(base)
                    end = max_val
                values.update(range(start, end + 1, step))
            elif "-" in part:
                # 范围: 1-5
                start, end = map(int, part.split("-", 1))
                values.update(range(start, end + 1))
            else:
                # 单个数字
                values.add(int(part))

        return sorted(v for v in values if min_val <= v <= max_val)

    @staticmethod
    def parse(expr: str) -> Dict[str, List[int]]:
        """解析完整 cron 表达式

        Args:
            expr: cron 表达式，格式 "分 时 日 月 周"

        Returns:
            各字段的匹配值
        """
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"cron 表达式需要5个字段(分 时 日 月 周)，实际 {len(parts)} 个: '{expr}'"
            )

        return {
            "minute": CronParser.parse_field(parts[0], 0, 59),
            "hour": CronParser.parse_field(parts[1], 0, 23),
            "day": CronParser.parse_field(parts[2], 1, 31),
            "month": CronParser.parse_field(parts[3], 1, 12),
            "weekday": CronParser.parse_field(parts[4], 0, 6),  # 0=周日
        }

    @staticmethod
    def matches(expr: str, dt: Optional[datetime] = None) -> bool:
        """检查给定时间是否匹配 cron 表达式

        Args:
            expr: cron 表达式
            dt: 要检查的时间（默认当前时间）

        Returns:
            是否匹配
        """
        if dt is None:
            dt = datetime.now()

        parsed = CronParser.parse(expr)

        return (
            dt.minute in parsed["minute"]
            and dt.hour in parsed["hour"]
            and dt.day in parsed["day"]
            and dt.month in parsed["month"]
            and dt.weekday() in parsed["weekday"]
        )

    @staticmethod
    def next_time(expr: str, after: Optional[datetime] = None) -> float:
        """计算下一次匹配的时间戳

        Args:
            expr: cron 表达式
            after: 起始时间（默认当前时间）

        Returns:
            下次匹配的 UNIX 时间戳
        """
        if after is None:
            after = datetime.now()

        # 从下一分钟开始搜索
        dt = after.replace(second=0, microsecond=0)

        # 最多搜索2年
        max_iterations = 525960  # 365 * 24 * 60 * 2
        for _ in range(max_iterations):
            dt = datetime(
                dt.year, dt.month, dt.day, dt.hour, dt.minute
            )
            # 加1分钟
            import calendar
            minutes = (
                dt.year * 525960
                + dt.month * 43800
                + dt.day * 1440
                + dt.hour * 60
                + dt.minute
                + 1
            )
            # 简单加1分钟
            ts = time.mktime(dt.timetuple()) + 60
            dt = datetime.fromtimestamp(ts)

            if CronParser.matches(expr, dt):
                return dt.timestamp()

        # 找不到 → 1天后
        return time.time() + 86400


# ─────────────────────────────────────────────
# 任务调度器
# ─────────────────────────────────────────────

class TaskScheduler:
    """定时任务调度器

    后台线程运行，支持 cron、一次性、间隔三种定时模式。

    Args:
        tick_interval: 调度器检查间隔（秒）
    """

    def __init__(self, tick_interval: float = 1.0) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tick_interval = tick_interval

    # ── 调度方法 ──

    def schedule_cron(
        self,
        name: str,
        cron_expr: str,
        callback: Callable,
        max_runs: int = 0,
    ) -> Job:
        """注册 cron 定时任务

        Args:
            name: 任务名
            cron_expr: cron 表达式 (分 时 日 月 周)
            callback: 回调函数
            max_runs: 最大执行次数（0 = 无限）

        Returns:
            Job 实例
        """
        # 验证表达式
        CronParser.parse(cron_expr)

        job = Job(
            name=name,
            job_type=JobType.CRON,
            callback=callback,
            cron_expr=cron_expr,
            max_runs=max_runs,
            next_run=CronParser.next_time(cron_expr),
        )

        with self._lock:
            self._jobs[name] = job

        logger.info("注册 cron 任务: %s (%s)", name, cron_expr)
        return job

    def schedule_once(
        self,
        name: str,
        delay_seconds: float,
        callback: Callable,
    ) -> Job:
        """注册一次性延迟任务

        Args:
            name: 任务名
            delay_seconds: 延迟秒数
            callback: 回调函数

        Returns:
            Job 实例
        """
        job = Job(
            name=name,
            job_type=JobType.ONCE,
            callback=callback,
            delay=delay_seconds,
            next_run=time.time() + delay_seconds,
            max_runs=1,
        )

        with self._lock:
            self._jobs[name] = job

        logger.info("注册一次性任务: %s (%s秒后)", name, delay_seconds)
        return job

    def schedule_interval(
        self,
        name: str,
        interval_seconds: float,
        callback: Callable,
        max_runs: int = 0,
        immediate: bool = False,
    ) -> Job:
        """注册间隔重复任务

        Args:
            name: 任务名
            interval_seconds: 间隔秒数
            callback: 回调函数
            max_runs: 最大执行次数（0 = 无限）
            immediate: 是否立即执行第一次

        Returns:
            Job 实例
        """
        first_run = time.time() if immediate else time.time() + interval_seconds

        job = Job(
            name=name,
            job_type=JobType.INTERVAL,
            callback=callback,
            interval=interval_seconds,
            next_run=first_run,
            max_runs=max_runs,
        )

        with self._lock:
            self._jobs[name] = job

        logger.info("注册间隔任务: %s (每%s秒)", name, interval_seconds)
        return job

    # ── 管理方法 ──

    def cancel(self, name: str) -> bool:
        """取消任务

        Args:
            name: 任务名

        Returns:
            是否成功取消
        """
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return False
            job.status = JobStatus.CANCELLED
            del self._jobs[name]
        logger.info("取消任务: %s", name)
        return True

    def pause(self, name: str) -> bool:
        """暂停任务"""
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return False
            job.status = JobStatus.PAUSED
        return True

    def resume(self, name: str) -> bool:
        """恢复任务"""
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return False
            job.status = JobStatus.ACTIVE
        return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        """列出所有任务

        Returns:
            任务信息列表
        """
        with self._lock:
            result = []
            for job in self._jobs.values():
                info = {
                    "name": job.name,
                    "type": job.job_type.value,
                    "status": job.status.value,
                    "run_count": job.run_count,
                    "next_run": datetime.fromtimestamp(job.next_run).isoformat()
                    if job.next_run
                    else None,
                    "last_run": datetime.fromtimestamp(job.last_run).isoformat()
                    if job.last_run
                    else None,
                    "error": job.error,
                }
                if job.job_type == JobType.CRON:
                    info["cron_expr"] = job.cron_expr
                elif job.job_type == JobType.INTERVAL:
                    info["interval"] = job.interval
                result.append(info)
            return result

    def get_job(self, name: str) -> Optional[Job]:
        """获取任务"""
        with self._lock:
            return self._jobs.get(name)

    # ── 生命周期 ──

    def start(self) -> None:
        """启动调度器（后台线程）"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("调度器已启动")

    def stop(self) -> None:
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("调度器已停止")

    @property
    def is_running(self) -> bool:
        """调度器是否在运行"""
        return self._running

    # ── 内部循环 ──

    def _loop(self) -> None:
        """调度循环"""
        while self._running:
            now = time.time()

            with self._lock:
                jobs_to_run = [
                    j
                    for j in self._jobs.values()
                    if j.status == JobStatus.ACTIVE and j.next_run <= now
                ]

            for job in jobs_to_run:
                self._execute_job(job)

            time.sleep(self._tick_interval)

    def _execute_job(self, job: Job) -> None:
        """执行单个任务"""
        try:
            if job.callback:
                job.callback()
            job.last_run = time.time()
            job.run_count += 1
            job.error = None

            # 更新下次执行时间
            if job.job_type == JobType.ONCE:
                job.status = JobStatus.COMPLETED
            elif job.job_type == JobType.INTERVAL:
                job.next_run = time.time() + job.interval
            elif job.job_type == JobType.CRON:
                job.next_run = CronParser.next_time(job.cron_expr)

            # 检查最大执行次数
            if job.max_runs > 0 and job.run_count >= job.max_runs:
                job.status = JobStatus.COMPLETED

            logger.debug("任务 '%s' 执行成功 (第%d次)", job.name, job.run_count)

        except Exception as e:
            job.error = str(e)
            job.status = JobStatus.FAILED
            logger.error("任务 '%s' 执行失败: %s", job.name, e)

    # ── 便捷方法 ──

    def run_now(self, name: str) -> bool:
        """立即执行一次指定任务

        Args:
            name: 任务名

        Returns:
            是否成功
        """
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return False

        self._execute_job(job)
        return True

    def clear_all(self) -> int:
        """清除所有任务

        Returns:
            清除的任务数
        """
        with self._lock:
            count = len(self._jobs)
            self._jobs.clear()
        return count
