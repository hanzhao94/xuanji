# -*- coding: utf-8 -*-
"""
Universal Adapter Layer — 生态兼容适配器

自动注册所有适配器，提供统一迁移入口。
"""
from .base import (
    BaseAdapter, AdapterRegistry,
    Skill, Memory, Tool, AgentConfig, MigrationResult,
)
from .openclaw_adapter import OpenClawAdapter
from .claude_adapter import ClaudeAdapter
from .mcp_adapter import MCPAdapter
from .langchain_adapter import LangChainAdapter
from .autogen_adapter import AutoGenAdapter
from .crewai_adapter import CrewAIAdapter
from .openai_adapter import OpenAIAdapter
from .llamaindex_adapter import LlamaIndexAdapter
from .migrate import MigrationEngine, quick_preview, quick_migrate

# 注册所有适配器
AdapterRegistry.register(OpenClawAdapter())
AdapterRegistry.register(ClaudeAdapter())
AdapterRegistry.register(MCPAdapter())
AdapterRegistry.register(LangChainAdapter())
AdapterRegistry.register(AutoGenAdapter())
AdapterRegistry.register(CrewAIAdapter())
AdapterRegistry.register(OpenAIAdapter())
AdapterRegistry.register(LlamaIndexAdapter())

__all__ = [
    "BaseAdapter", "AdapterRegistry",
    "Skill", "Memory", "Tool", "AgentConfig", "MigrationResult",
    "OpenClawAdapter", "ClaudeAdapter", "MCPAdapter", "LangChainAdapter",
    "AutoGenAdapter", "CrewAIAdapter", "OpenAIAdapter", "LlamaIndexAdapter",
    "MigrationEngine", "quick_preview", "quick_migrate",
]
