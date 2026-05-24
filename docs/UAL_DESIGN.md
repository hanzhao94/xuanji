# UAL (Universal Adapter Layer) 设计文档

> **目标：** 让每个人都能把自己之前在任何框架里跑出来的东西，无缝带到玄机上继续用。
> 
> "不管你在哪跑的，来玄机都能接着用。"

## 架构

```
┌─────────────────────────────────────────┐
│              玄机 核心框架                │
│  ┌─────────────────────────────────┐    │
│  │       统一工具接口层 (UTI)       │    │
│  │  tool_call() / skill_run() /    │    │
│  │  memory_search() / agent_spawn()│    │
│  └──────┬──────┬──────┬──────┬─────┘    │
│         │      │      │      │          │
│  ┌──────▼──┐ ┌─▼────┐ ┌▼────┐ ┌▼─────┐ │
│  │OpenClaw │ │Claude│ │Lang │ │ MCP  │ │
│  │Adapter  │ │Adapt │ │Chain│ │Adapt │ │
│  └─────────┘ └──────┘ └─────┘ └──────┘ │
│  ┌─────────┐ ┌──────┐ ┌─────┐ ┌──────┐ │
│  │AutoGen  │ │CrewAI│ │Open │ │Llama │ │
│  │Adapter  │ │Adapt │ │AI   │ │Index │ │
│  └─────────┘ └──────┘ └─────┘ └──────┘ │
└─────────────────────────────────────────┘
```

## 已实现的适配器 (8个)

| 适配器 | 状态 | 迁移内容 |
|---|---|---|
| **OpenClaw** | ✅ | Skills (SKILL.md) / Memory / Tools / Agent Config / MCP |
| **Claude** | ✅ | .claude/skills / CLAUDE.md / Projects 对话记录 |
| **MCP** | ✅ | 连接任意 MCP Server，自动发现 tools |
| **LangChain** | ✅ | chains / agents / tools / 对话历史 |
| **AutoGen** | ✅ | Agents / GroupChat / Tools / Conversations |
| **CrewAI** | ✅ | Agents / Tasks / Tools / Knowledge |
| **OpenAI** | ✅ | Assistants / GPTs / Functions / Conversations |
| **LlamaIndex** | ✅ | QueryEngines / Tools / Documents / Agent Config |

## 核心设计原则

1. **只读不写** — UAL读取外部格式，转为玄机内部格式，不修改原始文件
2. **零配置** — 自动检测，不需要用户手动指定
3. **渐进式** — 先支持主流框架，再扩展其他

## 用户命令

```bash
# 预览
xuanji migrate preview ~/.openclaw/workspace

# 从指定框架迁移
xuanji migrate from openclaw ~/.openclaw/workspace
xuanji migrate from claude ~/.claude/project
xuanji migrate from langchain ./my-project
xuanji migrate from autogen ./autogen-project
xuanji migrate from crewai ./crew-project
xuanji migrate from openai ./openai-project
xuanji migrate from llamaindex ./llama-project

# 自动检测
xuanji migrate auto /some/path
```

## 代码集成

```python
from xuanji.adapters import AdapterRegistry, MigrationEngine

# 自动检测并迁移
result = AdapterRegistry.auto_migrate('/path/to/workspace')

# 注入玄机核心
engine = MigrationEngine(
    skill_loader=my_skill_loader,
    tool_registry=my_tool_registry,
    memory_manager=my_memory_manager,
)
counts = engine.apply_all(result)
```

## 测试结果

```
OpenClaw 工作区迁移: 43 skills + 43 tools + 70 memories
SkillLoader 注入: 43/43 ✅
ToolRegistry 注册: 43/43 ✅
MemoryManager 存储: 70/70 ✅
CLI migrate 命令: ✅ 已集成
```

## 代码统计

- **文件数:** 11 个
- **总代码:** 2553 行
- **适配器:** 8 个

## 扩展方式

要添加新框架适配器：

1. 创建 `xuanji/adapters/xxx_adapter.py`
2. 继承 `BaseAdapter`，实现 `migrate()` 方法
3. 在 `__init__.py` 中注册：`AdapterRegistry.register(XxxAdapter())`

```python
from .base import BaseAdapter, MigrationResult

class MyAdapter(BaseAdapter):
    name = "my_framework"
    detect_patterns = ["my_config.json"]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        # 你的迁移逻辑
        return result
```

## 下一步

- [ ] 更多适配器（根据需要）
- [ ] 真实项目迁移测试
- [ ] 性能优化
