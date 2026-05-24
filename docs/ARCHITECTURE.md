# 玄机 (XuanJi) 架构设计文档

> 版本: v0.1.0 | 日期: 2026-05-15 | 状态: 设计阶段

## 一、项目定位

具身智能多Agent框架。不是"能聊天的Agent"，是"能像人一样用电脑的Agent"。

核心差异：
- 传统Agent框架：LLM + 工具调用 = 聊天机器人
- 玄机 (XuanJi)：LLM + 眼睛 + 手 + 嘴 + 记忆 + 多Agent = 数字员工

## 二、技术栈

```
C11   — 底座（消息总线/调度器/资源管理/进程隔离）
Python — 运行时+业务层（Agent逻辑/AI对接/插件系统）
ctypes — C↔Python桥接（零外部依赖）
```

选型理由：
- C底座：写一次管十年，ABI通用，全平台，极致轻量
- Python业务层：AI生态全在Python，快速迭代
- 不用Rust：已有体系全是Python，加Rust增加编译链复杂度
- 不用Go/Java：AI生态弱，桥接开销大

## 三、总体架构

```
┌──────────────────────────────────────────────────┐
│                 插件层（可换）                      │
│  Agent插件 / LLM后端 / 记忆后端 / 调度策略         │
│  扩展工具 / Skill / MCP Server                    │
├──────────────────────────────────────────────────┤
│                                                   │
│            框架内核（内置，不可拔）                  │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  引擎1: 多Agent引擎                          │ │
│  │    进程隔离 / 资源分区 / 仲裁 / 租约           │ │
│  │    消息总线 / 操控令牌 / 心跳 / 崩溃恢复        │ │
│  ├─────────────────────────────────────────────┤ │
│  │  引擎2: 具身引擎                              │ │
│  │    感知: 截屏/OCR/变化检测/摄像头               │ │
│  │    操控: 鼠标/键盘/窗口/浏览器                  │ │
│  │    语音: STT/TTS/唤醒词                        │ │
│  ├─────────────────────────────────────────────┤ │
│  │  引擎3: 通信引擎                              │ │
│  │    渠道: 微信/QQ/Telegram/Discord/邮件...      │ │
│  │    路由: 多渠道统一收发/群发/转发               │ │
│  ├─────────────────────────────────────────────┤ │
│  │  引擎4: 智能引擎                              │ │
│  │    LLM: 全模型适配/智能路由/降级链/缓存         │ │
│  │    记忆: 三级缓存/记忆守护/防丢失               │ │
│  │    Token: 预算/计数/压缩/按需加载               │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
├──────────────────────────────────────────────────┤
│                    C底座 + PAL                     │
│  oa_bus / oa_sched / oa_res / oa_proc / oa_heart  │
│  PAL: Windows / Linux / macOS / ARM               │
└──────────────────────────────────────────────────┘
```

## 四、框架内置 vs 插件

### 框架内置（装完就有，不可拔）

| 模块 | 功能 | 理由 |
|------|------|------|
| 多Agent引擎 | 进程隔离/资源仲裁/消息总线 | Agent框架基础设施 |
| 具身引擎 | 感知/操控/语音 | 具身Agent核心能力 |
| 通信引擎 | 全平台IM/邮件收发 | Agent必须能和人交流 |
| 智能引擎 | LLM适配/记忆/Token治理 | Agent必须能思考 |
| 安全系统 | 七层防护/审计 | 安全不是可选项 |
| 插件系统 | 加载/发现/管理 | 可扩展性基础 |

### 插件（可替换可扩展）

| 类型 | 示例 | 说明 |
|------|------|------|
| Agent | 编程Agent/分析Agent | 用户业务逻辑 |
| LLM后端 | OpenAI/Ollama/百炼 | 选用哪个模型服务 |
| 记忆后端 | SQLite/向量库 | 数据存在哪里 |
| 调度策略 | 优先级/轮询 | 怎么分配任务 |
| 扩展工具 | Docker/Git | 额外工具能力 |

## 五、C底座设计

### 5.1 平台抽象层 (PAL)

所有OS差异封装在PAL层，上层代码不碰OS API。

```
oa_pal.h — 统一接口
oa_pal_win.c — Windows实现 (Win32 API)
oa_pal_unix.c — Linux/macOS实现 (POSIX)
```

