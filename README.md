# XuanJi (玄机)

> **Embodied AI Multi-Agent Framework** — An open-source AI agent that actually uses your computer.
> 
> *See screen · Control mouse/keyboard · Talk across 30+ platforms · 19 LLM models · Zero mandatory dependencies*

---

**XuanJi** is a fully embodied AI agent framework. Unlike LLMs that only chat, XuanJi can see your screen, control the mouse and keyboard, browse the web, and communicate across 30+ platforms (WeChat, QQ, Telegram, Discord, email, and more). It coordinates multiple AI agents with process isolation and resource arbitration, so they work in parallel without conflicts.

**Key highlights:**
- 🖥️ **Embodied AI** — Screen capture, OCR, mouse/keyboard control, browser automation, voice STT/TTS
- 🤖 **Multi-Agent** — Process-isolated agents with resource arbitration, parallel execution
- 💬 **30+ Channels** — WeChat, QQ, DingTalk, Feishu, Telegram, Discord, Email, Slack, and more
- 🧠 **31+ LLM Models** — DeepSeek, Qwen, GLM, GPT, Claude, Gemini, Ollama local models
- 💾 **Persistent Memory** — 3-level cache + WAL anti-loss + context management
- 🔒 **7-Layer Security** — Sandbox, operation grading, input sanitization, audit, secret management
- 📦 **Extensible** — Skill (markdown), MCP protocol, Plugin (Python) — three extension mechanisms
- 🌍 **Cross-Platform** — Windows / Linux / macOS / ARM, C core for cross-platform performance
- 🚀 **Zero Dependencies** — Core runs with no external packages; optional backends installed on demand

```bash
pip install xuanji
xuanji init my-project
xuanji run
```

---

## 中文文档

**玄机 (XuanJi)** — 具身智能多Agent框架，让AI像人一样使用电脑、与世界交流。

<details>
<summary>👆 English summary above — click here for full Chinese documentation</summary>

---

```bash
pip install xuanji
xuanji init my-project
xuanji run
```

## 🌟 核心特性

| 能力 | 说明 |
|------|------|
| 🖥️ **具身智能** | 截屏/鼠标/键盘/浏览器/语音，像人一样操作电脑 |
| 🤖 **多Agent协作** | 进程隔离+资源仲裁，多Agent并行不冲突 |
| 💬 **全平台通信** | 微信/QQ/钉钉/飞书/Telegram/Discord/邮件...30+平台 |
| 🧠 **全模型接入** | DeepSeek/通义/智谱/GPT/Claude/Gemini/本地...31+模型 |
| 💾 **记忆永不丢** | 三级缓存+WAL防丢失+上下文管理 |
| 🔒 **安全内置** | 七层防护+沙盒+RBAC+审计+密钥管理 |
| 📦 **可扩展** | Skill/MCP/Plugin三种扩展机制 |
| 🌍 **全平台** | Windows/Linux/macOS/ARM，C底座跨平台 |

## 🚀 快速开始

**5分钟跑起来** → 先看 [QUICKSTART.md](QUICKSTART.md) 保姆级教程

> 不想读长篇？四步搞定：
> ```bash
> pip install xuanji          # 安装
> xuanji init my-bot          # 创建项目
> # 编辑 config.toml，写一行 key  ← 配API（智谱/DeepSeek/通义/Ollama都行）
> xuanji run                  # 跑起来
> ```

完整入门指南：[QUICKSTART.md](QUICKSTART.md) — 从安装到写第一个Agent，一步步照着做。

## 📖 文档

- [⚡ 5分钟上手](QUICKSTART.md) — 从安装到跑起来的保姆级教程
- [API配置](docs/API_CONFIG.md) — 所有API的详细配置方式
- [架构设计](docs/ARCHITECTURE.md) — 四大引擎+安全+扩展
- [扩展机制](docs/EXTENSION.md) — Skill/MCP/Plugin
- [安全设计](docs/SECURITY.md) — 七层防护
- [开发路线](docs/ROADMAP.md) — 开发计划

## 🧪 测试

```bash
# 集成测试（51项）
python tests/test_full.py

# 端到端测试（Ollama本地）
python tests/test_full_ollama.py

# 真实任务测试
python tests/test_real_task.py        # TODO应用开发
python tests/test_hard_task.py        # PyPI分析器
python tests/test_movie_analysis.py   # 电影行业分析
python tests/test_stock_dashboard.py  # 股票分析仪表盘
```

## 🆚 为什么选玄机？

| 能力 | **玄机 XuanJi** | AutoGPT | CrewAI | LangChain |
|------|:---:|:---:|:---:|:---:|
| 操控电脑（截屏/鼠标/键盘） | ✅ | ❌ | ❌ | ❌ |
| 全平台通信（微信/QQ/钉钉/Telegram/Discord） | ✅ | ❌ | ❌ | ❌ |
| 多Agent协作+资源仲裁 | ✅ | ⚠️ | ✅ | ❌ |
| 30+ LLM适配器 | ✅ | ⚠️ | ⚠️ | ✅ |
| 七层安全防护 | ✅ | ❌ | ❌ | ❌ |
| 零强制依赖 | ✅ | ❌ | ❌ | ❌ |
| 全平台（Win/Linux/macOS） | ✅ | ⚠️ | ✅ | ✅ |
| 进化系统（失败学习/模式复用） | ✅ | ❌ | ❌ | ❌ |
| `pip install` 直接运行 | ✅ | ⚠️ | ✅ | ✅ |
| **迁移其他框架的旧数据** | ✅ | ❌ | ❌ | ❌ |

## 🔀 无缝迁移 — 从任何框架迁移到玄机

