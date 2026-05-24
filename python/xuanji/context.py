"""
xuanji 上下文管理引擎

核心策略（老大定的方向）：
  每次任务 = 新对话（干净上下文）
  + 检索注入长期记忆中的"重点"（精准信息包）
  + 任务结束写入长期记忆（终点沉淀）

把大模型的上下文窗口从"无限堆叠的聊天记录"
变成"按需加载的精准信息包"。

三步流程：
  1. 任务开始 → 检索相关记忆 → 注入系统提示
  2. 任务执行 → 工作记忆全量保留
  3. 任务结束 → 结构化摘要 → 写入长期记忆
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskContext:
    """单个任务的上下文——每次任务一个新实例"""
    
    task_id: str = ""
    task_description: str = ""
    agent_name: str = ""
    
    # 系统提示（固定部分 + 检索注入部分）
    system_prompt: str = ""
    injected_memories: List[Dict] = field(default_factory=list)
    
    # 工作记忆（当前任务的完整上下文）
    messages: List[Dict] = field(default_factory=list)
    
    # 任务状态
    started_at: float = 0.0
    finished_at: float = 0.0
    status: str = "pending"  # pending/running/done/failed
    result: str = ""
    
    # Token统计
    tokens_used: int = 0
    
    def add_message(self, role: str, content: str):
        """添加消息到工作记忆"""
        self.messages.append({
            "role": role,
            "content": content,
            "ts": time.time()
        })
    
    def get_llm_messages(self) -> List[Dict]:
        """获取发给LLM的消息列表（系统提示+工作记忆）"""
        result = []
        
        # 系统提示（含注入的记忆）
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        
        # 工作记忆（只要role和content）
        for msg in self.messages:
            result.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        return result
    
    def to_summary(self) -> Dict:
        """生成结构化摘要（写入长期记忆用）"""
        duration = self.finished_at - self.started_at if self.finished_at else 0
        return {
            "task": self.task_description,
            "agent": self.agent_name,
            "status": self.status,
            "result": self.result[:500],  # 截断
            "duration_sec": round(duration, 1),
            "tokens": self.tokens_used,
            "message_count": len(self.messages),
            "ts": self.started_at,
        }


class ContextEngine:
    """上下文管理引擎
    
    管理任务的完整生命周期：
      开始 → 检索注入 → 执行 → 终点写入
    
    与记忆系统配合：
      memory.search() → 检索相关经验
      memory.store()  → 沉淀新经验
    """
    
    def __init__(self, memory_store=None, governor=None):
        """
        Args:
            memory_store: 记忆系统实例（MemoryStore）
            governor: Token治理实例（TokenGovernor）
        """
        self.memory = memory_store
        self.governor = governor
        
        # 活跃任务
        self._active_tasks: Dict[str, TaskContext] = {}
        
        # 配置
        self.max_inject_memories = 5       # 最多注入几条记忆
        self.min_relevance_score = 0.3     # 最低相关度
        self.summary_importance = 6        # 摘要存入记忆的重要度
        self.experience_importance = 8     # 经验教训的重要度
    
    async def start_task(self, task_id: str, description: str,
                         agent_name: str = "default",
                         base_system_prompt: str = "",
                         extra_context: str = "") -> TaskContext:
        """任务开始——创建干净上下文 + 检索注入记忆
        
        这是整个策略的第一步：
        1. 创建新的TaskContext（干净上下文）
        2. 从长期记忆检索与当前任务相关的经验
        3. 把经验注入到系统提示里
        4. 返回准备好的上下文
        
        Args:
            task_id: 任务唯一ID
            description: 任务描述（用于检索相关记忆）
            agent_name: 执行Agent名
            base_system_prompt: 基础系统提示（Agent角色定义等）
            extra_context: 额外上下文（用户偏好等）
        
        Returns:
            准备好的TaskContext
        """
        ctx = TaskContext(
            task_id=task_id,
            task_description=description,
            agent_name=agent_name,
            started_at=time.time(),
            status="running",
        )
        
        # === 检索相关记忆 ===
        injected_text = ""
        if self.memory:
            # 用任务描述搜索相关经验
            memories = await self.memory.search(
                description,
                limit=self.max_inject_memories
            )
            
            if memories:
                ctx.injected_memories = memories
                
                # 格式化注入文本
                memory_lines = []
                for m in memories:
                    content = m.get("content", "")
                    importance = m.get("importance", 0)
                    tags = m.get("tags", "")
                    memory_lines.append(f"- [{importance}] {content}")
                
                injected_text = (
                    "\n\n## 相关历史经验（从长期记忆检索）\n"
                    + "\n".join(memory_lines)
                )
        
        # === 组装系统提示 ===
        parts = []
        if base_system_prompt:
            parts.append(base_system_prompt)
        if extra_context:
            parts.append(extra_context)
        if injected_text:
            parts.append(injected_text)
        
        ctx.system_prompt = "\n".join(parts)
        
        # 注册活跃任务
        self._active_tasks[task_id] = ctx
        
        return ctx
    
    async def finish_task(self, task_id: str, result: str = "",
                          status: str = "done",
                          lessons: List[str] = None) -> Dict:
        """任务结束——生成摘要 + 写入长期记忆
        
        这是策略的第三步：
        1. 标记任务完成
        2. 生成结构化摘要
        3. 写入长期记忆（WAL防丢失）
        4. 如果有经验教训，以更高优先级存入
        
        Args:
            task_id: 任务ID
            result: 任务结果
            status: 完成状态（done/failed）
            lessons: 经验教训列表（可选）
        
        Returns:
            任务摘要
        """
        ctx = self._active_tasks.get(task_id)
        if not ctx:
            return {"error": "task not found"}
        
        # 标记完成
        ctx.finished_at = time.time()
        ctx.status = status
        ctx.result = result
        
        # 生成摘要
        summary = ctx.to_summary()
        
        # === 写入长期记忆 ===
        if self.memory:
            # 1. 任务摘要
            summary_text = (
                f"[{status}] {ctx.task_description} → {result[:200]}"
            )
            tags = ["task_summary", status, ctx.agent_name]
            await self.memory.store(
                summary_text,
                importance=self.summary_importance,
                tags=tags
            )
            
            # 2. 经验教训（更高优先级）
            if lessons:
                for lesson in lessons:
                    await self.memory.store(
                        f"[经验] {lesson}",
                        importance=self.experience_importance,
                        tags=["lesson", ctx.agent_name]
                    )
            
            # 3. 失败任务额外记录原因
            if status == "failed":
                await self.memory.store(
                    f"[失败] {ctx.task_description}: {result[:300]}",
                    importance=self.experience_importance,
                    tags=["failure", ctx.agent_name]
                )
        
        # Token统计
        if self.governor and ctx.tokens_used > 0:
            self.governor.record_usage(
                "context_engine",
                ctx.tokens_used, 0,
                agent_name=ctx.agent_name
            )
        
        # 清理
        del self._active_tasks[task_id]
        
        return summary
    
    async def auto_compress(self, ctx: TaskContext, 
                            max_messages: int = 20,
                            keep_recent: int = 5) -> TaskContext:
        """自动压缩上下文——当消息太多时
        
        策略：
        - 保留最近N条完整消息
        - 旧消息生成摘要替换
        - 系统提示不动
        
        Args:
            ctx: 任务上下文
            max_messages: 超过多少条触发压缩
            keep_recent: 保留最近几条
        """
        if len(ctx.messages) <= max_messages:
            return ctx  # 不需要压缩
        
        # 分离：旧消息 + 最近消息
        old_messages = ctx.messages[:-keep_recent]
        recent_messages = ctx.messages[-keep_recent:]
        
        # 旧消息生成摘要
        old_text = "\n".join([
            f"[{m['role']}] {m['content'][:100]}"
            for m in old_messages
        ])
        
        summary = f"[上下文摘要: 之前{len(old_messages)}条对话的要点]\n{old_text[:500]}"
        
        # 替换
        ctx.messages = [
            {"role": "system", "content": summary, "ts": time.time()}
        ] + recent_messages
        
        return ctx
    
    def get_active_tasks(self) -> Dict[str, Dict]:
        """获取所有活跃任务"""
        return {
            tid: {
                "description": ctx.task_description,
                "agent": ctx.agent_name,
                "status": ctx.status,
                "messages": len(ctx.messages),
                "duration": time.time() - ctx.started_at,
                "injected_memories": len(ctx.injected_memories),
            }
            for tid, ctx in self._active_tasks.items()
        }


class MemoryAgent:
    """记忆Agent——专职管理长期记忆
    
    老大的设计：
      执行Agent的上下文永远干净
      记忆Agent负责"挑重点"
      两者通过消息总线协同
    
    职责：
    1. 任务开始时：检索最相关的历史经验
    2. 任务执行中：不干预（执行Agent自己管工作记忆）
    3. 任务结束时：提取经验、生成摘要、写入长期记忆
    4. 定期维护：记忆整理、过期清理、重要度调整
    """
    
    def __init__(self, memory_store, llm_adapter=None):
        self.memory = memory_store
        self.llm = llm_adapter  # 可选：用LLM生成更好的摘要
    
    async def retrieve_for_task(self, task_description: str,
                                agent_name: str = "",
                                limit: int = 5) -> List[Dict]:
        """为任务检索相关记忆
        
        检索策略：
        1. 语义检索（关键词匹配）
        2. 按标签过滤（同类型任务的经验）
        3. 按时间衰减（近期经验权重更高）
        4. 按重要度排序
        """
        results = await self.memory.search(task_description, limit=limit * 2)
        
        if not results:
            return []
        
        # 按重要度+时间综合排序
        now = time.time()
        scored = []
        for m in results:
            importance = m.get("importance", 5)
            ts = m.get("created_at", now)
            
            # 时间衰减：7天内权重1.0，30天0.7，90天0.4，更久0.2
            if isinstance(ts, str):
                age_days = 0  # 无法解析时不衰减
            else:
                age_days = (now - ts) / 86400
            
            if age_days < 7:
                time_weight = 1.0
            elif age_days < 30:
                time_weight = 0.7
            elif age_days < 90:
                time_weight = 0.4
            else:
                time_weight = 0.2
            
            # 综合分 = 重要度 × 时间权重
            score = importance * time_weight
            scored.append((score, m))
        
        # 按分数降序
        scored.sort(key=lambda x: x[0], reverse=True)
        
        return [m for _, m in scored[:limit]]
    
    async def extract_and_store(self, task_ctx: TaskContext,
                                 result: str = "",
                                 success: bool = True):
        """从任务中提取经验并存入长期记忆
        
        提取内容：
        - 任务目标和结果（摘要级别）
        - 成功/失败的原因
        - 可复用的操作模式
        - 用户偏好（如果发现了）
        """
        # 基础摘要
        summary = (
            f"{'[成功]' if success else '[失败]'} "
            f"{task_ctx.task_description}: {result[:200]}"
        )
        
        importance = 6 if success else 8  # 失败经验更重要
        tags = ["experience", task_ctx.agent_name]
        if not success:
            tags.append("failure")
        
        await self.memory.store(summary, importance=importance, tags=tags)
        
        # 如果有LLM，生成更智能的摘要
        if self.llm and len(task_ctx.messages) > 2:
            try:
                # 用LLM提取关键经验
                conversation = "\n".join([
                    f"[{m['role']}] {m['content'][:200]}"
                    for m in task_ctx.messages[-10:]  # 最近10条
                ])
                
                extract_prompt = (
                    "从以下任务对话中提取1-3条关键经验教训，"
                    "每条一行，简洁实用：\n\n"
                    f"任务: {task_ctx.task_description}\n"
                    f"结果: {result[:200]}\n"
                    f"对话:\n{conversation[:1000]}"
                )
                
                lessons_text = await self.llm.chat(
                    [{"role": "user", "content": extract_prompt}],
                    max_tokens=200
                )
                
                if lessons_text:
                    for line in str(lessons_text).strip().split("\n"):
                        line = line.strip().lstrip("-•·0123456789. ")
                        if line and len(line) > 5:
                            await self.memory.store(
                                f"[经验] {line}",
                                importance=self.experience_importance(success),
                                tags=["lesson", "llm_extracted"]
                            )
            except Exception:
                pass  # LLM提取失败不影响基础摘要
    
    def experience_importance(self, success: bool) -> int:
        """经验的重要度"""
        return 7 if success else 9  # 失败教训更重要
    
    async def consolidate(self):
        """定期整理记忆
        
        - L1→L2→L3沉淀
        - 合并重复记忆
        - 降低过时记忆的重要度
        """
        if hasattr(self.memory, 'consolidate'):
            await self.memory.consolidate()
