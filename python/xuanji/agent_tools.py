"""
xuanji Agent工具扩展：记忆 + 消息推送

把玄玑已有的 memory/ 和 channels/ 注册为Agent可调用的工具。
"""

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 记忆工具
# ─────────────────────────────────────────────

def register_memory_tools(registry, memory_manager=None) -> None:
    """注册记忆工具到Agent
    
    Args:
        registry: ToolRegistry实例
        memory_manager: MemoryManager实例（None则用MemoryStore）
    """
    store = None
    if memory_manager:
        try:
            store = memory_manager.store
        except AttributeError:
            pass
    
    if not store:
        # 没有外部MemoryManager，用内置的MemoryStore
        try:
            from xuanji.memory.store import MemoryStore
            store = MemoryStore()
        except Exception as e:
            logger.warning(f"Memory store unavailable: {e}")
            return
    
    # ── remember ──
    def _remember(key: str, value: str, category: str = "general") -> str:
        """记住一条信息"""
        try:
            # 如果store有remember方法
            if hasattr(store, 'remember'):
                store.remember(key, value, category=category)
                return f"已记住：{key} = {value[:50]}{'...' if len(value) > 50 else ''}"
            # 否则用store方法
            if hasattr(store, 'store'):
                store.store(key, value, tags=[category])
                return f"已存储：{key} ({category})"
            return f"存储方法不可用"
        except Exception as e:
            return f"存储失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="remember",
        description="记住一条信息，供未来任务使用。如：记住用户的偏好、重要信息",
        params={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的唯一标识，如 user_preferred_style"},
                "value": {"type": "string", "description": "要记住的内容"},
                "category": {"type": "string", "description": "分类：preference/task/fact/note（默认general）"},
            },
            "required": ["key", "value"],
        },
        func=_remember,
        category="memory",
    )
    
    # ── search_memory ──
    def _search_memory(query: str, category: str = "", limit: int = 5) -> str:
        """搜索已存储的记忆"""
        try:
            if hasattr(store, 'search'):
                if category:
                    results = store.search(query, category=category, limit=limit)
                else:
                    results = store.search(query, limit=limit)
                if isinstance(results, list) and results:
                    lines = []
                    for i, r in enumerate(results[:limit]):
                        if isinstance(r, dict):
                            key = r.get("key", r.get("id", "?"))
                            val = r.get("value", r.get("content", str(r)))
                            cat = r.get("category", r.get("tags", ""))
                        else:
                            key = f"item_{i}"
                            val = str(r)
                            cat = ""
                        lines.append(f"[{i+1}] {key}: {val[:100]}{'...' if len(str(val)) > 100 else ''}")
                    return "\n".join(lines)
                return "未找到相关记忆"
            return "搜索方法不可用"
        except Exception as e:
            return f"搜索失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="search_memory",
        description="搜索之前记住的信息。如：'查一下我的写作风格偏好'",
        params={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "category": {"type": "string", "description": "按分类过滤（可选）"},
                "limit": {"type": "integer", "description": "返回数量（默认5）"},
            },
            "required": ["query"],
        },
        func=_search_memory,
        category="memory",
    )
    
    # ── forget ──
    def _forget(key: str) -> str:
        """删除一条记忆"""
        try:
            if hasattr(store, 'forget'):
                store.forget(key)
                return f"已删除记忆：{key}"
            return "删除方法不可用"
        except Exception as e:
            return f"删除失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="forget",
        description="删除之前记住的某条信息",
        params={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "要删除的记忆标识"},
            },
            "required": ["key"],
        },
        func=_forget,
        category="memory",
    )
    
    # ── list_memories ──
    def _list_memories(category: str = "") -> str:
        """列出所有记忆的统计信息"""
        try:
            if hasattr(store, 'stats'):
                stats = store.stats()
                if isinstance(stats, dict):
                    lines = []
                    for k, v in stats.items():
                        lines.append(f"  {k}: {v}")
                    return "记忆统计：\n" + "\n".join(lines)
                return f"记忆统计：{stats}"
            return "统计方法不可用"
        except Exception as e:
            return f"获取统计失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="list_memories",
        description="列出已存储的记忆统计信息",
        params={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "按分类过滤（可选，为空则显示全部）"},
            },
            "required": [],
        },
        func=_list_memories,
        category="memory",
    )
    
    logger.info(f"Memory tools registered: 4 tools")


