"""
xuanji 调试器

用法 (CLI):
  xuanji debug start          启动调试会话
  xuanji debug step           执行一步
  xuanji debug continue       继续到下一个断点
  xuanji debug break <step>   设置断点
  xuanji debug inspect        查看当前状态
  xuanji debug vars           查看变量
  xuanji debug history        查看执行历史
  xuanji debug stop           停止调试

用法 (API):
  from xuanji.debugger import AgentDebugger
  dbg = AgentDebugger()
  dbg.break_at("step_name")
  dbg.step()
  dbg.inspect()
"""

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional


class Breakpoint:
    """断点"""

    def __init__(self, step_name: str, condition: str = None):
        self.step_name = step_name
        self.condition = condition
        self.enabled = True
        self.hit_count = 0

    def should_break(self, context: dict) -> bool:
        if not self.enabled:
            return False
        if self.condition:
            try:
                return bool(_safe_debug_eval(self.condition, context))
            except Exception:
                return False
        return True



def _safe_debug_eval(condition: str, context: dict) -> bool:
    """安全的调试条件求值，替代eval()。支持比较/逻辑/属性访问/索引访问。"""
    import ast
    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in context:
                return context[node.id]
            if node.id == 'True': return True
            if node.id == 'False': return False
            if node.id == 'None': return None
            raise ValueError(f'未定义变量: {node.id}')
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
        if isinstance(node, ast.Attribute):
            obj = _eval(node.value)
            return getattr(obj, node.attr)
        if isinstance(node, ast.Subscript):
            obj = _eval(node.value)
            key = _eval(node.slice)
            return obj[key]
        raise ValueError(f'不支持: {ast.dump(node)}')
    tree = ast.parse(condition, mode='eval')
    return _eval(tree.body)

class ExecutionRecord:
    """执行记录"""

    def __init__(self, step_name: str, action: str, data: dict, timestamp: float = None):
        self.step_name = step_name
        self.action = action
        self.data = data
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "step": self.step_name,
            "action": self.action,
            "data": self.data,
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }


