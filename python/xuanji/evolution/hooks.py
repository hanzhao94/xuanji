"""
xuanji 钩子系统

任务执行前后插入自定义逻辑的中间件模式。
支持钩子链、输入/输出修改、异步钩子。

示例:
    hm = HookManager()

    # 注册钩子
    @hm.before_task
    def log_start(context):
        print(f"任务开始: {context.get('task_name')}")
        return context  # 可以修改输入

    @hm.after_task
    def log_end(context, result):
        print(f"任务完成: {result}")
        return result  # 可以修改输出

    @hm.on_error
    def handle_error(context, error):
        print(f"出错: {error}")

    # 执行（带钩子）
    result = hm.run("my_task", {"input": "data"}, lambda ctx: ctx["input"].upper())
"""

import asyncio
import inspect
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class HookPoint(Enum):
    """钩子挂载点"""
    BEFORE_TASK = "before_task"
    AFTER_TASK = "after_task"
    ON_ERROR = "on_error"
    ON_TOOL_CALL = "on_tool_call"
    BEFORE_LLM = "before_llm"
    AFTER_LLM = "after_llm"
    ON_MESSAGE = "on_message"
    CUSTOM = "custom"


@dataclass
class Hook:
    """钩子定义

    Attributes:
        name: 钩子名称
        point: 挂载点
        callback: 回调函数
        priority: 优先级（越大越先执行）
        enabled: 是否启用
        is_async: 是否为异步
        call_count: 已调用次数
        total_time: 累计耗时（秒）
        last_error: 最近一次错误
    """
    name: str = ""
    point: HookPoint = HookPoint.CUSTOM
    callback: Optional[Callable] = None
    priority: int = 0
    enabled: bool = True
    is_async: bool = False
    call_count: int = 0
    total_time: float = 0.0
    last_error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.callback:
            self.is_async = inspect.iscoroutinefunction(self.callback)
        if not self.name and self.callback:
            self.name = getattr(self.callback, "__name__", "anonymous")


@dataclass
class HookResult:
    """钩子执行结果

    Attributes:
        hook_name: 钩子名
        success: 是否成功
        result: 返回值
        error: 错误信息
        duration: 耗时
    """
    hook_name: str = ""
    success: bool = True
    result: Any = None
    error: Optional[str] = None
    duration: float = 0.0


@dataclass
class TaskContext:
    """任务上下文

    Attributes:
        task_name: 任务名
        input_data: 输入数据
        output_data: 输出数据
        metadata: 元数据
        start_time: 开始时间
        hook_results: 钩子执行结果
    """
    task_name: str = ""
    input_data: Any = None
    output_data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    hook_results: List[HookResult] = field(default_factory=list)


# ─────────────────────────────────────────────
# 钩子管理器
# ─────────────────────────────────────────────

