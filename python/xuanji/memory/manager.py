"""
xuanji 统一记忆管理器

把 MemoryStore + MemoryGuard + ContextEngine 三者融合为一体。

单一入口管理所有记忆操作：
  存储 → 自动分级 + WAL保护 + 身份检测
  检索 → 语义匹配 + 时间衰减 + 重要度排序
  上下文 → 每次任务干净窗口 + 检索注入 + 终点写入
  维护 → 自动checkpoint + 沉淀 + 完整性校验

用户只需要：
  mgr = MemoryManager()
  ctx = await mgr.begin_task("写一个Python函数")
  # ... 任务执行 ...
  await mgr.end_task(ctx, result="完成")
"""

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xuanji.memory.store import MemoryStore
from xuanji.memory.guard import MemoryGuard


@dataclass
class TaskContext:
    """任务上下文——每次任务一个干净实例"""
    
    task_id: str = ""
    description: str = ""
    agent_name: str = "default"
    
    # 系统提示
    system_prompt: str = ""
    injected_memories: List[Dict] = field(default_factory=list)
    
    # 工作记忆（当前任务的消息）
    messages: List[Dict] = field(default_factory=list)
    
    # 状态
    started_at: float = 0.0
    finished_at: float = 0.0
    status: str = "running"
    result: str = ""
    tokens_used: int = 0
    
    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "ts": time.time()
        })
    
    def get_llm_messages(self) -> List[Dict]:
        """获取发给LLM的消息（系统提示+工作记忆）"""
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        for m in self.messages:
            msgs.append({"role": m["role"], "content": m["content"]})
        return msgs
    
    def to_summary(self) -> str:
        """生成摘要文本"""
        duration = self.finished_at - self.started_at if self.finished_at else 0
        return (
            f"[{self.status}] {self.description} "
            f"({round(duration, 1)}s, {self.tokens_used}tok) "
            f"→ {self.result[:200]}"
        )