class AgentDebugger:
    """Agent调试器 — 支持单步执行、断点、状态检查"""

    def __init__(self):
        self.breakpoints: Dict[str, Breakpoint] = {}
        self.history: List[ExecutionRecord] = []
        self.variables: Dict[str, Any] = {}
        self.context: Dict[str, Any] = {}
        self.current_step: Optional[str] = None
        self.paused = False
        self.session_id = f"debug-{int(time.time())}"
        self._step_queue: List[dict] = []
        self._step_index = 0
        self._running = False

    # ============================================================
    # 核心调试操作
    # ============================================================

    def step(self) -> dict:
        """执行一步，返回当前状态"""
        if not self._running and self._step_queue:
            self._running = True

        if self._step_index >= len(self._step_queue):
            self._running = False
            return {"status": "completed", "message": "所有步骤执行完毕"}

        step = self._step_queue[self._step_index]
        self.current_step = step.get("name", f"step-{self._step_index}")

        # 检查断点
        bp = self.breakpoints.get(self.current_step)
        if bp and bp.should_break(self.context):
            bp.hit_count += 1
            self.paused = True
            self._record("breakpoint_hit", {"step": self.current_step})
            return {
                "status": "paused",
                "reason": "breakpoint",
                "step": self.current_step,
                "hit_count": bp.hit_count,
            }

        # 执行步骤
        try:
            result = self._execute_step(step)
            self._record("step_executed", {"step": self.current_step, "result": result})
            self._step_index += 1
            return {"status": "stepped", "step": self.current_step, "result": result}
        except Exception as e:
            self._record("error", {"step": self.current_step, "error": str(e)})
            self.paused = True
            return {"status": "error", "step": self.current_step, "error": str(e)}

    def continue_to_next(self) -> dict:
        """继续执行到下一个断点或结束"""
        self.paused = False
        results = []

        while self._step_index < len(self._step_queue):
            result = self.step()
            results.append(result)

            if result["status"] in ("paused", "error", "completed"):
                break

        return {
            "status": results[-1]["status"] if results else "completed",
            "steps_executed": len(results),
            "results": results,
        }

    def break_at(self, step_name: str, condition: str = None) -> Breakpoint:
        """在指定步骤设置断点"""
        bp = Breakpoint(step_name, condition)
        self.breakpoints[step_name] = bp
        return bp

    def remove_break(self, step_name: str):
        """移除断点"""
        self.breakpoints.pop(step_name, None)

    def list_breakpoints(self) -> List[dict]:
        """列出所有断点"""
        result = []
        for name, bp in self.breakpoints.items():
            result.append({
                "step": name,
                "enabled": bp.enabled,
                "condition": bp.condition,
                "hits": bp.hit_count,
            })
        return result

    def toggle_break(self, step_name: str):
        """切换断点开关"""
        bp = self.breakpoints.get(step_name)
        if bp:
            bp.enabled = not bp.enabled
            return bp.enabled
        return None

    # ============================================================
    # 状态检查
    # ============================================================

    def inspect(self) -> dict:
        """查看当前调试状态"""
        return {
            "session": self.session_id,
            "current_step": self.current_step,
            "step_index": self._step_index,
            "total_steps": len(self._step_queue),
            "paused": self.paused,
            "running": self._running,
            "context_keys": list(self.context.keys()),
            "variable_count": len(self.variables),
            "breakpoint_count": len(self.breakpoints),
            "history_length": len(self.history),
        }

    def variables(self) -> Dict[str, Any]:
        """查看所有变量"""
        return dict(self.variables)

    def get_variable(self, name: str, default=None):
        """获取单个变量"""
        return self.variables.get(name, default)

    def set_variable(self, name: str, value: Any):
        """设置变量"""
        self.variables[name] = value
        self._record("variable_set", {"name": name, "value": str(value)[:200]})

    def context_snapshot(self) -> dict:
        """查看当前上下文快照"""
        return {
            "keys": list(self.context.keys()),
            "data": {k: str(v)[:500] for k, v in self.context.items()},
        }

    def history(self, limit: int = 20) -> List[dict]:
        """查看执行历史"""
        recent = self.history[-limit:]
        return [r.to_dict() for r in recent]

    # ============================================================
    # 会话管理
    # ============================================================

    def load_steps(self, steps: List[dict]):
        """加载要调试的步骤队列"""
        self._step_queue = steps
        self._step_index = 0
        self._running = False
        self.paused = False
        self.history = []
        self._record("session_started", {"steps": len(steps)})

    def reset(self):
        """重置调试器"""
        self._step_queue = []
        self._step_index = 0
        self._running = False
        self.paused = False
        self.current_step = None
        self.variables = {}
        self.context = {}
        self.breakpoints = {}
        self.history = []
        self.session_id = f"debug-{int(time.time())}"

    def stop(self):
        """停止调试"""
        self._running = False
        self.paused = False
        self._record("session_stopped", {})

    # ============================================================
    # 内部方法
    # ============================================================

    def _execute_step(self, step: dict) -> Any:
        """执行单个步骤"""
        action = step.get("action", "")
        params = step.get("params", {})

        # 模拟执行（实际使用时替换为真实执行逻辑）
        if action == "llm_chat":
            return {"reply": f"[LLM回复] 处理了: {params}"}
        elif action == "tool_call":
            return {"result": f"[工具结果] {params.get('tool', '?')}"}
        elif action == "memory_read":
            return {"data": self.context.get(params.get("key", ""), None)}
        elif action == "memory_write":
            self.context[params.get("key", "")] = params.get("value", "")
            return {"ok": True}
        elif action == "channel_send":
            return {"sent": True}
        elif action == "wait":
            return {"waited": params.get("duration", 0)}
        else:
            return {"executed": action}

    def _record(self, action: str, data: dict):
        """记录执行"""
        record = ExecutionRecord(
            step_name=self.current_step or "unknown",
            action=action,
            data=data,
        )
        self.history.append(record)

        # 限制历史长度
        if len(self.history) > 1000:
            self.history = self.history[-500:]


# ============================================================
# 交互式CLI调试器
# ============================================================