class HookManager:
    """钩子管理器

    管理任务执行前后的钩子链。钩子按优先级排序执行，
    每个钩子可以修改输入/输出（中间件模式）。

    示例:
        hm = HookManager()

        @hm.before_task
        def validate(ctx):
            if not ctx.get("data"):
                raise ValueError("缺少数据")
            return ctx

        @hm.after_task
        def enrich(ctx, result):
            result["processed_at"] = time.time()
            return result

        result = hm.run("process", {"data": "hello"}, my_processor)
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookPoint, List[Hook]] = {
            point: [] for point in HookPoint
        }
        self._lock = threading.Lock()
        self._global_enabled = True

    # ── 注册钩子（装饰器方式）──

    def before_task(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册任务前钩子

        回调签名: (context: Dict) -> Dict
        返回修改后的 context（中间件模式）

        Args:
            callback: 回调函数
            priority: 优先级
            name: 钩子名

        Returns:
            装饰器或回调本身
        """
        return self._register(HookPoint.BEFORE_TASK, callback, priority, name)

    def after_task(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册任务后钩子

        回调签名: (context: Dict, result: Any) -> Any
        返回修改后的 result

        Args:
            callback: 回调函数
            priority: 优先级
            name: 钩子名

        Returns:
            装饰器或回调本身
        """
        return self._register(HookPoint.AFTER_TASK, callback, priority, name)

    def on_error(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册错误钩子

        回调签名: (context: Dict, error: Exception) -> None
        可用于日志、报警、恢复等

        Args:
            callback: 回调函数
            priority: 优先级
            name: 钩子名

        Returns:
            装饰器或回调本身
        """
        return self._register(HookPoint.ON_ERROR, callback, priority, name)

    def on_tool_call(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册工具调用钩子

        回调签名: (tool_name: str, params: Dict) -> Dict
        返回修改后的 params

        Args:
            callback: 回调函数
            priority: 优先级
            name: 钩子名

        Returns:
            装饰器或回调本身
        """
        return self._register(HookPoint.ON_TOOL_CALL, callback, priority, name)

    def before_llm(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册 LLM 调用前钩子

        回调签名: (messages: List[Dict], **kwargs) -> List[Dict]
        """
        return self._register(HookPoint.BEFORE_LLM, callback, priority, name)

    def after_llm(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册 LLM 调用后钩子

        回调签名: (response: str, messages: List[Dict]) -> str
        """
        return self._register(HookPoint.AFTER_LLM, callback, priority, name)

    def on_message(
        self,
        callback: Optional[Callable] = None,
        priority: int = 0,
        name: str = "",
    ) -> Callable:
        """注册消息钩子

        回调签名: (message: Any) -> Any
        """
        return self._register(HookPoint.ON_MESSAGE, callback, priority, name)

    def register(
        self,
        point: str,
        callback: Callable,
        priority: int = 0,
        name: str = "",
    ) -> Hook:
        """通用注册方法

        Args:
            point: 挂载点名称
            callback: 回调函数
            priority: 优先级
            name: 钩子名

        Returns:
            Hook 实例
        """
        try:
            hook_point = HookPoint(point)
        except ValueError:
            hook_point = HookPoint.CUSTOM

        hook = Hook(
            name=name or getattr(callback, "__name__", "anonymous"),
            point=hook_point,
            callback=callback,
            priority=priority,
        )

        with self._lock:
            self._hooks[hook_point].append(hook)
            self._hooks[hook_point].sort(key=lambda h: h.priority, reverse=True)

        return hook

    def _register(
        self,
        point: HookPoint,
        callback: Optional[Callable],
        priority: int,
        name: str,
    ) -> Callable:
        """内部注册方法"""
        def decorator(fn: Callable) -> Callable:
            hook = Hook(
                name=name or getattr(fn, "__name__", "anonymous"),
                point=point,
                callback=fn,
                priority=priority,
            )
            with self._lock:
                self._hooks[point].append(hook)
                self._hooks[point].sort(key=lambda h: h.priority, reverse=True)
            return fn

        if callback is not None:
            decorator(callback)
            return callback
        return decorator

    # ── 执行钩子 ──

    def trigger(self, point: HookPoint, *args: Any, **kwargs: Any) -> List[HookResult]:
        """触发指定挂载点的所有钩子

        Args:
            point: 挂载点
            *args: 传给回调的参数
            **kwargs: 传给回调的关键字参数

        Returns:
            各钩子执行结果
        """
        if not self._global_enabled:
            return []

        with self._lock:
            hooks = [h for h in self._hooks.get(point, []) if h.enabled]

        results: List[HookResult] = []
        for hook in hooks:
            hr = self._execute_hook(hook, *args, **kwargs)
            results.append(hr)

        return results

    def trigger_chain(
        self,
        point: HookPoint,
        initial_value: Any,
        *extra_args: Any,
    ) -> Any:
        """链式触发钩子（中间件模式）

        每个钩子的返回值作为下一个钩子的输入。

        Args:
            point: 挂载点
            initial_value: 初始值
            *extra_args: 额外参数

        Returns:
            最终值
        """
        if not self._global_enabled:
            return initial_value

        with self._lock:
            hooks = [h for h in self._hooks.get(point, []) if h.enabled]

        value = initial_value
        for hook in hooks:
            hr = self._execute_hook(hook, value, *extra_args)
            if hr.success and hr.result is not None:
                value = hr.result

        return value

    def _execute_hook(self, hook: Hook, *args: Any, **kwargs: Any) -> HookResult:
        """执行单个钩子"""
        hr = HookResult(hook_name=hook.name)
        start = time.time()

        try:
            if hook.is_async:
                # 异步钩子同步执行
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            future = pool.submit(
                                asyncio.run, hook.callback(*args, **kwargs)
                            )
                            hr.result = future.result(timeout=30)
                    else:
                        hr.result = loop.run_until_complete(hook.callback(*args, **kwargs))
                except RuntimeError:
                    hr.result = asyncio.run(hook.callback(*args, **kwargs))
            else:
                hr.result = hook.callback(*args, **kwargs)

            hr.success = True
            hook.call_count += 1

        except Exception as e:
            hr.success = False
            hr.error = str(e)
            hook.last_error = str(e)
            logger.error("钩子 '%s' 执行失败: %s", hook.name, e)

        hr.duration = time.time() - start
        hook.total_time += hr.duration
        return hr

    # ── 便捷执行方法 ──

    def run(
        self,
        task_name: str,
        context: Dict[str, Any],
        task_fn: Callable,
    ) -> Any:
        """执行任务（带完整钩子链）

        自动触发 before_task → 执行任务 → after_task / on_error

        Args:
            task_name: 任务名
            context: 任务上下文
            task_fn: 任务函数 (context) -> result

        Returns:
            任务结果（可能被 after_task 钩子修改）
        """
        ctx = dict(context)
        ctx["_task_name"] = task_name

        # before_task 钩子链
        ctx = self.trigger_chain(HookPoint.BEFORE_TASK, ctx)

        try:
            result = task_fn(ctx)

            # after_task 钩子链
            result = self.trigger_chain(HookPoint.AFTER_TASK, ctx, result)

            return result

        except Exception as e:
            # on_error 钩子
            self.trigger(HookPoint.ON_ERROR, ctx, e)
            raise

    async def run_async(
        self,
        task_name: str,
        context: Dict[str, Any],
        task_fn: Callable,
    ) -> Any:
        """异步执行任务（带钩子链）"""
        ctx = dict(context)
        ctx["_task_name"] = task_name

        ctx = self.trigger_chain(HookPoint.BEFORE_TASK, ctx)

        try:
            if inspect.iscoroutinefunction(task_fn):
                result = await task_fn(ctx)
            else:
                result = task_fn(ctx)

            result = self.trigger_chain(HookPoint.AFTER_TASK, ctx, result)
            return result

        except Exception as e:
            self.trigger(HookPoint.ON_ERROR, ctx, e)
            raise

    # ── 管理 ──

    def remove(self, name: str) -> bool:
        """移除钩子

        Args:
            name: 钩子名

        Returns:
            是否成功
        """
        with self._lock:
            for point in self._hooks:
                before = len(self._hooks[point])
                self._hooks[point] = [
                    h for h in self._hooks[point] if h.name != name
                ]
                if len(self._hooks[point]) < before:
                    return True
        return False

    def enable(self, name: str) -> bool:
        """启用钩子"""
        return self._set_enabled(name, True)

    def disable(self, name: str) -> bool:
        """禁用钩子"""
        return self._set_enabled(name, False)

    def _set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            for point in self._hooks:
                for h in self._hooks[point]:
                    if h.name == name:
                        h.enabled = enabled
                        return True
        return False

    def enable_all(self) -> None:
        """全局启用"""
        self._global_enabled = True

    def disable_all(self) -> None:
        """全局禁用"""
        self._global_enabled = False

    def clear(self, point: Optional[HookPoint] = None) -> int:
        """清除钩子

        Args:
            point: 指定挂载点（None 清除所有）

        Returns:
            清除的数量
        """
        with self._lock:
            if point:
                count = len(self._hooks.get(point, []))
                self._hooks[point] = []
                return count
            else:
                count = sum(len(v) for v in self._hooks.values())
                for p in self._hooks:
                    self._hooks[p] = []
                return count

    # ── 查询 ──

    def list_hooks(self, point: Optional[HookPoint] = None) -> List[Dict[str, Any]]:
        """列出钩子

        Args:
            point: 过滤挂载点

        Returns:
            钩子信息列表
        """
        with self._lock:
            result = []
            points = [point] if point else list(self._hooks.keys())

            for p in points:
                for h in self._hooks.get(p, []):
                    result.append({
                        "name": h.name,
                        "point": h.point.value,
                        "priority": h.priority,
                        "enabled": h.enabled,
                        "is_async": h.is_async,
                        "call_count": h.call_count,
                        "total_time": round(h.total_time, 4),
                        "last_error": h.last_error,
                    })
            return result

    def stats(self) -> Dict[str, Any]:
        """钩子统计"""
        with self._lock:
            total = sum(len(v) for v in self._hooks.values())
            by_point = {p.value: len(v) for p, v in self._hooks.items() if v}
            total_calls = sum(
                h.call_count for hooks in self._hooks.values() for h in hooks
            )
            total_time = sum(
                h.total_time for hooks in self._hooks.values() for h in hooks
            )

        return {
            "total_hooks": total,
            "by_point": by_point,
            "total_calls": total_calls,
            "total_time": round(total_time, 4),
            "global_enabled": self._global_enabled,
        }
