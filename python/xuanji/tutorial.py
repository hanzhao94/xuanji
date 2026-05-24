"""
xuanji 交互式教程引擎

用法:
  xuanji tutorial list              列出所有教程
  xuanji tutorial start <教程名>     开始教程
  xuanji tutorial next              下一步
  xuanji tutorial status            查看进度
  xuanji tutorial reset <教程名>     重置进度

每个教程包含多个步骤，每步一个任务+验证+提示。
进度保存在 ~/.xuanji/tutorials/
"""

import json
import os
import sys
import time
from pathlib import Path


# ============================================================
# 教程内容定义
# ============================================================

TUTORIALS = {
    "hello-world": {
        "title": "Hello World",
        "description": "创建你的第一个xuanji插件",
        "steps": [
            {
                "id": "intro",
                "title": "什么是xuanji？",
                "content": """xuanji 是一个插件化的Agent框架。核心概念：
  - Plugin（插件）：Agent/Tool/Skill 都是插件
  - Runtime（运行时）：加载插件并调度执行
  - Context（上下文）：Agent之间的通信桥梁

你的第一个任务：创建一个Hello World插件。""",
                "task": "运行命令: xuanji init my-first-agent",
                "verify": "verify_hello_init",
                "hint": "打开终端，输入 xuanji init my-first-agent",
            },
            {
                "id": "config",
                "title": "配置LLM",
                "content": """项目创建后，打开 config.toml 文件。
在 [llm] 段添加你的LLM密钥：

  [llm]
  deepseek = "sk-your-key-here"

支持多个LLM同时配置，框架会自动fallback。""",
                "task": "编辑 config.toml，在[llm]段添加至少一个LLM配置",
                "verify": "verify_llm_config",
                "hint": "用任何文本编辑器打开 my-first-agent/config.toml",
            },
            {
                "id": "run",
                "title": "运行Agent",
                "content": """配置完成后，启动运行时：

  cd my-first-agent
  xuanji run

你会看到Agent启动日志。默认会加载 hello 示例插件。
按 Ctrl+C 停止。""",
                "task": "进入项目目录，运行 xuanji run，看到启动日志后停止",
                "verify": "verify_run",
                "hint": "cd my-first-agent && xuanji run，看到日志后 Ctrl+C",
            },
            {
                "id": "test",
                "title": "测试回复",
                "content": """Hello World插件已经配置好了！
它会自动回复你发送的消息。

试试给它发一条消息，看看回复效果。
如果配置了Telegram/QQ渠道，可以直接在聊天软件中测试。""",
                "task": "给Agent发一条消息，收到回复即完成",
                "verify": "verify_message",
                "hint": "在配置好的渠道中发送'你好'测试",
            },
        ],
    },
    "config-llm": {
        "title": "配置LLM",
        "description": "深入理解LLM配置和多个模型的使用",
        "steps": [
            {
                "id": "intro",
                "title": "LLM配置基础",
                "content": """xuanji支持多种LLM后端：
  - DeepSeek: deepseek = "sk-xxx"
  - OpenAI: openai = "sk-xxx"
  - Ollama: ollama = "localhost" （本地部署）
  - 其他兼容OpenAI API的模型

配置优先级：config.toml > 环境变量 > 默认值""",
                "task": "查看 config.toml 中的 [llm] 段配置",
                "verify": "verify_llm_read",
                "hint": "打开 config.toml 找到 [llm] 段",
            },
            {
                "id": "multi",
                "title": "多LLM配置",
                "content": """你可以同时配置多个LLM：

  [llm]
  deepseek = "sk-xxx"
  openai = "sk-xxx"
  ollama = "localhost"

框架会自动选择可用的LLM。如果第一个失败，自动fallback到下一个。""",
                "task": "在 config.toml 中配置至少2个LLM（或1个LLM+1个Ollama）",
                "verify": "verify_multi_llm",
                "hint": "添加多个LLM配置，用 # 注释掉暂时不用的",
            },
            {
                "id": "test",
                "title": "测试LLM连接",
                "content": """配置完成后，运行Agent测试LLM连接：

  xuanji run

查看启动日志，确认LLM加载成功。
如果看到错误信息，检查API密钥是否正确。""",
                "task": "运行 xuanji run，确认LLM加载成功",
                "verify": "verify_llm_connection",
                "hint": "启动后查看日志中是否有LLM相关错误",
            },
        ],
    },
    "create-skill": {
        "title": "创建Skill",
        "description": "学习如何创建自定义Skill扩展Agent能力",
        "steps": [
            {
                "id": "intro",
                "title": "什么是Skill？",
                "content": """Skill是xuanji的能力扩展模块。
一个Skill包含：
  - SKILL.md：指导Agent如何执行任务
  - skill.toml：元数据配置
  - 可选的Python代码

Skill通过关键词触发，Agent会自动匹配并执行。""",
                "task": "运行: xuanji skill create my-skill",
                "verify": "verify_skill_create",
                "hint": "在终端中运行 skill create 命令",
            },
            {
                "id": "edit",
                "title": "编辑SKILL.md",
                "content": """打开 skills/my-skill/SKILL.md，添加你的指导内容：

  # 天气查询
  
  当用户询问天气时：
  1. 提取城市名
  2. 调用天气API
  3. 格式化回复

SKILL.md用自然语言编写，Agent会理解并执行。""",
                "task": "编辑 SKILL.md，添加至少3个执行步骤",
                "verify": "verify_skill_edit",
                "hint": "用文本编辑器打开 SKILL.md 并编辑",
            },
            {
                "id": "install",
                "title": "安装Skill",
                "content": """创建完成后，安装到全局目录：

  xuanji skill install skills/my-skill

安装后，Skill会被复制到 ~/.xuanji/skills/
所有项目都可以使用已安装的Skill。""",
                "task": "运行 xuanji skill install skills/my-skill",
                "verify": "verify_skill_install",
                "hint": "在项目根目录运行安装命令",
            },
            {
                "id": "verify",
                "title": "验证安装",
                "content": """安装完成后，列出已安装的Skill：

  xuanji skill list

确认你的Skill出现在列表中。
现在Agent可以根据关键词自动触发你的Skill了。""",
                "task": "运行 xuanji skill list，确认Skill已安装",
                "verify": "verify_skill_list",
                "hint": "检查列表中是否包含 my-skill",
            },
        ],
    },
    "multi-agent": {
        "title": "多Agent协作",
        "description": "学习如何配置多个Agent协同工作",
        "steps": [
            {
                "id": "intro",
                "title": "多Agent架构",
                "content": """xuanji支持多Agent协作：
  - 每个Agent是独立的插件
  - Agent之间通过Context通信
  - 可以配置不同的角色和能力

典型场景：客服Agent + 订单Agent + 支付Agent""",
                "task": "创建两个Agent插件: agent-a 和 agent-b",
                "verify": "verify_multi_agent_create",
                "hint": "使用 xuanji create agent agent-a 和 xuanji create agent agent-b",
            },
            {
                "id": "configure",
                "title": "配置Agent",
                "content": """编辑每个Agent的 agent.py：

Agent A（客服）：
  - 接收用户消息
  - 简单问题直接回复
  - 复杂问题转发给Agent B

Agent B（专家）：
  - 接收Agent A转发的复杂问题
  - 调用LLM深度分析
  - 返回结果给Agent A""",
                "task": "编辑两个Agent的agent.py，实现基本逻辑",
                "verify": "verify_agent_config",
                "hint": "参考 plugins/agents/ 目录下的模板",
            },
            {
                "id": "run",
                "title": "运行多Agent",
                "content": """启动运行时，框架会自动加载所有Agent：

  xuanji run

两个Agent会同时运行，通过Bus进行通信。
查看日志确认两个Agent都成功加载。""",
                "task": "运行 xuanji run，确认两个Agent都加载成功",
                "verify": "verify_multi_agent_run",
                "hint": "日志中应该看到两个Agent的加载信息",
            },
        ],
    },
    "embodied": {
        "title": "具身操作",
        "description": "学习如何让Agent操作物理设备",
        "steps": [
            {
                "id": "intro",
                "title": "什么是具身操作？",
                "content": """具身操作（Embodied）让Agent能控制物理设备：
  - 摄像头：拍照、识别
  - 机械臂：抓取、移动
  - 传感器：温度、湿度
  - 其他IoT设备

通过hands模块实现设备控制。""",
                "task": "查看 hands 目录了解可用的设备驱动",
                "verify": "verify_hands_explore",
                "hint": "查看 xuanji/hands/ 目录下的文件",
            },
            {
                "id": "config",
                "title": "配置设备",
                "content": """在 config.toml 中配置设备：

  [hands]
  # 示例：配置摄像头
  camera = "usb:0"
  
  # 示例：配置机械臂
  # arm = "serial:/dev/ttyUSB0"

根据实际设备修改配置。""",
                "task": "在 config.toml 中配置至少一个设备（或模拟设备）",
                "verify": "verify_embodied_config",
                "hint": "参考 hands/ 目录下的示例配置",
            },
            {
                "id": "test",
                "title": "测试设备",
                "content": """启动运行时，测试设备连接：

  xuanji run

在Agent对话中发送控制指令：
  "拍一张照片"
  "读取温度"

Agent会调用对应的设备驱动执行操作。""",
                "task": "运行Agent，发送一条设备控制指令",
                "verify": "verify_embodied_test",
                "hint": "发送'拍照'或'读取传感器'等指令",
            },
        ],
    },
}

