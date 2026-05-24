# 玄机 QuickStart — 5分钟跑起来

> 不看架构、不读源码，照着做就能让AI替你干活。

---

## 第1步：安装（30秒）

### 前提条件

| 需要什么 | 说明 |
|----------|------|
| Python | 3.9+（`python --version` 检查） |
| 操作系统 | Windows / Linux / macOS 都行 |
| API Key | 至少一个LLM的Key（下面有免费方案） |

没有Python？
- **Windows**: https://www.python.org/downloads/ → 安装时勾选 "Add to PATH"
- **macOS**: `brew install python3`
- **Ubuntu**: `sudo apt install python3 python3-pip`

### 一键安装

```bash
pip install xuanji
```

装好了验证一下：

```bash
xuanji --version
```

看到版本号 = 成功。

---

## 第2步：创建项目（10秒）

```bash
xuanji init my-bot
cd my-bot
```

会自动生成：
```
my-bot/
├── config.toml      ← 配置文件（改这里）
├── skills/          ← 放你的技能
└── plugins/         ← 放你的插件
```

---

## 第3步：配一个API Key（1分钟）

### 方案A：国内免费/便宜方案（推荐新手）

**智谱GLM** — 新用户送额度，够用很久
1. 去 https://open.bigmodel.cn 注册
2. 创建 API Key
3. 打开 `config.toml`，写一行：

```toml
[llm]
zhipu = "你的key"
```

**通义千问** — 新用户也有额度
```toml
[llm]
dashscope = "你的key"
```

### 方案B：DeepSeek（性价比高）

```toml
[llm]
deepseek = "sk-你的key"
```

### 方案C：完全免费 — 本地模型

装 Ollama：https://ollama.com

```bash
# 装完后拉一个模型
ollama pull qwen2.5:7b

# 玄机自动检测到Ollama，不用配
```

### 方案D：OpenAI / Claude

```toml
[llm]
openai = "sk-你的key"
# 或
anthropic = "sk-ant-你的key"
```

### 多个一起配（自动降级）

```toml
[llm]
zhipu = "key1"           # 主模型
fallback = ["dashscope", "ollama"]  # 挂了自动切
```

---

## 第4步：跑起来！（5秒）

```bash
xuanji run
```

看到类似输出 = 成功启动：
```
✅ 玄机 v1.0.2 已启动
✅ LLM: 智谱GLM (glm-4-plus)
✅ 渠道: 控制台交互模式
🤖 等待任务中...
```

在控制台直接输入任务试试：

```
> 帮我搜索2024年AI大事件并总结
```

玄机会自动思考、搜索、生成报告。

---

## 第5步（可选）：接入聊天平台

想让它在你常用的IM里回复你？

### QQ

```toml
[channels]
qq = "你的APP_ID:你的APP_SECRET"
```

> QQ开放平台申请: https://q.qq.com

### Telegram

```toml
[channels]
telegram = "你的BOT_TOKEN"
```

> 找 @BotFather 创建机器人，拿到 token

### 微信

```toml
[channels]
wechat = "已登录的微信实例"
```

> 需要配合微信hook或协议端使用

### Discord

```toml
[channels]
discord = "你的BOT_TOKEN"
```

> Discord开发者后台创建应用

### 同时接入多个

```toml
[channels]
qq = "id:secret"
telegram = "token"
discord = "token"
```

消息从哪个平台来，就回复到哪个平台。

---

## 进阶：写你的第一个Agent

### 最简单的Agent — 5行代码

在 `plugins/` 下创建 `my_helper.py`：

```python
from xuanji import AgentPlugin

class MyHelper(AgentPlugin):
    name = "我的助手"

    async def on_message(self, msg, ctx):
        # 收到消息 → 调LLM → 回复
        reply = await ctx.llm.chat([
            {"role": "user", "content": msg.content}
        ])
        await ctx.channels.reply(msg, reply)
```

重启 `xuanji run`，它就开始工作了。

### 能操作电脑的Agent

```python
from xuanji import AgentPlugin

class WebSearcher(AgentPlugin):
    name = "网页搜索员"

    async def on_task(self, task, ctx):
        # 打开浏览器
        await ctx.hands.open_app("Chrome")
        # 输入网址
        await ctx.hands.type_text("https://www.google.com")
        # 按回车
        await ctx.hands.press("Enter")
        # 截图看看打开了没
        screen = ctx.perception.screenshot()
        # 让LL分析截图
        analysis = await ctx.llm.vision(screen, "这个页面上有什么？")
        return analysis
```

---

## 常见问题

### Q: 安装时 `pip install` 报错？

```bash
# Windows
pip install xuanji --user

# 如果是权限问题
pip install xuanji --user

# 网络慢
pip install xuanji -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 启动后说找不到LLM？

检查 `config.toml` 里的 key 是否正确，以及是否有网络访问外网的权限。

```bash
# 调试模式看详细日志
xuanji run --debug
```

### Q: 想用免费模型？

1. 装 Ollama（https://ollama.com）
2. `ollama pull qwen2.5:7b`
3. 玄机自动检测，不需要额外配置

### Q: 如何停止？

`Ctrl+C` 就行。优雅退出，不丢数据。

### Q: 配置文件在哪里？

项目目录下的 `config.toml`。改了不用重启，热加载生效。

### Q: 怎么知道它正在干什么？

运行时实时输出日志，每个操作都有进度提示。

---

## 下一步

跑起来之后，看这些：

| 文档 | 看什么 |
|------|--------|
| [API_CONFIG.md](docs/API_CONFIG.md) | 所有API的详细配置方式 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 四大引擎+安全+扩展的完整架构 |
| [EXTENSION.md](docs/EXTENSION.md) | 怎么扩展（Skill/MCP/Plugin） |
| [SECURITY.md](docs/SECURITY.md) | 七层安全体系设计 |
| [ROADMAP.md](docs/ROADMAP.md) | 开发计划和路线图 |

---

## 从0到跑起来，总结

```
pip install xuanji          ← 安装
xuanji init my-bot          ← 创建项目
# 编辑 config.toml，写一行 key  ← 配API
xuanji run                  ← 跑起来
```

**就这四步。**

有问题？提 issue → https://github.com/hanzhao94/xuanji/issues
