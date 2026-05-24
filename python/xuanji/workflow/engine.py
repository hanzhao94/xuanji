"""
xuanji 工作流引擎

多步骤自动化流程定义与执行。
支持顺序、条件分支、循环、并行执行模式。

示例:
    engine = WorkflowEngine()
    workflow = Workflow(
        name="数据处理流水线",
        steps=[
            Step(name="获取数据", action="fetch", params={"url": "..."}),
            Step(name="清洗", action="clean", params={"input": "{{获取数据.result}}"}),
            Step(name="存储", action="save", params={"data": "{{清洗.result}}"}),
        ]
    )
    result = engine.execute(workflow)
"""

import re
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

class StepStatus(Enum):
    """步骤执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class FlowControl(Enum):
    """流程控制类型"""
    SEQUENTIAL = "sequential"     # 顺序执行
    CONDITIONAL = "conditional"   # 条件分支 (if/else)
    LOOP_WHILE = "loop_while"     # while 循环
    LOOP_FOR = "loop_for"         # for 循环
    PARALLEL = "parallel"         # 并行执行


@dataclass
class Step:
    """工作流步骤定义

    Attributes:
        name: 步骤名称（唯一标识）
        action: 动作名称，对应注册的 action handler
        params: 动作参数，支持 {{step_name.result}} 变量替换
        next_on_success: 成功后跳转的步骤名（None 则继续下一步）
        next_on_failure: 失败后跳转的步骤名（None 则终止）
        condition: 条件表达式字符串，非空时仅条件为真才执行
        flow_control: 流程控制类型
        sub_steps: 子步骤（用于 parallel / loop）
        loop_items: for 循环的迭代列表或引用
        loop_var: for 循环的变量名
        loop_condition: while 循环的条件表达式
        max_retries: 最大重试次数
        timeout: 超时秒数（0 = 无限）
    """
    name: str
    action: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    next_on_success: Optional[str] = None
    next_on_failure: Optional[str] = None
    condition: Optional[str] = None
    flow_control: FlowControl = FlowControl.SEQUENTIAL
    sub_steps: List["Step"] = field(default_factory=list)
    loop_items: Optional[Union[str, List[Any]]] = None
    loop_var: str = "item"
    loop_condition: Optional[str] = None
    max_retries: int = 0
    timeout: float = 0


@dataclass
class StepResult:
    """步骤执行结果

    Attributes:
        step_name: 步骤名
        status: 执行状态
        result: 返回值
        error: 错误信息
        duration: 耗时（秒）
        retries: 实际重试次数
    """
    step_name: str
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    duration: float = 0.0
    retries: int = 0


@dataclass
class Workflow:
    """工作流定义

    Attributes:
        name: 工作流名称
        steps: 步骤列表
        description: 描述
        metadata: 自定义元数据
    """
    name: str
    steps: List[Step] = field(default_factory=list)
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    """工作流执行结果

    Attributes:
        workflow_name: 工作流名称
        success: 是否全部成功
        step_results: 各步骤结果
        duration: 总耗时
        variables: 最终变量上下文
    """
    workflow_name: str
    success: bool = True
    step_results: List[StepResult] = field(default_factory=list)
    duration: float = 0.0
    variables: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────
# 变量替换引擎
# ─────────────────────────────────────────────

# 匹配 {{xxx}} 或 {{xxx.yyy}} 或 {{xxx.yyy.zzz}}
_VAR_PATTERN = re.compile(r"\{\{([\w.]+)\}\}")


def _resolve_var(path: str, context: Dict[str, Any]) -> Any:
    """按路径从上下文取值

    Args:
        path: 变量路径，如 "step1.result" 或 "vars.count"
        context: 变量上下文

    Returns:
        解析到的值，找不到返回原占位符
    """
    parts = path.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return "{{" + path + "}}"
    return current


def substitute_vars(value: Any, context: Dict[str, Any]) -> Any:
    """递归替换参数中的 {{变量}} 占位符

    支持 str / dict / list 递归替换。
    如果整个字符串就是一个变量引用，直接返回原始类型（不强制 str）。

    Args:
        value: 待替换的值
        context: 变量上下文

    Returns:
        替换后的值
    """
    if isinstance(value, str):
        # 完整匹配 → 返回原始类型
        full_match = _VAR_PATTERN.fullmatch(value)
        if full_match:
            return _resolve_var(full_match.group(1), context)
        # 部分匹配 → 字符串拼接
        def _replacer(m: re.Match) -> str:
            resolved = _resolve_var(m.group(1), context)
            return str(resolved)
        return _VAR_PATTERN.sub(_replacer, value)
    elif isinstance(value, dict):
        return {k: substitute_vars(v, context) for k, v in value.items()}
    elif isinstance(value, list):
        return [substitute_vars(v, context) for v in value]
    return value


# ─────────────────────────────────────────────
# 条件表达式求值
# ─────────────────────────────────────────────

def _eval_condition(expr: str, context: Dict[str, Any]) -> bool:
    """安全地求值条件表达式

    先做变量替换，然后在受限环境中 eval。
    仅允许比较运算和基本类型操作。

    Args:
        expr: 条件表达式字符串
        context: 变量上下文

    Returns:
        布尔值
    """
    resolved = substitute_vars(expr, context)
    if isinstance(resolved, bool):
        return resolved
    if not isinstance(resolved, str):
        return bool(resolved)
    try:
        # 受限 eval — 不暴露内置函数
        safe_globals = {"__builtins__": {}}
        safe_locals = {}
        # 把上下文扁平化到 locals
        for key, val in context.items():
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    safe_locals[f"{key}_{k2}"] = v2
            safe_locals[key] = val
        return bool(_safe_wf_eval(resolved, safe_locals))
    except Exception as e:
        logger.warning("条件表达式求值失败: %s → %s", expr, e)
        return False


# ─────────────────────────────────────────────
# 工作流引擎
# ─────────────────────────────────────────────


def _safe_wf_eval(expression: str, context: dict) -> bool:
    """工作流条件安全求值，替代eval()。支持比较/逻辑/属性/索引/数学运算。"""
    import ast, operator
    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.Mod: operator.mod,
    }
    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in context: return context[node.id]
            if node.id == 'True': return True
            if node.id == 'False': return False
            if node.id == 'None': return None
            raise ValueError(f'未定义: {node.id}')
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = _eval(comp)
                if isinstance(op, ast.Eq):
                    if not (left == right): return False
                elif isinstance(op, ast.NotEq):
                    if not (left != right): return False
                elif isinstance(op, ast.Lt):
                    if not (left < right): return False
                elif isinstance(op, ast.LtE):
                    if not (left <= right): return False
                elif isinstance(op, ast.Gt):
                    if not (left > right): return False
                elif isinstance(op, ast.GtE):
                    if not (left >= right): return False
                elif isinstance(op, ast.In):
                    if not (left in right): return False
                elif isinstance(op, ast.NotIn):
                    if not (left not in right): return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            results = [_eval(v) for v in node.values]
            return all(results) if isinstance(node.op, ast.And) else any(results)
        if isinstance(node, ast.UnaryOp):
            val = _eval(node.operand)
            if isinstance(node.op, ast.Not): return not val
            if isinstance(node.op, ast.USub): return -val
        if isinstance(node, ast.BinOp):
            return ops.get(type(node.op), lambda a, b: a + b)(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.Attribute):
            obj = _eval(node.value)
            return getattr(obj, node.attr)
        if isinstance(node, ast.Subscript):
            obj = _eval(node.value)
            key = _eval(node.slice)
            return obj[key]
        raise ValueError(f'不支持: {ast.dump(node)}')
    tree = ast.parse(expression, mode='eval')
    return _eval(tree.body)

class WorkflowEngine:
    """工作流执行引擎

    注册 action handler，然后执行 Workflow。
    支持顺序、条件分支、循环、并行。

    示例:
        engine = WorkflowEngine()

        @engine.action("greet")
        def greet(params, ctx):
            return f"Hello, {params.get('name', 'World')}!"

        workflow = Workflow(name="demo", steps=[
            Step(name="say_hi", action="greet", params={"name": "Alice"})
        ])
        result = engine.execute(workflow)
    """

    def __init__(self) -> None:
        self._actions: Dict[str, Callable] = {}
        self._middleware: List[Callable] = []

    # ── 注册 action ──

    def register_action(self, name: str, handler: Callable) -> None:
        """注册动作处理函数

        Args:
            name: 动作名
            handler: 函数签名 (params: Dict, context: Dict) -> Any
        """
        self._actions[name] = handler

    def action(self, name: str) -> Callable:
        """装饰器方式注册动作

        Args:
            name: 动作名

        Returns:
            装饰器
        """
        def decorator(fn: Callable) -> Callable:
            self.register_action(name, fn)
            return fn
        return decorator

    def add_middleware(self, fn: Callable) -> None:
        """添加执行中间件

        中间件签名: (step, params, context, next_fn) -> Any
        """
        self._middleware.append(fn)

    # ── 执行工作流 ──

    def execute(self, workflow: Workflow, initial_vars: Optional[Dict[str, Any]] = None) -> WorkflowResult:
        """执行工作流

        Args:
            workflow: 工作流定义
            initial_vars: 初始变量

        Returns:
            WorkflowResult 执行结果
        """
        start_time = time.time()
        context: Dict[str, Any] = {"vars": initial_vars or {}}
        wf_result = WorkflowResult(workflow_name=workflow.name)

        try:
            self._run_steps(workflow.steps, context, wf_result)
        except _WorkflowAbort as e:
            wf_result.success = False
            logger.error("工作流 '%s' 中止: %s", workflow.name, e)

        wf_result.duration = time.time() - start_time
        wf_result.variables = context.get("vars", {})
        return wf_result

    # ── 内部执行逻辑 ──

    def _run_steps(
        self,
        steps: List[Step],
        context: Dict[str, Any],
        wf_result: WorkflowResult,
    ) -> None:
        """按列表顺序执行步骤，支持跳转"""
        step_map: Dict[str, int] = {s.name: i for i, s in enumerate(steps)}
        idx = 0

        while idx < len(steps):
            step = steps[idx]
            step_result = self._execute_step(step, context)
            wf_result.step_results.append(step_result)

            # 记录到上下文
            context[step.name] = {
                "result": step_result.result,
                "status": step_result.status.value,
                "error": step_result.error,
            }

            if step_result.status == StepStatus.FAILED:
                wf_result.success = False
                if step.next_on_failure:
                    if step.next_on_failure in step_map:
                        idx = step_map[step.next_on_failure]
                        continue
                    else:
                        raise _WorkflowAbort(
                            f"失败跳转目标 '{step.next_on_failure}' 不存在"
                        )
                # 没有失败跳转 → 中止
                raise _WorkflowAbort(
                    f"步骤 '{step.name}' 失败: {step_result.error}"
                )

            if step_result.status == StepStatus.SUCCESS and step.next_on_success:
                if step.next_on_success in step_map:
                    idx = step_map[step.next_on_success]
                    continue
                else:
                    raise _WorkflowAbort(
                        f"成功跳转目标 '{step.next_on_success}' 不存在"
                    )

            idx += 1

    def _execute_step(self, step: Step, context: Dict[str, Any]) -> StepResult:
        """执行单个步骤（含条件判断、重试、超时）"""
        sr = StepResult(step_name=step.name)
        start = time.time()

        # 条件检查
        if step.condition:
            if not _eval_condition(step.condition, context):
                sr.status = StepStatus.SKIPPED
                sr.duration = time.time() - start
                return sr

        try:
            if step.flow_control == FlowControl.PARALLEL:
                sr.result = self._run_parallel(step.sub_steps, context)
                sr.status = StepStatus.SUCCESS
            elif step.flow_control == FlowControl.LOOP_FOR:
                sr.result = self._run_loop_for(step, context)
                sr.status = StepStatus.SUCCESS
            elif step.flow_control == FlowControl.LOOP_WHILE:
                sr.result = self._run_loop_while(step, context)
                sr.status = StepStatus.SUCCESS
            elif step.flow_control == FlowControl.CONDITIONAL:
                sr.result = self._run_conditional(step, context)
                sr.status = StepStatus.SUCCESS
            else:
                sr.result = self._invoke_action(step, context)
                sr.status = StepStatus.SUCCESS
        except Exception as e:
            # 重试逻辑
            for attempt in range(1, step.max_retries + 1):
                sr.retries = attempt
                try:
                    sr.result = self._invoke_action(step, context)
                    sr.status = StepStatus.SUCCESS
                    break
                except Exception:
                    continue
            else:
                sr.status = StepStatus.FAILED
                sr.error = str(e)

        sr.duration = time.time() - start
        return sr

    def _invoke_action(self, step: Step, context: Dict[str, Any]) -> Any:
        """调用注册的 action handler"""
        handler = self._actions.get(step.action)
        if handler is None:
            raise ValueError(f"未注册的 action: '{step.action}'")

        params = substitute_vars(step.params, context)

        # 中间件链
        def call_handler(p: Dict, c: Dict) -> Any:
            return handler(p, c)

        fn = call_handler
        for mw in reversed(self._middleware):
            prev_fn = fn
            def make_next(prev: Callable, middleware: Callable) -> Callable:
                def wrapped(p: Dict, c: Dict) -> Any:
                    return middleware(step, p, c, prev)
                return wrapped
            fn = make_next(prev_fn, mw)

        return fn(params, context)

    def _run_parallel(self, sub_steps: List[Step], context: Dict[str, Any]) -> List[Any]:
        """并行执行子步骤"""
        results: List[Any] = [None] * len(sub_steps)
        errors: List[Optional[str]] = [None] * len(sub_steps)

        def worker(idx: int, s: Step) -> None:
            try:
                results[idx] = self._invoke_action(s, context)
            except Exception as e:
                errors[idx] = str(e)

        threads = []
        for i, s in enumerate(sub_steps):
            t = threading.Thread(target=worker, args=(i, s), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 检查错误
        for i, err in enumerate(errors):
            if err:
                raise RuntimeError(
                    f"并行步骤 '{sub_steps[i].name}' 失败: {err}"
                )

        return results

    def _run_loop_for(self, step: Step, context: Dict[str, Any]) -> List[Any]:
        """执行 for 循环"""
        items = step.loop_items
        if isinstance(items, str):
            items = substitute_vars(items, context)
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"loop_items 必须是列表，实际: {type(items)}")

        results = []
        for item in items:
            context["vars"][step.loop_var] = item
            for sub in step.sub_steps:
                r = self._invoke_action(sub, context)
                results.append(r)
        return results

    def _run_loop_while(self, step: Step, context: Dict[str, Any]) -> List[Any]:
        """执行 while 循环"""
        results = []
        max_iterations = 10000  # 防无限循环
        count = 0

        while count < max_iterations:
            if step.loop_condition and not _eval_condition(step.loop_condition, context):
                break
            for sub in step.sub_steps:
                r = self._invoke_action(sub, context)
                results.append(r)
            count += 1

        return results

    def _run_conditional(self, step: Step, context: Dict[str, Any]) -> Any:
        """执行条件分支

        sub_steps[0] = if 分支（用 step.condition）
        sub_steps[1] = else 分支（可选）
        """
        if step.condition and _eval_condition(step.condition, context):
            if len(step.sub_steps) > 0:
                return self._invoke_action(step.sub_steps[0], context)
        else:
            if len(step.sub_steps) > 1:
                return self._invoke_action(step.sub_steps[1], context)
        return None

    # ── 从 dict/YAML 构建 ──

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> Workflow:
        """从字典构建工作流

        Args:
            data: 工作流定义字典，格式:
                {
                    "name": "...",
                    "description": "...",
                    "steps": [
                        {
                            "name": "...",
                            "action": "...",
                            "params": {...},
                            "next_on_success": "...",
                            "next_on_failure": "...",
                            "condition": "...",
                            "flow_control": "sequential|parallel|...",
                            "sub_steps": [...],
                            "max_retries": 0,
                            "timeout": 0
                        }
                    ]
                }

        Returns:
            Workflow 实例
        """
        def parse_step(s: Dict) -> Step:
            fc_str = s.get("flow_control", "sequential")
            fc_map = {
                "sequential": FlowControl.SEQUENTIAL,
                "conditional": FlowControl.CONDITIONAL,
                "loop_while": FlowControl.LOOP_WHILE,
                "loop_for": FlowControl.LOOP_FOR,
                "parallel": FlowControl.PARALLEL,
            }
            fc = fc_map.get(fc_str, FlowControl.SEQUENTIAL)

            sub = [parse_step(ss) for ss in s.get("sub_steps", [])]

            return Step(
                name=s["name"],
                action=s.get("action", ""),
                params=s.get("params", {}),
                next_on_success=s.get("next_on_success"),
                next_on_failure=s.get("next_on_failure"),
                condition=s.get("condition"),
                flow_control=fc,
                sub_steps=sub,
                loop_items=s.get("loop_items"),
                loop_var=s.get("loop_var", "item"),
                loop_condition=s.get("loop_condition"),
                max_retries=s.get("max_retries", 0),
                timeout=s.get("timeout", 0),
            )

        steps = [parse_step(s) for s in data.get("steps", [])]
        return Workflow(
            name=data.get("name", "unnamed"),
            steps=steps,
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


class _WorkflowAbort(Exception):
    """工作流中止异常（内部使用）"""
    pass