class MemoryManager:
    """统一记忆管理器
    
    融合三大模块：
      MemoryStore  — 三级缓存存储
      MemoryGuard  — WAL防丢失+身份保护+checkpoint
      ContextEngine — 上下文管理（检索注入+终点写入）
    
    生命周期：
      startup() → 恢复WAL + 校验完整性 + 启动自动checkpoint
      begin_task() → 创建干净上下文 + 检索注入
      end_task() → 摘要 + 写入长期记忆
      shutdown() → 最终checkpoint + 关闭
    """
    
    def __init__(self, data_dir: str = None, db_name: str = "memory.db"):
        if data_dir is None:
            data_dir = os.path.join(
                os.path.expanduser("~"), ".xuanji", "data"
            )
        os.makedirs(data_dir, exist_ok=True)
        
        self.data_dir = data_dir
        db_path = os.path.join(data_dir, db_name)
        
        # 三大模块
        self._store = MemoryStore(db_path=db_path)
        self.guard = MemoryGuard(
            store=self._store,
            checkpoint_interval=300,  # 5分钟自动checkpoint
        )
        
        # 活跃任务
        self._tasks: Dict[str, TaskContext] = {}
        
        # 配置
        self.max_inject = 5           # 最多注入几条记忆
        self.summary_importance = 6    # 任务摘要的重要度
        self.lesson_importance = 8     # 经验教训的重要度
        self.failure_importance = 9    # 失败教训的重要度
        
        self._started = False
    
    async def startup(self) -> Dict:
        """启动——恢复WAL + 校验 + 自动checkpoint
        
        Returns:
            启动报告 {recovered, integrity, stats}
        """
        report = {}
        
        # 1. 恢复WAL（上次崩溃未提交的记忆）
        try:
            recovered = self.guard.recover()
            if asyncio.iscoroutine(recovered):
                recovered = await recovered
            report["recovered"] = recovered
        except Exception as e:
            report["recovered"] = {"error": str(e)}
        
        # 2. 完整性校验
        try:
            integrity = self.guard.verify_integrity()
            if asyncio.iscoroutine(integrity):
                integrity = await integrity
            report["integrity"] = integrity
        except Exception as e:
            report["integrity"] = {"status": "error", "error": str(e)}
        
        # 3. 启动自动checkpoint
        try:
            self.guard.start_auto_checkpoint()
        except Exception:
            pass
        
        # 4. 统计
        report["stats"] = self._store.stats()
        
        self._started = True
        return report
    
    async def shutdown(self):
        """关闭——最终checkpoint + 停止"""
        if not self._started:
            return
        
        # 结束所有活跃任务
        for tid in list(self._tasks.keys()):
            await self.end_task(
                self._tasks[tid],
                result="shutdown",
                status="interrupted"
            )
        
        # 最终checkpoint
        self.guard.checkpoint("shutdown")
        # guard.shutdown是async的
        try:
            result = self.guard.shutdown()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
        self._store.close()
        self._started = False
    
    # === 存储（直接代理到MemoryStore + Guard保护）===
    
    async def remember(self, content: str, importance: int = 5,
                       tags: List[str] = None, source: str = "",
                       permanent: bool = False) -> str:
        """存储记忆（带Guard保护）
        
        自动：
        - WAL写入（防崩溃丢失）
        - 身份检测（含灵明/identity等关键词自动标记permanent）
        - 按importance分级存储（L1/L2/L3）
        """
        # Guard检测身份信息
        is_identity = self.guard.protect_identity(content, tags)
        if is_identity:
            permanent = True
            importance = max(importance, 9)
        
        # 存储
        memory_id = await self._store.store(
            content, importance=importance,
            tags=tags, source=source, permanent=permanent
        )
        
        return memory_id
    
    async def search(self, query: str, limit: int = 5,
                     tags: List[str] = None,
                     min_importance: int = 0) -> List[Dict]:
        """检索记忆（带时间衰减排序）"""
        results = await self._store.search(
            query, limit=limit * 2,  # 多检索一些再筛
            tags=tags, min_importance=min_importance
        )
        
        if not results:
            return []
        
        # 时间衰减重排序
        now = time.time()
        scored = []
        for m in results:
            imp = m.get("importance", 5)
            
            # 时间衰减
            created = m.get("created_at", "")
            if isinstance(created, (int, float)):
                age_days = (now - created) / 86400
            else:
                age_days = 0
            
            if age_days < 7:
                tw = 1.0
            elif age_days < 30:
                tw = 0.7
            elif age_days < 90:
                tw = 0.4
            else:
                tw = 0.2
            
            score = imp * tw
            scored.append((score, m))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]
    
    async def forget(self, memory_id: str) -> bool:
        """删除记忆（permanent的不能删）"""
        return await self._store.forget(memory_id)
    
    # === 上下文管理（核心三步流程）===
    
    async def begin_task(self, description: str,
                         agent_name: str = "default",
                         system_prompt: str = "",
                         extra_context: str = "") -> TaskContext:
        """Step 1: 开始任务——干净上下文 + 检索注入
        
        1. 创建新的TaskContext
        2. 从长期记忆检索与任务相关的经验
        3. 注入到系统提示
        """
        ctx = TaskContext(
            task_id=str(uuid.uuid4())[:8],
            description=description,
            agent_name=agent_name,
            started_at=time.time(),
        )
        
        # 检索相关记忆
        memories = await self.search(description, limit=self.max_inject)
        ctx.injected_memories = memories
        
        # 组装系统提示
        parts = []
        if system_prompt:
            parts.append(system_prompt)
        if extra_context:
            parts.append(extra_context)
        
        if memories:
            lines = []
            for m in memories:
                imp = m.get("importance", 0)
                content = m.get("content", "")
                lines.append(f"- [{imp}] {content}")
            parts.append(
                "\n## 相关历史经验\n" + "\n".join(lines)
            )
        
        ctx.system_prompt = "\n\n".join(parts)
        
        self._tasks[ctx.task_id] = ctx
        return ctx
    
    async def end_task(self, ctx: TaskContext,
                       result: str = "",
                       status: str = "done",
                       lessons: List[str] = None) -> Dict:
        """Step 3: 结束任务——摘要 + 写入长期记忆
        
        1. 生成结构化摘要
        2. 写入长期记忆（WAL保护）
        3. 经验教训高优先级存储
        4. 失败教训最高优先级
        """
        ctx.finished_at = time.time()
        ctx.status = status
        ctx.result = result
        
        # 任务摘要写入记忆
        summary = ctx.to_summary()
        await self.remember(
            summary,
            importance=self.summary_importance,
            tags=["task", status, ctx.agent_name]
        )
        
        # 经验教训
        if lessons:
            for lesson in lessons:
                imp = self.lesson_importance
                if status == "failed":
                    imp = self.failure_importance
                await self.remember(
                    f"[经验] {lesson}",
                    importance=imp,
                    tags=["lesson", ctx.agent_name]
                )
        
        # 失败额外记录
        if status == "failed" and result:
            await self.remember(
                f"[失败原因] {ctx.description}: {result[:300]}",
                importance=self.failure_importance,
                tags=["failure", ctx.agent_name]
            )
        
        # 清理
        self._tasks.pop(ctx.task_id, None)
        
        return {
            "task_id": ctx.task_id,
            "description": ctx.description,
            "status": status,
            "duration": round(ctx.finished_at - ctx.started_at, 1),
            "tokens": ctx.tokens_used,
            "memories_injected": len(ctx.injected_memories),
        }
    
    async def compress_context(self, ctx: TaskContext,
                                max_messages: int = 20,
                                keep_recent: int = 5) -> TaskContext:
        """上下文压缩——消息太多时自动压缩"""
        if len(ctx.messages) <= max_messages:
            return ctx
        
        old = ctx.messages[:-keep_recent]
        recent = ctx.messages[-keep_recent:]
        
        # 旧消息摘要
        old_summary = "\n".join([
            f"[{m['role']}] {m['content'][:80]}"
            for m in old
        ])[:500]
        
        ctx.messages = [
            {"role": "system",
             "content": f"[之前{len(old)}条对话摘要]\n{old_summary}",
             "ts": time.time()}
        ] + recent
        
        return ctx
    
    # === 维护 ===
    
    async def consolidate(self):
        """记忆沉淀 L1→L2→L3"""
        return await self._store.consolidate()
    
    def checkpoint(self, label: str = "manual") -> str:
        """手动checkpoint"""
        return self.guard.checkpoint(label)
    
    def verify(self) -> Dict:
        """完整性校验"""
        return self.guard.verify_integrity()
    
    def stats(self) -> Dict:
        """统计信息"""
        s = self._store.stats()
        s["active_tasks"] = len(self._tasks)
        s["guard_started"] = self._started
        return s
