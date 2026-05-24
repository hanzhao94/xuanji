"""
长任务管理器

支持：
  - 异步执行长任务（不阻塞主循环）
  - 进度追踪（百分比 + 阶段状态）
  - 检查点/恢复（中断后可继续）
  - 超时控制
  - 任务队列

用法：
  # 简单任务
  task = await long_task_manager.run("研究市场趋势", research_fn)
  
  # 带进度回调
  task = await long_task_manager.run(
      "写长报告",
      write_report_fn,
      progress_fn=lambda pct, stage: print(f"{pct}% {stage}")
  )
  
  # 查询状态
  status = long_task_manager.status(task_id)
  
  # 恢复中断任务
  task = await long_task_manager.resume(task_id)
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskCheckpoint:
    """任务检查点"""
    stage: str
    progress: float
    data: Any
    timestamp: float


@dataclass
class LongTask:
    """长任务"""
    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    current_stage: str = ""
    stages: List[str] = field(default_factory=list)
    result: Any = None
    error: str = ""
    checkpoints: List[TaskCheckpoint] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    completed_at: float = 0
    timeout: float = 0  # 0=不限制
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    
    @property
    def elapsed(self) -> float:
        """已运行时间（秒）"""
        if self.started_at == 0:
            return 0
        end = self.completed_at if self.completed_at else time.time()
        return end - self.started_at
    
    @property
    def remaining(self) -> Optional[float]:
        """剩余时间估算（秒）"""
        if self.status != TaskStatus.RUNNING or self.progress <= 0:
            return None
        if self.elapsed <= 0:
            return None
        rate = self.progress / self.elapsed
        return (1.0 - self.progress) / rate if rate > 0 else None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "current_stage": self.current_stage,
            "stages": self.stages,
            "result": str(self.result)[:500] if self.result else None,
            "error": self.error,
            "checkpoints": len(self.checkpoints),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed": round(self.elapsed, 1),
        }


class LongTaskManager:
    """长任务管理器"""
    
    def __init__(self, max_concurrent: int = 3, checkpoint_dir: str = None):
        self._tasks: Dict[str, LongTask] = {}
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._checkpoint_dir = checkpoint_dir
        self._progress_callbacks: Dict[str, Callable] = {}
    
    async def run(
        self,
        name: str,
        fn,
        *,
        stages: List[str] = None,
        timeout: float = 0,
        progress_fn: Callable = None,
        task_id: str = None,
        wait: bool = True,
        **fn_kwargs
    ) -> LongTask:
        """启动长任务
        
        Args:
            name: 任务名
            fn: 任务函数（async），签名: fn(progress_fn, **kwargs)
            stages: 阶段列表（可选）
            timeout: 超时秒数（0=不限制）
            progress_fn: 进度回调
            task_id: 自定义ID
            wait: 是否等待完成（True=阻塞直到完成，False=立即返回）
            **fn_kwargs: 传给fn的参数
        
        Returns:
            LongTask对象
        """
        task_id = task_id or f"task-{uuid.uuid4().hex[:8]}"
        
        task = LongTask(
            id=task_id,
            name=name,
            stages=stages or [],
            timeout=timeout,
        )
        
        self._tasks[task_id] = task
        if progress_fn:
            self._progress_callbacks[task_id] = progress_fn
        
        # 创建asyncio任务
        async_task = asyncio.create_task(self._execute(task, fn, **fn_kwargs))
        task._task = async_task
        
        if wait:
            await async_task
        
        return task
    
    async def resume(self, task_id: str, **override_kwargs) -> LongTask:
        """恢复中断的任务"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status not in (TaskStatus.PAUSED, TaskStatus.FAILED):
            raise ValueError(f"Task {task_id} cannot resume (status={task.status})")
        
        # 从最后一个检查点恢复
        if task.checkpoints:
            last_cp = task.checkpoints[-1]
            task.progress = last_cp.progress
            task.current_stage = last_cp.stage
        
        task.status = TaskStatus.PENDING
        task.error = ""
        task.completed_at = 0
        
        return task
    
    def get(self, task_id: str) -> Optional[LongTask]:
        """获取任务"""
        return self._tasks.get(task_id)
    
    def list_tasks(self, status: TaskStatus = None) -> List[LongTask]:
        """列出任务"""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)
    
    async def cancel(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False
        if task._task:
            task._task.cancel()
            try:
                await task._task
            except asyncio.CancelledError:
                pass
        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        return True
    
    def status_summary(self) -> dict:
        """任务状态摘要"""
        tasks = list(self._tasks.values())
        return {
            "total": len(tasks),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
            "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
            "max_concurrent": self._max_concurrent,
        }
    
    def recent(self, n: int = 5) -> List[dict]:
        """最近N个任务摘要"""
        tasks = self.list_tasks()
        return [t.to_dict() for t in tasks[:n]]
    
    # === 内部方法 ===
    
    async def _execute(self, task: LongTask, fn, **fn_kwargs) -> LongTask:
        """执行任务"""
        async with self._semaphore:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            
            # 进度回调
            def _progress(progress: float, stage: str = "", checkpoint_data: Any = None):
                task.progress = max(0, min(1, progress))
                if stage:
                    task.current_stage = stage
                if checkpoint_data is not None:
                    task.checkpoints.append(TaskCheckpoint(
                        stage=stage,
                        progress=task.progress,
                        data=checkpoint_data,
                        timestamp=time.time(),
                    ))
                # 回调
                cb = self._progress_callbacks.get(task.id)
                if cb:
                    cb(task.progress, task.current_stage)
                # 保存检查点
                if checkpoint_data is not None and self._checkpoint_dir:
                    self._save_checkpoint(task)
            
            try:
                # 超时控制
                if task.timeout > 0:
                    result = await asyncio.wait_for(
                        fn(_progress, **fn_kwargs),
                        timeout=task.timeout
                    )
                else:
                    result = await fn(_progress, **fn_kwargs)
                
                task.result = result
                task.progress = 1.0
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                raise
            
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.error = f"超时({task.timeout}s)"
                task.completed_at = time.time()
            
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()
            
            finally:
                self._progress_callbacks.pop(task.id, None)
            
            return task
    
    def _save_checkpoint(self, task: LongTask):
        """保存检查点到文件"""
        import os
        if not self._checkpoint_dir:
            return
        os.makedirs(self._checkpoint_dir, exist_ok=True)
        path = os.path.join(self._checkpoint_dir, f"{task.id}.json")
        data = task.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def __repr__(self) -> str:
        s = self.status_summary()
        return (
            f"<LongTaskManager running={s['running']}/{s['max_concurrent']} "
            f"completed={s['completed']} failed={s['failed']}>"
        )


# 模块级单例
default_manager = LongTaskManager()


async def run_long_task(name: str, fn, **kwargs) -> LongTask:
    """便捷函数：运行长任务"""
    return await default_manager.run(name, fn, **kwargs)


def get_task(task_id: str) -> Optional[LongTask]:
    """便捷函数：获取任务"""
    return default_manager.get(task_id)


def list_tasks(**kwargs) -> List[LongTask]:
    """便捷函数：列出任务"""
    return default_manager.list_tasks(**kwargs)


def task_status_summary() -> dict:
    """便捷函数：状态摘要"""
    return default_manager.status_summary()
