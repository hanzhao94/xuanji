"""
xuanji 自然语言Agent

ReAct循环：观察→思考→行动→观察→输出
零外部依赖，纯玄玑内部工具。

示例:
    runner = AgentRunner(llm_router)
    result = runner.run("帮我查一下今天北京的天气")
"""

import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class AgentStep:
    """单步执行记录"""
    step_num: int
    thought: str       # LLM的思考
    action: str        # 动作名
    action_input: dict # 动作参数
    observation: str   # 执行结果
    error: Optional[str] = None


@dataclass
class AgentResult:
    """最终结果"""
    success: bool
    answer: str             # 给用户的回答
    steps: List[AgentStep]  # 执行过程
    total_steps: int
    elapsed: float          # 耗时(秒)
    tokens_used: int = 0


# ─────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────

class ToolRegistry:
    """工具注册表
    
    每个工具 = 名称 + 描述 + 参数schema + 执行函数
    """
    def __init__(self):
        self._tools: Dict[str, dict] = {}
    
    def register(self, name: str, description: str, 
                 params: dict, func: Callable, category: str = "general"):
        """注册一个工具
        
        Args:
            name: 工具名（英文，如 web_search）
            description: 工具描述（给LLM看的）
            params: JSON Schema格式的参数定义
            func: 执行函数，签名 func(**kwargs) -> str
            category: 分类
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "params": params,
            "func": func,
            "category": category,
        }
        logger.debug(f"Tool registered: {name}")
    
    def get(self, name: str) -> Optional[dict]:
        return self._tools.get(name)
    
    def list_all(self) -> List[dict]:
        return list(self._tools.values())
    
    def to_prompt(self) -> str:
        """生成工具描述文本，注入到LLM prompt（紧凑格式）"""
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t['name']}: {t['description']}")
        return "\n".join(lines)
    
    def execute(self, name: str, **kwargs) -> str:
        """执行工具"""
        tool = self._tools.get(name)
        if not tool:
            return f"错误：工具 '{name}' 不存在"
        try:
            result = tool["func"](**kwargs)
            if isinstance(result, str):
                # 截断过长结果
                if len(result) > 4000:
                    return result[:4000] + "\n...（结果已截断，共{}字）".format(len(result))
                return result
            return str(result)
        except Exception as e:
            return f"错误：执行 '{name}' 失败 - {type(e).__name__}: {e}"
    
    @property
    def tool_names(self) -> List[str]:
        return list(self._tools.keys())


# ─────────────────────────────────────────────
# 系统Prompt模板
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个智能助手。你需要根据用户的指令，一步步思考并执行任务。

## 输出格式（铁律！）
你必须且只能输出以下JSON格式，不要输出任何其他文字：
{{"thought": "思考", "action": "工具名", "action_input": {{参数}}, "answer": "完成时填写，否则空字符串"}}

## 工作规则
1. 每次只调用一个工具
2. 根据结果决定下一步
3. 信息足够时立即填写answer字段
4. 只用JSON回复，不要markdown代码块，不要额外文字

## 可用工具
{tools}

## 注意
- 不要编造结果
- 工具失败就换其他方式
"""


# ─────────────────────────────────────────────
# AgentRunner
# ─────────────────────────────────────────────