# 验证函数映射
VERIFIERS = {}


def _get_tutorial_dir() -> Path:
    """获取教程进度存储目录"""
    d = Path.home() / ".xuanji" / "tutorials"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_progress_path(tutorial_id: str) -> Path:
    return _get_tutorial_dir() / f"{tutorial_id}.json"


def _load_progress(tutorial_id: str) -> dict:
    p = _get_progress_path(tutorial_id)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"current_step": 0, "completed": [], "started_at": None, "last_active": None}


def _save_progress(tutorial_id: str, progress: dict):
    p = _get_progress_path(tutorial_id)
    p.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")


# ============================================================
# 验证函数
# ============================================================

def verify_hello_init(args) -> bool:
    """验证Hello World初始化"""
    return (Path("my-first-agent") / "config.toml").is_file()


def verify_llm_config(args) -> bool:
    """验证LLM配置"""
    config_path = Path("my-first-agent") / "config.toml"
    if not config_path.is_file():
        return False
    content = config_path.read_text(encoding="utf-8")
    return "[llm]" in content and "sk-" in content


def verify_run(args) -> bool:
    """验证运行"""
    # 只要项目存在且配置了，就认为可以运行
    config_path = Path("my-first-agent") / "config.toml"
    return config_path.is_file()


def verify_message(args) -> bool:
    """验证消息回复"""
    # 消息测试需要用户手动确认
    return True