**"不管你在哪跑的，来玄机都能接着用。"**

在 OpenClaw 跑了半年？Claude Projects 积累了很多？LangChain 搭了一套 agents？
**一键迁移，无缝继续。**

```bash
# 从 OpenClaw 迁移
xuanji migrate from openclaw ~/.openclaw/workspace

# 从 Claude 迁移
xuanji migrate from claude ~/.claude/project

# 自动检测
xuanji migrate auto /some/path

# 预览可迁移内容
xuanji migrate preview ~/.openclaw/workspace
```

### 已支持的来源

| 来源 | 迁移内容 |
|------|----------|
| **OpenClaw** | Skills (SKILL.md) / Memory / Tools / Agent Config / MCP |
| **Claude** | .claude/skills / CLAUDE.md / Projects 对话记录 |
| **MCP** | 连接任意 MCP Server，自动发现 tools |
| **LangChain** | chains / agents / tools / 对话历史 |

### 示例输出

```
$ xuanji migrate from openclaw ~/.openclaw/workspace
迁移结果: 43 skills, 70 memories, 43 tools

Skills (43):
  - agentic-workflow-automation: Generate reusable multi-step agent workflow...
  - ai-aesthetics: AI审美判断系统——跨领域美学评估...
  - ...

Memories (70):
  [semantic] imp=1.0: # MEMORY.md - 灵明的长期记忆
  [episodic] imp=0.5: # Session: 2026-04-15
  ...

✅ 迁移完成
```

### 代码集成

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
# {'skills': 43, 'tools': 43, 'memories': 70}
```

### 扩展你自己的适配器

```python
from xuanji.adapters import BaseAdapter, AdapterRegistry, MigrationResult

class MyFrameworkAdapter(BaseAdapter):
    name = "my_framework"
    detect_patterns = ["my_config.json"]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        # 你的迁移逻辑
        return result

# 注册
AdapterRegistry.register(MyFrameworkAdapter())
```

## 📊 项目统计

| 指标 | 数值 |
|------|------|
| Python文件 | 169 |
| 代码行数 | ~55,000 |
| 通讯渠道 | 29个 |
| LLM适配器 | 19个 |
| Agent工具 | 24个 |
| 安全模块 | 12个 |
| 测试通过 | 17/17架构审查 + 5/5全量审计 |
| 外部依赖 | 零强制依赖 |

## 🏗️ 架构

```
┌─────────────────────────────────────────────┐
│            插件层（用户写的）                  │
│  Skill / MCP / Agent插件 / Tool插件          │
├─────────────────────────────────────────────┤
│          安全系统（七层防护）                  │
│  沙箱 / 操作分级 / 输入消毒 / 审计 / 密钥     │
├─────────────────────────────────────────────┤
│          四大内置引擎                         │
│                                              │
│  多Agent引擎        具身引擎                  │
│  ├ 消息总线        ├ 感知(截屏/OCR)           │
│  ├ 资源仲裁        ├ 操控(鼠标/键盘)          │
│  ├ 进程隔离        ├ 语音(STT/TTS)            │
│  └ 运行时          └ 具身协调                 │
│                                              │
│  通信引擎            智能引擎                  │
│  ├ 28个渠道        ├ LLM适配(31+模型)         │
│  ├ 智能路由        ├ 记忆(三级缓存)            │
│  └ 消息统一        └ Token治理                │
├─────────────────────────────────────────────┤
│          C底座 (跨平台)                       │
│  消息总线 / 调度器 / 资源管理 / 进程隔离       │
│  Windows / Linux / macOS / ARM               │
└─────────────────────────────────────────────┘
```

## 📝 真实任务示例

### 股票分析仪表盘（60秒完成）

```
任务: "帮我做一个股票市场分析"
→ 玄机自动: 组队(架构师+开发+分析师) → 请求Yahoo Finance API
→ 计算MA/RSI/MACD → LLM分析 → 生成可视化代码 → 写报告 → 沉淀经验
→ 产出: stock_data.json + dashboard.py + stock_report.md
```

### 电影行业调查（38秒完成）

```
任务: "调查2024-2026电影趋势"
→ 玄机自动: 检索历史记忆 → 团队分工 → 数据收集
→ LLM深度分析(趋势+投资) → 生成完整报告 → 经验沉淀
→ 产出: movie_data.json + movie_report.md
```

## 🔌 扩展机制

### Skill（技能）— 一个markdown文件

```markdown
# SKILL.md — 翻译技能
## 触发条件: 用户要求翻译
## 执行步骤: 1.识别源语言 2.确认目标语言 3.调用LLM翻译
```

### MCP（工具协议）— 标准协议接入

```toml
[mcp]
filesystem = "npx -y @modelcontextprotocol/server-filesystem /home"
github = "npx -y @modelcontextprotocol/server-github"
```

### Plugin（插件）— 深度扩展

```python
from xuanji import ToolPlugin

class StockTool(ToolPlugin):
    name = "stock_price"
    async def execute(self, params, ctx):
        return await self.fetch_price(params["symbol"])
```

## 🎬 演示

```
任务: "截个屏，告诉我现在屏幕上有什么"
→ 玄机: 截屏 → LLM分析画面 → 描述屏幕内容 → 回复

任务: "帮我搜索XXX并总结结果"
→ 玄机: 打开浏览器 → 输入搜索 → 读取结果 → 提取信息 → 生成摘要
```

## 📜 许可证

MIT License

## 🙏 致谢

本项目吸收了OpenClaw、Claw Code、deer-flow等项目的工程精华，
并融入了灵明（LingMing）数字生命体的核心技术方法论。

---

</details>