PAL覆盖：
- 进程管理: spawn/kill/wait/is_alive
- 共享内存: create/open/close/destroy
- 互斥锁: create/lock/unlock/destroy
- 文件锁: lock/unlock
- 时间: 毫秒时间戳/sleep
- 原子操作: add/compare-and-swap
- 路径: join/temp/home/exists/mkdir
- 动态库: load/symbol/close (.dll/.so/.dylib)

### 5.2 核心模块

| 文件 | 功能 | 关键数据结构 |
|------|------|-------------|
| oa_bus.c | 消息总线 | 无锁环形缓冲区 |
| oa_sched.c | 调度器 | 优先级队列 |
| oa_res.c | 资源管理 | 分区表+租约表 |
| oa_proc.c | 进程管理 | 进程表+PID白名单 |
| oa_heart.c | 心跳检测 | 时间戳数组 |
| oa_lock.c | 锁原语 | 原子操作+CAS |

### 5.3 编译

CMake跨平台编译，输出动态库：
- Windows: 玄机 (XuanJi).dll
- Linux: lib玄机 (XuanJi).so
- macOS: lib玄机 (XuanJi).dylib

预编译wheel覆盖 6个平台组合 (win/linux/mac × x64/arm64)。

## 六、多Agent引擎

### 6.1 三层隔离

```
Layer 1 — 进程隔离（物理隔离）
  每个Agent = 独立进程，崩了不连坐

Layer 2 — 资源分区（空间隔离）
  文件系统: 每个Agent有自己的工作目录
  GPU: 按Agent分配显存配额
  端口: 按Agent分配端口段

Layer 3 — 资源仲裁（时间隔离）
  共享资源（屏幕/鼠标/麦克风）→ 操控令牌排队
```

### 6.2 操控令牌

屏幕/鼠标/键盘是独占资源，同一时间只能一个Agent操作。

```
Agent A: "我要操作桌面" → 框架: "令牌给你，30秒内用完"
Agent B: "我也要" → 框架: "排队，A还在用"
Agent A: "操作完了" → 框架: "令牌给B"
```

### 6.3 任务投递保证

三次握手：主体发任务 → Agent回ACK → 主体确认。
没收到ACK重发3次，3次都没ACK → 标记DEAD重启。

### 6.4 心跳

30秒没心跳 → WARNING
60秒没心跳 → 尝试ping
120秒没心跳 → 标记DEAD重启
重启3次还死 → 放弃通知用户

## 七、具身引擎

### 7.1 感知系统

跨平台方案：
- Windows: mss + win32gui
- Linux: mss + Xlib/Wayland
- macOS: mss + Quartz
- 通用回退: Pillow

分层感知（省Token）：
```
L1 — 像素级变化检测（纯算法，0 token）
     截图diff → 变化<5% → 不处理
L2 — OCR + 模板匹配（本地模型，0 token）
     变化>5% → OCR读文字 + 找按钮位置
L3 — LLM理解（花token，尽量少用）
     OCR搞不定 → 截图发给视觉模型，只发变化区域
```

### 7.2 操控系统

跨平台方案：
- Windows: ctypes调user32.dll
- Linux: python-xlib / pynput
- macOS: pyobjc / Quartz
- 浏览器: playwright（全平台）

### 7.3 语音系统

全部本地，0 token：
- STT: whisper.cpp（C库，全平台）
- TTS: piper-tts（C库，全平台）
- 唤醒词: openwakeword（Python）

状态机：IDLE → LISTENING → THINKING → SPEAKING
90%时间IDLE，几乎不消耗资源。

## 八、通信引擎

### 8.1 统一消息格式

所有平台的消息进来都统一为 Message 对象：
```
channel / sender / sender_name / chat_id / chat_type
content_type / content / media_url / reply_to / timestamp / raw
```

### 8.2 渠道覆盖

国内：微信/QQ/钉钉/飞书/企业微信/微博/小红书/抖音/B站/短信
国外：Telegram/Discord/WhatsApp/Slack/Signal/iMessage/Twitter/Instagram/Facebook/Line/Matrix/Mattermost/Teams/Email
通用：IRC/XMPP/WebSocket/Webhook/gRPC

### 8.3 多渠道路由

同时在线N个平台，消息自动路由到来源平台回复。

## 九、智能引擎

### 9.1 LLM适配

全模型覆盖：
- 国内：通义千问/智谱/百度/月之暗面/DeepSeek/MiniMax/讯飞/百川
- 国外：OpenAI/Anthropic/Google/Mistral/Cohere/xAI
- 聚合：OpenRouter/Together/Groq/SiliconFlow
- 本地：Ollama/vLLM/llama.cpp

