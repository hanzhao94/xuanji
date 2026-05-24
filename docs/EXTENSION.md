# 玄机 (XuanJi) 扩展机制

三种扩展方式，覆盖所有场景。

## 选择指南

| 我想... | 用 | 难度 |
|---------|-----|------|
| 教Agent怎么做某事 | Skill | 会写文档 |
| 让Agent能调某个API | MCP | 会写函数 |
| 给框架加新平台/新能力 | Plugin | 懂框架接口 |

## 一、Skill（技能）

### 最简Skill

```
my-skill/
└── SKILL.md     ← 就这一个文件
```

```markdown
# SKILL.md — 翻译技能

## 触发条件
用户要求翻译

## 执行步骤
1. 识别源语言
2. 确认目标语言
3. 调用LLM翻译
4. 专业术语校对
```

### 带代码的Skill

```
my-skill/
├── SKILL.md
├── tools.py          ← 附带工具
├── templates/        ← 模板文件
├── data/             ← 数据文件
└── skill.toml        ← 元数据
```

```toml
# skill.toml
[skill]
name = "数据分析"
version = "1.0.0"
description = "Excel/CSV数据分析+可视化"

[requires]
python = ["pandas", "matplotlib"]
tools = ["file_read"]
```

### 安装Skill

```bash
# 本地目录
[skills]
paths = ["./my-skills"]

# 在线安装
玄机 (XuanJi) skill install translator
```

## 二、MCP（标准工具协议）

### 接入现有MCP Server

```toml
[mcp]
filesystem = "npx -y @modelcontextprotocol/server-filesystem /home"
github = "npx -y @modelcontextprotocol/server-github"
```

### 自己写MCP Server

```python
from 玄机 (XuanJi).mcp import MCPServer

server = MCPServer("我的工具集")

@server.tool("查天气")
def get_weather(city: str) -> str:
    """查询城市天气"""
    return f"{city}今天晴，25°C"

@server.tool("发邮件")
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    return "发送成功"

if __name__ == "__main__":
    server.run()
```

```toml
[mcp]
my-tools = { command = "python", args = ["my_mcp_server.py"] }
```

### 暴露为MCP Server

```bash
玄机 (XuanJi) serve --mcp --port 3001
# 其他框架（Claude Desktop/Cursor等）都能接入
```

## 三、Plugin（框架插件）

### Plugin类型

| 类型 | 说明 |
|------|------|
| agent | 新的Agent类型 |
| tool | 新的工具 |
| channel | 新的通信渠道 |
| llm | 新的LLM后端 |
| memory | 新的记忆后端 |
| scheduler | 新的调度策略 |

### 写一个Tool Plugin

```
my-tool/
├── plugin.toml
└── tool.py
```

```toml
# plugin.toml
[plugin]
name = "stock-price"
type = "tool"
version = "1.0.0"
entry = "tool.py:StockPriceTool"
description = "实时股票价格查询"

[requires]
python = ["requests"]

[permissions]
network = ["api.stockdata.com"]
```

```python
# tool.py
from 玄机 (XuanJi) import ToolPlugin

class StockPriceTool(ToolPlugin):
    name = "stock_price"
    description = "查询实时股票价格"
    
    def schema(self):
        return {
            "symbol": {"type": "string", "description": "股票代码"},
        }
    
    async def execute(self, params, ctx):
        price = await self.fetch_price(params["symbol"])
        return {"price": price}
```

### 写一个Agent Plugin

```python
from 玄机 (XuanJi) import AgentPlugin

class StockAnalyst(AgentPlugin):
    name = "股票分析师"
    tools = ["stock_price", "web_search"]
    
    async def on_task(self, task, ctx):
        price = await ctx.tools.execute("stock_price", {"symbol": task.symbol})
        analysis = await ctx.llm.chat([...])
        return analysis
    
    async def on_message(self, msg, ctx):
        if "股" in msg.content:
            return await self.on_task(msg, ctx)
```

### 写一个Channel Plugin

```python
from 玄机 (XuanJi) import ChannelPlugin, Message

class MyPlatform(ChannelPlugin):
    name = "my_platform"
    
    async def connect(self, config):
        self.ws = await websocket.connect(config["url"])
    
    async def listen(self):
        async for raw in self.ws:
            data = json.loads(raw)
            msg = Message(channel="my_platform", sender=data["from"],
                         content=data["text"], chat_type="direct")
            await self.emit("message", msg)
    
    async def send_text(self, target, text):
        await self.ws.send(json.dumps({"to": target, "text": text}))
```

## 脚手架

```bash
玄机 (XuanJi) create skill my-translator    # 生成Skill模板
玄机 (XuanJi) create tool stock-price       # 生成Tool Plugin模板
玄机 (XuanJi) create agent my-analyst       # 生成Agent Plugin模板
玄机 (XuanJi) create mcp my-tools           # 生成MCP Server模板
玄机 (XuanJi) create channel my-platform    # 生成Channel Plugin模板
```

## CLI管理

```bash
# Skill
玄机 (XuanJi) skill list / install / create / publish

# MCP
玄机 (XuanJi) mcp list / add / test

# Plugin
玄机 (XuanJi) plugin list / install / create / publish
```
