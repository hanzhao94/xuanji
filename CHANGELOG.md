# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-17

### Added
- **四大引擎**: 多Agent引擎 / 具身引擎 / 通信引擎 / 智能引擎
- **29个通信渠道**: 微信/QQ/钉钉/飞书/Telegram/Discord/WhatsApp/Slack/Email等
- **19个LLM适配器**: DeepSeek/通义/智谱/豆包/Moonshot/零一万物/百川/MiniMax/讯飞/OpenRouter/Groq/Together/SiliconFlow/StepFun/千帆/星环/混元 + Ollama本地
- **Agent三层体系**: Base (9工具) / Full (15工具) / Ultimate (24+工具)
- **七层安全系统**: 沙箱 / 操作分级 / 网络白名单 / 插件安全 / 输入消毒 / 密钥管理 / 审计日志
- **扩展系统**: Skill (markdown) / MCP协议 / Plugin (Python)
- **全平台支持**: Windows / Linux / macOS (含Darwin专属操控和截屏)
- **进化系统**: 进化引擎 / 钩子系统 / 失败学习 / 模式复用 / 反模式检测
- **工作流系统**: 工作流引擎 / 任务调度 / 检查点管理
- **记忆系统**: 三级缓存 + WAL防丢失 + 上下文管理
- **CLI**: `xuanji init` / `xuanji run` 一键启动

### Fixed
- 19个LLM适配器统一类型契约 (`chat()→str`, `chat_response()→ChatResponse`)
- DeepSeek/Doubao/Zhipu/Dashscope/OpenAICompat 思考模式架构修复
- OpenRouter BOM编码问题
- webhook渠道补全 `send_image()` / `send_file()`
- `tutorial.py` 安全修复 (`eval` → `getattr`)
- E2E架构审查异步调用修复

### Stats
- 169 Python 文件 / ~55,000 行代码
- 架构审查 17/17 全绿
- 全量审计 5/5 OK, 0 Issues, 0 Warnings
- 零强制依赖，wheel 650KB