# ─────────────────────────────────────────────
# 消息推送工具
# ─────────────────────────────────────────────

def register_channel_tools(registry, channel_router=None) -> None:
    """注册消息推送工具到Agent
    
    Args:
        registry: ToolRegistry实例
        channel_router: ChannelRouter实例
    """
    if not channel_router:
        logger.info("ChannelRouter not provided, skipping channel tools")
        return
    
    # ── send_message ──
    def _send_message(channel: str, target: str, message: str) -> str:
        """发送消息到指定渠道"""
        try:
            ch = channel_router.get_channel(channel)
            if not ch:
                return f"错误：渠道 '{channel}' 不存在。可用渠道：{', '.join(channel_router.channel_names)}"
            
            # 尝试不同的发送方法
            if hasattr(ch, 'send'):
                ch.send(target, message)
            elif hasattr(ch, 'send_message'):
                ch.send_message(target, message)
            elif hasattr(channel_router, 'send'):
                channel_router.send(channel, target, message)
            else:
                return f"错误：渠道 '{channel}' 不支持发送消息"
            
            return f"消息已发送到 {channel} -> {target}"
        except Exception as e:
            return f"发送失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="send_message",
        description="发送消息到指定通信渠道（微信/QQ/邮件/钉钉等）",
        params={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "渠道名，如 wechat/qq/email/dingtalk"},
                "target": {"type": "string", "description": "接收者，如群聊ID、用户ID、邮箱地址"},
                "message": {"type": "string", "description": "消息内容"},
            },
            "required": ["channel", "target", "message"],
        },
        func=_send_message,
        category="communication",
    )
    
    # ── list_channels ──
    def _list_channels() -> str:
        """列出可用的通信渠道"""
        try:
            names = channel_router.channel_names
            if isinstance(names, dict):
                lines = []
                for name, info in names.items():
                    status = "✅" if isinstance(info, dict) and info.get("connected") else "❌"
                    lines.append(f"  {status} {name}")
                return "可用渠道：\n" + "\n".join(lines)
            elif isinstance(names, list):
                return "可用渠道：" + ", ".join(names)
            return str(names)
        except Exception as e:
            return f"获取渠道失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="list_channels",
        description="列出所有已注册的通信渠道及其状态",
        params={
            "type": "object",
            "properties": {},
            "required": [],
        },
        func=_list_channels,
        category="communication",
    )
    
    logger.info(f"Channel tools registered: 2 tools")


# ─────────────────────────────────────────────
# 一键创建完整Agent
# ─────────────────────────────────────────────

def create_full_agent(llm_router, model: Optional[str] = None,
                      memory_manager=None, channel_router=None,
                      max_steps: int = 15) -> 'AgentRunner':
    """创建包含全部工具的Agent
    
    Args:
        llm_router: LLMRouter实例
        model: 使用的模型名
        memory_manager: MemoryManager实例（可选）
        channel_router: ChannelRouter实例（可选）
        max_steps: 最大执行步数
    
    Returns:
        配置好的AgentRunner
    """
    from xuanji.agent_runner import AgentRunner
    from xuanji.natural_agent import register_builtin_tools
    
    runner = AgentRunner(llm_router, model=model, max_steps=max_steps)
    
    # 基础工具（9个）
    register_builtin_tools(runner.registry)
    
    # 记忆工具（4个）
    register_memory_tools(runner.registry, memory_manager)
    
    # 渠道工具（2个，需要channel_router）
    if channel_router:
        register_channel_tools(runner.registry, channel_router)
    
    return runner