class AgentRunner:
    """自然语言Agent执行引擎
    
    ReAct循环：观察→思考→行动→观察→输出
    """
    
    def __init__(self, llm_router, tool_registry: Optional[ToolRegistry] = None,
                 max_steps: int = 15, model: Optional[str] = None,
                 system_prompt: Optional[str] = None):
        """
        Args:
            llm_router: LLMRouter实例
            tool_registry: 工具注册表（None则用空的）
            max_steps: 最大执行步数
            model: 使用的模型名
            system_prompt: 自定义系统prompt
        """
        self.llm = llm_router
        self.registry = tool_registry or ToolRegistry()
        self.max_steps = max_steps
        # 获取默认模型名
        self.model = model
        if not self.model:
            # 尝试从LLMRouter的capabilities推断
            caps = getattr(llm_router, 'capabilities', None)
            if caps:
                try:
                    caps_data = caps()
                    adapters = caps_data.get("adapters", {})
                    for name, info in adapters.items():
                        if info.get("primary"):
                            models = info.get("models", [])
                            if models:
                                self.model = models[0]
                            break
                except Exception:
                    pass
            if not self.model:
                self.model = "default"
        
        # System prompt
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._update_system_prompt()
    
    def _update_system_prompt(self):
        """更新系统prompt（注入工具列表）"""
        tools_text = self.registry.to_prompt()
        self.system_prompt = self._system_prompt.format(tools=tools_text)
    
    def register_tool(self, name: str, description: str, 
                      params: dict, func: Callable, category: str = "general"):
        """快捷注册工具"""
        self.registry.register(name, description, params, func, category)
        self._update_system_prompt()
    
    def run_sync(self, user_input: str, context: Optional[str] = None,
                 history: Optional[List[dict]] = None) -> AgentResult:
        """同步版本（兼容旧代码）
        
        Args:
            user_input: 用户输入
            context: 可选的上下文信息
            history: 可选的对话历史（用于多轮对话连续性）
        
        Returns:
            AgentResult: 执行结果
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.run(user_input, context, history))
                return future.result()
        return asyncio.run(self.run(user_input, context, history))

    async def run(self, user_input: str, context: Optional[str] = None,
                  history: Optional[List[dict]] = None) -> AgentResult:
        """执行自然语言任务（异步版本）
        
        Args:
            user_input: 用户的自然语言指令
            context: 可选的上下文信息
            history: 可选的对话历史（用于多轮对话连续性）。如果提供，将在其后追加当前用户输入。
        
        Returns:
            AgentResult: 执行结果
            
        注意：调用者应维护 history 列表以实现跨轮对话记忆。
        """
        start_time = time.time()
        steps: List[AgentStep] = []
        tokens_used = 0
        
        # 构建/恢复对话历史
        if history is not None and len(history) > 0:
            # 复用已有历史（多轮对话连续性）
            history.append({"role": "user", "content": f"用户指令：{user_input}"})
        else:
            # 全新会话（空列表或未提供）
            if history is not None:
                history.append({"role": "system", "content": self.system_prompt})
                history.append({"role": "user", "content": f"用户指令：{user_input}"})
            else:
                history = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"用户指令：{user_input}"},
                ]
        
        if context:
            history[-1]["content"] += f"\n\n上下文：{context}"
        
        step_num = 0
        final_answer = ""
        
        while step_num < self.max_steps:
            step_num += 1
            logger.info(f"[Agent] Step {step_num}/{self.max_steps}")
            
            # 调用LLM (async)
            try:
                resp = await self.llm.chat_response(history, model=self.model)
                reply = resp.content
                if resp.usage:
                    tokens_used += resp.usage.get("total_tokens", 0)
            except Exception as e:
                logger.error(f"[Agent] LLM error: {e}")
                return AgentResult(
                    success=False,
                    answer=f"执行失败：LLM调用出错 - {e}",
                    steps=steps,
                    total_steps=step_num,
                    elapsed=time.time() - start_time,
                    tokens_used=tokens_used,
                )
            
            # 解析JSON
            parsed = self._parse_llm_json(reply)
            if not parsed:
                logger.warning(f"[Agent] Step {step_num}: JSON parse failed")
                history.append({"role": "assistant", "content": reply})
                history.append({
                    "role": "user", 
                    "content": "请严格按照JSON格式回复。格式：{\"thought\": \"...\", \"action\": \"...\", \"action_input\": {...}, \"answer\": \"...\"}"
                })
                continue
            
            thought = parsed.get("thought", "")
            action = parsed.get("action", "")
            action_input = parsed.get("action_input", {})
            answer = parsed.get("answer", "")
            
            # 如果有answer，任务完成
            if answer:
                logger.info(f"[Agent] Completed at step {step_num}")
                step = AgentStep(
                    step_num=step_num,
                    thought=thought,
                    action="(final)",
                    action_input={},
                    observation=answer,
                )
                steps.append(step)
                final_answer = answer
                break
            
            # 执行工具
            if action and self.registry.get(action):
                # 执行
                result = self.registry.execute(action, **action_input)
                logger.info(f"[Agent] Action: {action}, result_len={len(result)}")
                
                step = AgentStep(
                    step_num=step_num,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    observation=result,
                )
                steps.append(step)
                
                # 添加对话历史
                history.append({"role": "assistant", "content": reply})
                history.append({
                    "role": "user",
                    "content": f"工具 '{action}' 执行结果：\n{result}\n\n请继续思考下一步。如果信息已足够回答用户，请在下一步填写answer字段。"
                })
            elif action and not self.registry.get(action):
                logger.warning(f"[Agent] Unknown tool: {action}")
                step = AgentStep(
                    step_num=step_num,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    observation=f"错误：工具 '{action}' 不存在。可用工具：{', '.join(self.registry.tool_names)}",
                    error=f"Unknown tool: {action}",
                )
                steps.append(step)
                history.append({"role": "assistant", "content": reply})
                history.append({
                    "role": "user",
                    "content": f"工具 '{action}' 不存在。可用工具：{', '.join(self.registry.tool_names)}\n请重新选择工具。"
                })
            else:
                # 没有action但有thought，可能是纯思考
                if thought:
                    history.append({"role": "assistant", "content": reply})
                    history.append({
                        "role": "user",
                        "content": "请调用工具或给出最终回答。"
                    })
        
        # 循环结束但没有answer
        if not final_answer:
            final_answer = f"任务未完成（已达最大步数{self.max_steps}）。已执行{step_num}步。"
            logger.warning(f"[Agent] Max steps reached without answer")
        
        elapsed = time.time() - start_time
        success = "任务未完成" not in final_answer and "执行失败" not in final_answer
        
        # 清理历史：移除最后一轮的assistant+user消息（它们是本轮的中间步骤）
        # 保留system + 历史对话，以便下次调用继续使用
        # 注意：history是可变的，调用者会持有引用
        
        return AgentResult(
            success=success,
            answer=final_answer,
            steps=steps,
            total_steps=step_num,
            elapsed=elapsed,
            tokens_used=tokens_used,
        )
    
    def _parse_llm_json(self, text: str) -> Optional[dict]:
        """从LLM回复中提取JSON"""
        import re
        text = text.strip()
        
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 去掉markdown代码块
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        
        # 尝试找代码块中的JSON
        match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 增强：从任意文本中提取嵌套JSON（括号匹配算法）
        # 找到第一个{和最后一个}，然后验证中间的JSON
        first_brace = text.find('{')
        if first_brace >= 0:
            # 从后往前找最后一个}
            last_brace = text.rfind('}')
            if last_brace > first_brace:
                candidate = text[first_brace:last_brace+1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
                
                # 尝试更短的JSON（可能在第一个}处就结束了）
                for i in range(len(candidate)-1, 0, -1):
                    if candidate[i] == '}':
                        short = candidate[:i+1]
                        try:
                            return json.loads(short)
                        except json.JSONDecodeError:
                            pass
        
        return None
    
    def run_and_print_sync(self, user_input: str, context: Optional[str] = None) -> AgentResult:
        """同步版本（兼容旧代码）"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.run_and_print(user_input, context))
                return future.result()
        return asyncio.run(self.run_and_print(user_input, context))

    async def run_and_print(self, user_input: str, context: Optional[str] = None) -> AgentResult:
        """执行并打印过程（异步版本）"""
        print(f"\n{'='*60}")
        print(f"🎯 任务：{user_input}")
        print(f"{'='*60}\n")
        
        result = await self.run(user_input, context)
        
        print(f"{'='*60}")
        print(f"📊 执行结果")
        print(f"{'='*60}")
        print(f"✅ 成功: {result.success}")
        print(f"📝 回答: {result.answer}")
        print(f"🔄 步数: {result.total_steps}")
        print(f"⏱ 耗时: {result.elapsed:.1f}s")
        print(f"🔤 Token: {result.tokens_used}")
        
        if result.steps:
            print(f"\n📋 执行过程:")
            for s in result.steps:
                print(f"  Step {s.step_num}:")
                print(f"    💭 {s.thought[:100]}")
                print(f"    🔧 {s.action}")
                if s.action != "(final)":
                    obs_preview = s.observation[:100]
                    print(f"    👁 {obs_preview}{'...' if len(s.observation) > 100 else ''}")
        
        return result