def verify_llm_read(args) -> bool:
    return True


def verify_multi_llm(args) -> bool:
    return True


def verify_llm_connection(args) -> bool:
    return True


def verify_skill_create(args) -> bool:
    return (Path("skills") / "my-skill" / "SKILL.md").is_file()


def verify_skill_edit(args) -> bool:
    skill_md = Path("skills") / "my-skill" / "SKILL.md"
    if not skill_md.is_file():
        return False
    content = skill_md.read_text(encoding="utf-8")
    return len(content) > 100


def verify_skill_install(args) -> bool:
    installed = Path.home() / ".xuanji" / "skills" / "my-skill"
    return installed.is_dir()


def verify_skill_list(args) -> bool:
    return True


def verify_multi_agent_create(args) -> bool:
    a = (Path("plugins") / "agents" / "agent-a").is_dir()
    b = (Path("plugins") / "agents" / "agent-b").is_dir()
    return a and b


def verify_agent_config(args) -> bool:
    return True


def verify_multi_agent_run(args) -> bool:
    return True


def verify_hands_explore(args) -> bool:
    return True


def verify_embodied_config(args) -> bool:
    return True


def verify_embodied_test(args) -> bool:
    return True


# 注册验证器
import sys as _sys
_current_module = _sys.modules[__name__]
for _name in dir(_current_module):
    if _name.startswith("verify_"):
        VERIFIERS[_name] = getattr(_current_module, _name)
del _sys, _current_module, _name


# ============================================================
# TutorialEngine 类
# ============================================================