class InteractiveDebugger:
    """交互式调试器 — 提供REPL界面"""

    COMMANDS = {
        "step": "执行一步 (s)",
        "continue": "继续到下一断点 (c)",
        "break": "设置断点: break <step_name>",
        "remove": "移除断点: remove <step_name>",
        "list": "列出断点",
        "inspect": "查看状态 (i)",
        "vars": "查看变量 (v)",
        "set": "设置变量: set <name> <value>",
        "context": "查看上下文",
        "history": "查看历史 (h) [limit]",
        "quit": "退出 (q)",
        "help": "帮助",
    }

    def __init__(self, debugger: AgentDebugger = None):
        self.dbg = debugger or AgentDebugger()

    def run(self):
        """启动交互调试"""
        print(f"🔍 xuanji Debugger — Session: {self.dbg.session_id}")
        print("输入 'help' 查看命令列表")
        print()

        while True:
            try:
                prompt = f"debug({self.dbg.current_step or 'init'})> "
                line = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 调试器退出")
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            # 别名
            if cmd in ("s", "step"):
                self._cmd_step()
            elif cmd in ("c", "cont", "continue"):
                self._cmd_continue()
            elif cmd in ("b", "break"):
                self._cmd_break(args)
            elif cmd in ("rm", "remove"):
                self._cmd_remove(args)
            elif cmd in ("l", "list", "breakpoints"):
                self._cmd_list_breaks()
            elif cmd in ("i", "inspect"):
                self._cmd_inspect()
            elif cmd in ("v", "vars"):
                self._cmd_vars()
            elif cmd == "set":
                self._cmd_set(args)
            elif cmd == "context":
                self._cmd_context()
            elif cmd in ("h", "history"):
                limit = int(args[0]) if args else 20
                self._cmd_history(limit)
            elif cmd in ("q", "quit", "exit"):
                print("👋 调试器退出")
                break
            elif cmd == "help":
                self._cmd_help()
            else:
                print(f"❌ 未知命令: {cmd}")
                print("   输入 'help' 查看可用命令")

    def _cmd_step(self):
        result = self.dbg.step()
        status = result.get("status", "")
        if status == "paused":
            print(f"⏸️  断点命中: {result['step']} (第{result['hit_count']}次)")
        elif status == "stepped":
            print(f"➡️  执行: {result['step']}")
            if result.get("result"):
                print(f"   结果: {json.dumps(result['result'], ensure_ascii=False)[:200]}")
        elif status == "error":
            print(f"❌ 错误: {result['step']} — {result['error']}")
        elif status == "completed":
            print("✅ 所有步骤执行完毕")

    def _cmd_continue(self):
        result = self.dbg.continue_to_next()
        steps = result.get("steps_executed", 0)
        status = result.get("status", "")
        print(f"▶️  执行了 {steps} 步，状态: {status}")

    def _cmd_break(self, args):
        if not args:
            print("用法: break <step_name> [condition]")
            return
        name = args[0]
        condition = " ".join(args[1:]) if len(args) > 1 else None
        bp = self.dbg.break_at(name, condition)
        print(f"🔖 断点已设置: {name}" + (f" (条件: {condition})" if condition else ""))

    def _cmd_remove(self, args):
        if not args:
            print("用法: remove <step_name>")
            return
        self.dbg.remove_break(args[0])
        print(f"🗑️  断点已移除: {args[0]}")

    def _cmd_list_breaks(self):
        breaks = self.dbg.list_breakpoints()
        if not breaks:
            print("📭 暂无断点")
            return
        print(f"🔖 断点列表 ({len(breaks)}个):")
        for bp in breaks:
            status = "✅" if bp["enabled"] else "⏸️"
            print(f"  {status} {bp['step']} (命中: {bp['hits']})" +
                  (f" [条件: {bp['condition']}]" if bp['condition'] else ""))

    def _cmd_inspect(self):
        info = self.dbg.inspect()
        print("📊 调试状态:")
        print(f"  会话: {info['session']}")
        print(f"  当前步骤: {info['current_step'] or '无'}")
        print(f"  进度: {info['step_index']}/{info['total_steps']}")
        print(f"  状态: {'⏸️ 暂停' if info['paused'] else '▶️ 运行中' if info['running'] else '⏹️ 停止'}")
        print(f"  上下文变量: {info['context_keys']}")
        print(f"  调试变量: {info['variable_count']}")
        print(f"  断点: {info['breakpoint_count']}")
        print(f"  历史记录: {info['history_length']}")

    def _cmd_vars(self):
        vars_dict = self.dbg.variables()
        if not vars_dict:
            print("📭 暂无变量")
            return
        print(f"📦 变量 ({len(vars_dict)}个):")
        for name, value in vars_dict.items():
            val_str = str(value)[:100]
            print(f"  {name} = {val_str}")

    def _cmd_set(self, args):
        if len(args) < 2:
            print("用法: set <name> <value>")
            return
        name = args[0]
        value = " ".join(args[1:])
        # 尝试解析为JSON
        try:
            value = json.loads(value)
        except Exception:
            pass
        self.dbg.set_variable(name, value)
        print(f"✅ 变量已设置: {name} = {value}")

    def _cmd_context(self):
        snap = self.dbg.context_snapshot()
        if not snap["keys"]:
            print("📭 上下文为空")
            return
        print(f"📋 上下文 ({len(snap['keys'])}个键):")
        for k, v in snap["data"].items():
            print(f"  {k} = {v}")

    def _cmd_history(self, limit=20):
        records = self.dbg.history(limit)
        if not records:
            print("📭 无执行历史")
            return
        print(f"📜 执行历史 (最近{len(records)}条):")
        for r in records:
            print(f"  [{r['time']}] {r['step']} → {r['action']}")

    def _cmd_help(self):
        print("📖 调试器命令:")
        for cmd, desc in self.COMMANDS.items():
            print(f"  {cmd:12s} {desc}")
