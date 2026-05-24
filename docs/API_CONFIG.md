# 玄机 (XuanJi) API配置指南

> 核心原则：用户给key，框架管其他一切。

## LLM配置

### 极简版（一行一个）

```toml
[llm]
deepseek = "sk-xxx"
openai = "sk-xxx"
dashscope = "sk-xxx"
ollama = "localhost"
```

框架自动识别：provider名 → base_url + 默认模型 + 认证方式。

### 选模型

```toml
[llm.deepseek]
key = "sk-xxx"
model = "deepseek-reasoner"
```

### 精细控制

```toml
[llm.deepseek]
key = "sk-xxx"
model = "deepseek-reasoner"
base_url = "https://my-proxy.com/v1"
max_tokens = 4096
temperature = 0.7
timeout = 30
rate_limit = 60
```

三个级别用同一个字段名，往下加就行。不加用默认值。

### 降级链

```toml
[llm]
primary = "deepseek"
fallback = ["dashscope", "ollama"]
```

primary挂了 → fallback[0] → fallback[1] → 报错（进程不退出）。

### 智能路由

```toml
[llm.routing]
simple_threshold = 100
complex_model = "deepseek"
simple_model = "ollama"
```

简单任务用本地，复杂任务用API。

## 通信渠道配置

### 极简版

```toml
[channels]
telegram = "bot_token"
discord = "bot_token"
qq = "app_id:app_secret"
email = "user:pass@imap.gmail.com"
```

### 细配

```toml
[channels.telegram]
token = "bot_token"
proxy = "socks5://127.0.0.1:1080"
allowed_users = [12345, 67890]
```

## MCP配置

```toml
[mcp]
# 极简
sqlite = "npx -y @modelcontextprotocol/server-sqlite"

# 详细
github = { command = "npx", args = ["-y", "@modelcontextprotocol/server-github"] }

# 远程
my-server = { url = "http://localhost:3001/sse" }
```

## 服务API配置

```toml
[services]
google_search = "api_key_xxx"
weather = "api_key_xxx"
```

## 环境变量

不想写配置文件，环境变量也行：

```bash
export 玄机 (XuanJi)_LLM_DEEPSEEK=sk-xxx
export 玄机 (XuanJi)_CHANNEL_TELEGRAM=bot_token
玄机 (XuanJi) run
```

## 密钥安全

配置文件里不存明文密钥：

```toml
[llm.deepseek]
key = "${DEEPSEEK_API_KEY}"        # 环境变量
key = "${secret:deepseek_key}"     # 密钥库
```

```bash
玄机 (XuanJi) secret set deepseek_key sk-xxx
```

## 优先级

```
环境变量 > config.toml > 自动探测 > 内置默认值
```

## 自动探测

```bash
玄机 (XuanJi) init

🔍 正在扫描本地环境...
✅ 发现 Ollama (localhost:11434) → 可用模型: qwen3:8b
✅ 发现 DEEPSEEK_API_KEY → 已自动配置
📝 已生成 config.toml
🚀 直接运行 玄机 (XuanJi) run 即可启动
```

## 预置Provider默认值

框架内置所有主流API的默认配置，用户不需要知道：

| Provider | base_url | 默认模型 |
|----------|----------|---------|
| deepseek | api.deepseek.com/v1 | deepseek-chat |
| openai | api.openai.com/v1 | gpt-4o |
| anthropic | api.anthropic.com/v1 | claude-sonnet-4 |
| dashscope | dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| zhipu | open.bigmodel.cn/api/paas/v4 | glm-4-plus |
| moonshot | api.moonshot.cn/v1 | moonshot-v1-8k |
| minimax | api.minimax.chat/v1 | MiniMax-Text-01 |
| ollama | localhost:11434 | 自动扫描 |
| openrouter | openrouter.ai/api/v1 | auto |
| groq | api.groq.com/openai/v1 | llama-3.3-70b |
| together | api.together.xyz/v1 | auto |
| siliconflow | api.siliconflow.cn/v1 | auto |