class TutorialEngine:
    """交互式教程引擎"""

    def __init__(self):
        self.tutorials = TUTORIALS
        self.verifiers = VERIFIERS

    def list_tutorials(self):
        """列出所有教程"""
        print("📚 可用教程:")
        print()
        for tid, t in self.tutorials.items():
            progress = _load_progress(tid)
            total = len(t["steps"])
            done = len(progress.get("completed", []))
            status = "✅ 已完成" if done >= total else f"📖 {done}/{total}"
            print(f"  📝 {tid}")
            print(f"     {t['title']} — {t['description']}")
            print(f"     进度: {status}")
            print()

    def start(self, tutorial_id: str):
        """开始教程"""
        if tutorial_id not in self.tutorials:
            print(f"❌ 教程不存在: {tutorial_id}")
            print(f"   可用教程: {', '.join(self.tutorials.keys())}")
            return

        tutorial = self.tutorials[tutorial_id]
        progress = _load_progress(tutorial_id)

        if not progress["started_at"]:
            progress["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            progress["last_active"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_progress(tutorial_id, progress)
            print(f"🚀 开始教程: {tutorial['title']}")
        else:
            done = len(progress.get("completed", []))
            total = len(tutorial["steps"])
            if done >= total:
                print(f"✅ 教程 '{tutorial['title']}' 已完成！")
                return
            print(f"📖 继续教程: {tutorial['title']} (已完成 {done}/{total})")

        self._show_step(tutorial_id, progress)

    def next_step(self, tutorial_id: str):
        """下一步"""
        if tutorial_id not in self.tutorials:
            print(f"❌ 教程不存在: {tutorial_id}")
            return

        progress = _load_progress(tutorial_id)
        if not progress["started_at"]:
            print("❌ 请先开始教程: xuanji tutorial start " + tutorial_id)
            return

        tutorial = self.tutorials[tutorial_id]
        steps = tutorial["steps"]
        current = progress.get("current_step", 0)

        if current >= len(steps):
            print(f"🎉 恭喜！教程 '{tutorial['title']}' 已完成！")
            return

        self._show_step(tutorial_id, progress)

    def reset(self, tutorial_id: str):
        """重置教程进度"""
        if tutorial_id not in self.tutorials:
            print(f"❌ 教程不存在: {tutorial_id}")
            return

        progress_path = _get_progress_path(tutorial_id)
        if progress_path.is_file():
            progress_path.unlink()
        print(f"🔄 教程 '{tutorial_id}' 进度已重置")

    def status(self):
        """查看所有教程进度"""
        print("📊 教程进度:")
        print()
        for tid, t in self.tutorials.items():
            progress = _load_progress(tid)
            total = len(t["steps"])
            done = len(progress.get("completed", []))
            pct = int(done / total * 100) if total > 0 else 0
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            started = progress.get("started_at", "未开始")
            print(f"  {tid}: [{bar}] {pct}% ({done}/{total})")
            print(f"     开始于: {started}")
            print()

    def _show_step(self, tutorial_id: str, progress: dict):
        """显示当前步骤"""
        tutorial = self.tutorials[tutorial_id]
        steps = tutorial["steps"]
        current = progress.get("current_step", 0)

        if current >= len(steps):
            print(f"🎉 恭喜！教程 '{tutorial['title']}' 已完成！")
            return

        step = steps[current]
        print(f"\n{'='*60}")
        print(f"📖 {tutorial['title']} — 第 {current+1}/{len(steps)} 步")
        print(f"   {step['title']}")
        print(f"{'='*60}")
        print()
        print(step["content"])
        print()
        print(f"📋 任务: {step['task']}")
        print()

        # 等待用户确认
        print("输入 'done' 完成任务并继续")
        print("输入 'hint' 查看提示")
        print("输入 'quit' 保存进度并退出")
        print()

        while True:
            try:
                choice = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n💾 进度已保存")
                return

            if choice == "done":
                self._complete_step(tutorial_id, progress, current)
                break
            elif choice == "hint":
                print(f"\n💡 提示: {step['hint']}")
                print()
            elif choice == "quit":
                progress["last_active"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _save_progress(tutorial_id, progress)
                print("💾 进度已保存，下次继续！")
                return
            else:
                print("请输入 'done' / 'hint' / 'quit'")

    def _complete_step(self, tutorial_id: str, progress: dict, step_index: int):
        """完成当前步骤"""
        tutorial = self.tutorials[tutorial_id]
        step = tutorial["steps"][step_index]
        step_id = step["id"]

        # 记录完成
        if step_id not in progress.get("completed", []):
            if "completed" not in progress:
                progress["completed"] = []
            progress["completed"].append(step_id)

        progress["current_step"] = step_index + 1
        progress["last_active"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_progress(tutorial_id, progress)

        print(f"\n✅ 步骤完成: {step['title']}")

        # 检查是否全部完成
        if progress["current_step"] >= len(tutorial["steps"]):
            print(f"\n🎉 恭喜！教程 '{tutorial['title']}' 全部完成！")
            print(f"   你已掌握xuanji基础操作。")
        else:
            print(f"   下一步: {tutorial['steps'][progress['current_step']]['title']}")
            print(f"   运行 'xuanji tutorial next' 继续")
