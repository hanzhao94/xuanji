# -*- coding: utf-8 -*-
"""
玄机 (XuanJi) — 具身智能多Agent框架

让AI像人一样使用电脑、与世界交流。

核心能力:
  - 具身智能: 看屏幕、操作鼠标键盘、语音对话
  - 多Agent: 多Agent并行不冲突，进程隔离+资源仲裁
  - 全平台通信: 微信/QQ/Telegram/Discord/邮件...30+平台
  - 全模型接入: DeepSeek/通义/智谱/GPT/Claude/Gemini/本地...31+模型
  - 记忆系统: 三级记忆+记忆守护，永不丢失
  - 安全内置: 七层安全防护，沙箱+审计+密钥保护
  - 可扩展: Skill/MCP/Plugin三种扩展机制，用户自由定制
"""

__version__ = "1.0.4"
__author__ = "XuanJi Team"

# 核心类
from xuanji.plugin import AgentPlugin, ToolPlugin, ChannelPlugin
from xuanji.runtime import Runtime

# 内置架构
from xuanji.bus import MessageBus, Message, MsgType
from xuanji.arbiter import ResourceArbiter
from xuanji.memory import MemoryManager
from xuanji.agent_runner import AgentRunner, ToolRegistry, AgentResult, AgentStep
from xuanji.team import TeamEngine
from xuanji.personas import PersonaLibrary, ExpertPersona, BUILTIN_PERSONAS
from xuanji.governor_advanced import AdvancedGovernor, TimeTravel
from xuanji.llm._base import ChatResponse

# 安全
from xuanji.security.sandbox import FileSystemSandbox as Sandbox
from xuanji.security.guard import OperationGuard as Guard

# 长任务
from xuanji.long_task import LongTaskManager, LongTask, TaskStatus, run_long_task, get_task, list_tasks

__all__ = [
    # 核心
    "AgentPlugin", "ToolPlugin", "ChannelPlugin", "Runtime",
    # 内置架构
    "MessageBus", "Message", "MsgType", "ResourceArbiter",
    "MemoryManager", "AgentRunner", "ToolRegistry", "AgentResult", "AgentStep", "TeamEngine",
    # 专家人格
    "PersonaLibrary", "ExpertPersona", "BUILTIN_PERSONAS",
    # 思考模式
    "ChatResponse",
    # 安全
    "Sandbox", "Guard",
    # 长任务
    "LongTaskManager", "LongTask", "TaskStatus",
    "run_long_task", "get_task", "list_tasks",
]
