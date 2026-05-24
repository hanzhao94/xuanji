# 贡献指南

感谢你对玄机 (XuanJi) 感兴趣！

## 快速开始

```bash
git clone https://github.com/your-org/xuanji.git
cd xuanji
pip install -e ".[all]"
```

## 运行测试

```bash
# 架构审查（17项检查）
python tests/test_architecture_v2.py

# 全量审计
python tests/full_audit.py

# 集成测试
python tests/test_full.py

# Ollama 端到端测试（需要本地运行 Ollama）
python tests/test_full_ollama.py
```

## 代码风格

- Python 3.10+
- 遵循 PEP 8
- 核心模块零外部依赖
- 可选依赖用 try/except 保护，graceful degrade

## 添加 LLM 适配器

1. 在 `python/xuanji/llm/` 下新建 `{provider}_adapter.py`
2. 继承 `LLMAdapter` 基类
3. 实现 `_do_chat()` (返回str) 和 `_do_chat_response()` (返回ChatResponse)
4. 在 `router.py` 中注册

参考 `llm/openai_compat.py` 作为模板。

## 添加通信渠道

1. 在 `python/xuanji/channels/` 下新建 `{channel}.py`
2. 继承 `ChannelAdapter` 基类
3. 实现 `connect()` / `disconnect()` / `send()` / `_on_message()`
4. 在 `channels/router.py` 中注册

## Pull Request

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 发起 Pull Request

## 许可证

本项目采用 MIT License。
