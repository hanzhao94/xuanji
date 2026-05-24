# 玄机 (XuanJi) 开发路线图

> 使用开源工程方法论开发
> 进化式研发 + 分身并行 + 专家辩论 + 质检流水线 + 知识沉淀

## Phase 1：骨架 + C底座（1-2天）

**目标：框架能跑起来，能加载一个最简Agent**

- [ ] 项目结构搭建
- [ ] C底座 PAL层
  - [ ] oa_pal.h 统一接口定义
  - [ ] oa_pal_win.c Windows实现
  - [ ] oa_pal_unix.c Linux/macOS实现
- [ ] C底座核心模块
  - [ ] oa_bus.c 消息总线（无锁环形缓冲区）
  - [ ] oa_proc.c 进程管理
  - [ ] oa_heart.c 心跳检测
- [ ] Python运行时骨架
  - [ ] _ffi.py C绑定
  - [ ] runtime.py 主控
  - [ ] plugin.py 插件加载器
  - [ ] config.py 配置系统
- [ ] 最简示例：一个Agent说Hello

**验证标准：玄机 (XuanJi) run 能启动，Agent能收发消息**

## Phase 2：多Agent引擎（2-3天）

**目标：多Agent并行不冲突**

- [ ] C底座
  - [ ] oa_res.c 资源分区+租约
  - [ ] oa_lock.c 无锁队列
  - [ ] oa_sched.c 调度器
- [ ] Python
  - [ ] isolation.py 进程隔离+分区
  - [ ] arbiter.py 仲裁+操控令牌
  - [ ] bus.py 消息总线Python封装
- [ ] 安全内核
  - [ ] 命令黑名单
  - [ ] 进程自保护
  - [ ] watchdog

**验证标准：启动3个Agent，同时工作，互不干扰，kill 1个不影响其他**

## Phase 3：智能引擎（2-3天）

**目标：能接LLM，能记忆**

- [ ] LLM适配器
  - [ ] _base.py 统一接口
  - [ ] deepseek.py / openai.py / ollama.py
  - [ ] router.py 智能路由
  - [ ] fallback.py 降级链
  - [ ] cache.py 响应缓存
- [ ] 极简配置系统
  - [ ] 一行配一个API
  - [ ] 自动探测
  - [ ] 环境变量支持
- [ ] 记忆系统
  - [ ] store.py 三级缓存
  - [ ] guard.py 记忆守护（WAL+checkpoint）
- [ ] Token治理
  - [ ] governor.py 预算+计数

**验证标准：配一行deepseek key，Agent能对话，记忆重启不丢**

## Phase 4：具身引擎（3-4天）

**目标：Agent能看屏幕、操作电脑**

- [ ] 感知系统
  - [ ] 截屏（跨平台）
  - [ ] 变化检测（纯算法）
  - [ ] OCR（本地tesseract）
- [ ] 操控系统
  - [ ] 鼠标/键盘（跨平台）
  - [ ] 窗口管理
  - [ ] 浏览器（playwright）
- [ ] 语音系统
  - [ ] STT（whisper.cpp）
  - [ ] TTS（piper-tts）
  - [ ] 唤醒词

**验证标准：Agent能自动打开Chrome、搜索内容、截屏验证结果**

## Phase 5：通信引擎（3-4天）

**目标：Agent能通过IM和人交流**

- [ ] 通信基础
  - [ ] _base.py Channel+Message定义
  - [ ] router.py 多渠道路由
- [ ] 渠道实现（先做3个核心的）
  - [ ] telegram.py
  - [ ] qq.py
  - [ ] email.py
- [ ] 扩展更多渠道
  - [ ] discord.py / wechat.py / slack.py ...

**验证标准：Agent同时在线Telegram+QQ，消息自动路由**

## Phase 6：扩展系统（2天）

**目标：Skill + MCP + Plugin全通**

- [ ] Skill加载器
- [ ] MCP客户端
- [ ] MCP Server生成器
- [ ] Plugin发现+加载+沙箱
- [ ] CLI工具
  - [ ] 玄机 (XuanJi) init/run/skill/mcp/plugin

**验证标准：用户写一个SKILL.md，框架自动匹配使用**

## Phase 7：安全加固（2天）

**目标：七层安全防护全上线**

- [ ] L1 沙箱完善
- [ ] L2 操作分级+确认
- [ ] L3 网络白名单+出站扫描
- [ ] L4 插件代码扫描
- [ ] L5 prompt注入防护
- [ ] L6 密钥管理（系统密钥链）
- [ ] L7 审计日志

**验证标准：恶意命令被拦截，密钥不泄露，审计日志完整**

## Phase 8：开源准备（2天）

**目标：可以pip install，可以GitHub开源**

- [ ] 文档完善
  - [ ] README / 快速开始 / API文档
- [ ] 示例项目
  - [ ] 01-hello / 02-desktop / 03-voice / 04-multi-agent
- [ ] CI/CD
  - [ ] GitHub Actions
  - [ ] 预编译wheel (win/linux/mac × x64/arm64)
- [ ] 发布
  - [ ] PyPI: pip install 玄机 (XuanJi)
  - [ ] GitHub: 开源仓库

## 总估时

```
Phase 1-3（核心）：5-8天
Phase 4-5（能力）：6-8天
Phase 6-7（生态+安全）：4天
Phase 8（发布）：2天
总计：17-22天

用研发体系方法论（分身并行）：预计10-14天实际工期
```