智能路由：简单任务→小模型，复杂任务→大模型，本地能做→本地。
降级链：A挂了→B→C→本地→报错（进程不退出）。

### 9.2 极简配置

```toml
[llm]
deepseek = "sk-xxx"     # 一行配一个，框架自动补全base_url/模型名
```

优先级：环境变量 > config.toml > 自动探测 > 内置默认值

### 9.3 记忆系统

三级缓存：
```
L1 工作记忆（内存，当前任务）→ 任务结束清除
L2 短期记忆（文件/SQLite，当天）→ 每天consolidate到L3
L3 长期记忆（向量数据库，永久）→ 语义搜索，按需加载
```

记忆守护（从实战血泪史提炼）：
- WAL写入：记忆先写日志再写存储，崩了可恢复
- 自动checkpoint：每N分钟/每个重要操作后自动存档
- 身份保护：identity标记为PERMANENT，不可压缩
- 三份备份：内存→文件→数据库，任何一层丢了另两层恢复

### 9.4 Token治理

日预算 / 单任务预算 / 模型路由 / 响应缓存 / 上下文压缩。

Token消耗对比：
- 暴力模式：每秒截屏+每帧LLM = 8600万token/天 = 破产
- 节省模式：变化检测+OCR+必要时LLM = 20万token/天 = 可控

## 十、安全体系

### 七层防护

```
L7 审计层   — 所有操作可追溯，append-only日志
L6 通信安全 — 密钥加密存储（系统密钥链），日志自动遮盖
L5 输入安全 — 防prompt注入，不可信内容标记
L4 插件安全 — 权限声明/代码扫描/沙箱隔离
L3 网络安全 — 出站域名白名单/内容扫描/流量限制
L2 操作安全 — 三级分类（绿/黄/红），红色必须用户确认
L1 沙箱层   — 文件沙箱/进程沙箱/命令黑名单/资源配额
```

### 从实战踩坑提炼的15项防护

| # | 坑 | 框架防护 |
|---|-----|---------|
| 1 | 自杀式操作（Agent关自己） | 命令黑名单+进程保护+watchdog |
| 2 | 记忆丢失（上下文压缩） | WAL+checkpoint+三份备份 |
| 3 | 分身超时/丢失 | 三次握手+心跳带进度+结果校验 |
| 4 | 模型不可用 | 探测+降级链+热切换+不崩溃 |
| 5 | 长任务被kill | 后台任务+断点续传+可配超时 |
| 6 | 健康度崩溃无预警 | 系统监控+阈值告警+自愈 |
| 7 | 配置改错全崩 | schema校验+备份回滚+热加载 |
| 8 | 依赖静默失败 | 启动检查+明确报错+依赖隔离 |
| 9 | 文件冲突 | 文件锁+目录隔离+原子写入 |
| 10 | 安全软件杀进程 | watchdog重启+白名单引导 |
| 11 | 编码问题(BOM/GBK) | 统一UTF-8无BOM+编码检测 |
| 12 | 端口冲突 | 端口分区+启动前检测 |
| 13 | 僵尸进程 | PID文件+启动前清理 |
| 14 | 上下文窗口爆炸 | 自动压缩+摘要链+按需加载 |
| 15 | API限频 | 自动限速+排队+429处理 |

## 十一、扩展机制

### Skill（知识）

一个SKILL.md文件 = 一个技能。教Agent怎么做某事。

```
my-skill/
├── SKILL.md       ← 技能说明（必须）
├── tools.py       ← 附带代码（可选）
└── skill.toml     ← 元数据（可选）
```

### MCP（标准工具协议）

兼容Anthropic MCP标准，所有MCP Server直接接入。

```toml
[mcp]
filesystem = "npx -y @modelcontextprotocol/server-filesystem /home"
```

### Plugin（框架能力）

给框架加新能力：新Agent类型/新工具/新通信渠道/新LLM后端。

```python
class MyTool(ToolPlugin):
    name = "my_tool"
    def schema(self): ...
    async def execute(self, params, ctx): ...
```

三种机制对比：
- Skill = 会写文档就行 → 教Agent做事
- MCP = 会写函数就行 → 接入外部工具
- Plugin = 懂框架接口 → 加新能力

## 十二、开源/私有边界

### 开源（MIT License）

框架全部核心能力。C底座+Python运行时+四大引擎+插件系统+安全系统。

### 私有（不开源，通过插件接入）

用户私有技术，通过标准插件接口接入框架。

框架里零私有技术，一滴不漏。用户通过标准插件接口接入私有能力。
