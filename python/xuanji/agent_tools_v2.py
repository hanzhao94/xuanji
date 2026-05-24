"""
xuanji Agent工具扩展 v2：感知 + 操控 + 语音 + OpenClaw导入

补全Agent工具链中遗漏的能力：
- 浏览器操控 (hands/browser.py) - 5个工具
- 桌面操控 (hands/_win.py) - 4个工具
- 截屏/视觉 (perception/) - 3个工具
- 语音 (voice/) - 2个工具
- OpenClaw workspace 导入器 - 1个工具
"""

import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 浏览器工具 (BrowserHands)
# ─────────────────────────────────────────────

def register_browser_tools(registry) -> None:
    """注册浏览器操控工具 (5个)"""
    try:
        from xuanji.hands.browser import BrowserHands
        _browser = BrowserHands()

        def _browser_navigate(url: str) -> str:
            try:
                _browser.open_url(url)
                return f"已打开: {url}"
            except Exception as e:
                return f"打开失败: {e}"

        registry.register("browser_navigate", "在浏览器中打开指定URL",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            _browser_navigate, "browser")

        def _browser_get_text() -> str:
            try:
                text = _browser.get_page_text()
                if len(text) > 3000:
                    return text[:3000] + f"\n...（共{len(text)}字，已截断）"
                return text
            except Exception as e:
                return f"获取失败: {e}"

        registry.register("browser_get_text", "获取当前浏览器页面正文文本",
            {"type": "object", "properties": {}, "required": []},
            _browser_get_text, "browser")

        def _browser_get_info() -> str:
            try:
                title = _browser.get_page_title()
                url = _browser.get_page_url()
                return f"标题: {title}\nURL: {url}"
            except Exception as e:
                return f"获取失败: {e}"

        registry.register("browser_get_info", "获取当前浏览器页面的标题和URL",
            {"type": "object", "properties": {}, "required": []},
            _browser_get_info, "browser")

        def _browser_fill(selector: str, text: str) -> str:
            try:
                _browser.fill_element(selector, text)
                return f"已在 {selector} 中输入: {text[:30]}{'...' if len(text) > 30 else ''}"
            except Exception as e:
                return f"输入失败: {e}"

        registry.register("browser_fill", "在网页指定CSS选择器的元素中输入文字",
            {"type": "object", "properties": {
                "selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]},
            _browser_fill, "browser")

        def _browser_click_el(selector: str) -> str:
            try:
                _browser.click_element(selector)
                return f"已点击: {selector}"
            except Exception as e:
                return f"点击失败: {e}"

        registry.register("browser_click", "点击网页上指定CSS选择器的元素",
            {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
            _browser_click_el, "browser")

        logger.info("Browser tools registered: 5 tools")
    except Exception as e:
        logger.warning(f"Browser tools registration failed: {e}")


# ─────────────────────────────────────────────
# 桌面操控工具 (WinHands)
# ─────────────────────────────────────────────

def register_hands_tools(registry) -> None:
    """注册桌面操控工具（鼠标/键盘）(4个)"""
    try:
        import platform
        system = platform.system()
        if system == "Windows":
            from xuanji.hands._win import WinHands
            _hands = WinHands()
        elif system == "Darwin":
            from xuanji.hands._darwin import DarwinHands
            _hands = DarwinHands()
        else:
            from xuanji.hands._linux import LinuxHands
            _hands = LinuxHands()

        def _mouse_move(x: int, y: int) -> str:
            try:
                _hands.move(x, y)
                return f"鼠标已移动到 ({x},{y})"
            except Exception as e:
                return f"移动失败: {e}"

        registry.register("mouse_move", "移动鼠标到屏幕指定坐标",
            {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]},
            _mouse_move, "desktop")

        def _mouse_click(x: int, y: int) -> str:
            try:
                _hands.click(x, y)
                return f"已点击 ({x},{y})"
            except Exception as e:
                return f"点击失败: {e}"

        registry.register("mouse_click", "鼠标点击指定坐标",
            {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]},
            _mouse_click, "desktop")

        def _keyboard_type(text: str) -> str:
            try:
                _hands.type_text(text)
                return f"已输入: {text[:30]}{'...' if len(text) > 30 else ''}"
            except Exception as e:
                return f"输入失败: {e}"

        registry.register("keyboard_type", "通过键盘输入文字到当前焦点窗口",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            _keyboard_type, "desktop")

        def _keyboard_hotkey(keys: str) -> str:
            try:
                key_list = [k.strip().lower() for k in keys.split("+")]
                _hands.hotkey(*key_list)
                return f"已按下: {keys}"
            except Exception as e:
                return f"快捷键失败: {e}"

        registry.register("keyboard_hotkey", "按下组合键，如 Ctrl+C, Alt+Tab",
            {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]},
            _keyboard_hotkey, "desktop")

        logger.info("Hands tools registered: 4 tools")
    except Exception as e:
        logger.warning(f"Hands tools registration failed: {e}")


# ─────────────────────────────────────────────
# 截屏/视觉工具 (Perception)
# ─────────────────────────────────────────────

def register_perception_tools(registry) -> None:
    """注册截屏/视觉工具 (3个)"""
    try:
        import platform
        system = platform.system()
        if system == "Windows":
            from xuanji.perception._win import WinPerception
            _perc = WinPerception()
        elif system == "Darwin":
            from xuanji.perception._darwin import DarwinPerception
            _perc = DarwinPerception()
        else:
            from xuanji.perception._linux import LinuxPerception
            _perc = LinuxPerception()

        def _screenshot(path: str = "") -> str:
            try:
                if not path:
                    path = os.path.join(tempfile.gettempdir(), "screenshot.png")
                img = _perc.screenshot()
                if hasattr(img, 'save'):
                    img.save(path)
                    return f"截图已保存: {path}"
                return "截图完成"
            except Exception as e:
                return f"截图失败: {e}"

        registry.register("screenshot", "截取全屏画面并保存为图片",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
            _screenshot, "perception")

        def _screen_info() -> str:
            try:
                size = _perc.screen_size()
                return f"屏幕分辨率: {size[0]}x{size[1]}"
            except Exception as e:
                return f"获取失败: {e}"

        registry.register("screen_info", "获取当前屏幕分辨率",
            {"type": "object", "properties": {}, "required": []},
            _screen_info, "perception")

        def _screen_region(x: int, y: int, width: int, height: int, path: str = "") -> str:
            try:
                if not path:
                    path = os.path.join(tempfile.gettempdir(), "screen_region.png")
                img = _perc.screen_region(x, y, width, height)
                if hasattr(img, 'save'):
                    img.save(path)
                    return f"区域截图已保存: {path} ({x},{y},{width}x{height})"
                return "区域截图完成"
            except Exception as e:
                return f"区域截图失败: {e}"

        registry.register("screen_region", "截取屏幕指定区域的画面",
            {"type": "object", "properties": {
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
                "path": {"type": "string"}}, "required": ["x", "y", "width", "height"]},
            _screen_region, "perception")

        logger.info("Perception tools registered: 3 tools")
    except Exception as e:
        logger.warning(f"Perception tools registration failed: {e}")


# ─────────────────────────────────────────────
# 语音工具 (Voice)
# ─────────────────────────────────────────────

def register_voice_tools(registry) -> None:
    """注册语音工具 (2个)"""
    try:
        from xuanji.voice.tts import TextToSpeech
        _tts = TextToSpeech()

        def _text_to_speech(text: str, output: str = "") -> str:
            try:
                if not output:
                    output = os.path.join(tempfile.gettempdir(), "speech.wav")
                _tts.speak(text)  # returns bytes
                # The actual speak() returns bytes, save them
                audio_bytes = _tts.speak(text)
                with open(output, 'wb') as f:
                    f.write(audio_bytes)
                return f"语音已生成: {output} ({len(text)}字)"
            except Exception as e:
                return f"TTS失败: {e}"

        registry.register("text_to_speech", "将文字转换为语音文件",
            {"type": "object", "properties": {
                "text": {"type": "string"},
                "output": {"type": "string"}}, "required": ["text"]},
            _text_to_speech, "voice")
    except Exception as e:
        logger.warning(f"TTS registration failed: {e}")

    try:
        from xuanji.voice.stt import SpeechToText
        _stt = SpeechToText()

        def _speech_to_text(audio_path: str) -> str:
            try:
                with open(audio_path, 'rb') as f:
                    audio_bytes = f.read()
                result = _stt.transcribe(audio_bytes)
                return f"识别结果: {result}"
            except Exception as e:
                return f"STT失败: {e}"

        registry.register("speech_to_text", "将语音文件转换为文字",
            {"type": "object", "properties": {"audio_path": {"type": "string"}}, "required": ["audio_path"]},
            _speech_to_text, "voice")
    except Exception as e:
        logger.warning(f"STT registration failed: {e}")

    logger.info("Voice tools registered")


# ─────────────────────────────────────────────
# OpenClaw Workspace 导入器
# ─────────────────────────────────────────────

def import_openclaw_workspace(workspace_path: str, registry=None) -> dict:
    """将OpenClaw workspace的数据导入玄玑
    
    扫描OpenClaw workspace，自动转换注册到玄玑Agent。
    用户只需要提供OpenClaw workspace目录路径。
    """
    stats = {
        "workspace": workspace_path,
        "skills_imported": 0,
        "memory_imported": 0,
        "personas_imported": 0,
        "workflows_imported": 0,
        "errors": [],
    }

    if not os.path.exists(workspace_path):
        stats["errors"].append(f"Workspace不存在: {workspace_path}")
        return stats

    # 1. 扫描 skills → 注册为Agent工具
    skills_dirs = []
    for root, dirs, files in os.walk(workspace_path):
        depth = root[len(workspace_path):].count(os.sep)
        if depth > 2:
            continue
        for d in dirs:
            skill_md = os.path.join(root, d, "SKILL.md")
            if os.path.isfile(skill_md):
                skills_dirs.append(os.path.join(root, d))

    for sd in skills_dirs:
        skill_md = os.path.join(sd, "SKILL.md")
        try:
            item = os.path.basename(sd)
            with open(skill_md, 'r', encoding='utf-8') as f:
                content = f.read()
            desc = content.split('\n')[0].strip('#').strip()[:200]
            if not desc:
                desc = f"OpenClaw skill: {item}"

            def _make_skill(path, description):
                def _run_skill(query: str = "") -> str:
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            return f.read()
                    except Exception as e:
                        return f"读取失败: {e}"
                _run_skill.__doc__ = description
                return _run_skill

            safe_name = f"skill_{item.replace('-', '_').replace(' ', '_')}"
            registry.register(safe_name, desc,
                {"type": "object", "properties": {"query": {"type": "string"}}, "required": []},
                _make_skill(skill_md, desc), "openclaw_skill")
            stats["skills_imported"] += 1
        except Exception as e:
            stats["errors"].append(f"Skill导入失败 {item}: {e}")

    # 2. 导入 MEMORY.md 等
    memory_files = ["MEMORY.md", "memory/SESSION_BRIDGE.md", "memory/DECISIONS.md"]
    for mf in memory_files:
        full = os.path.join(workspace_path, mf.replace("/", os.sep))
        if os.path.exists(full):
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    content = f.read()
                key = mf.replace("/", "_").replace(".md", "")
                if registry and registry.get("remember"):
                    registry.execute("remember", key=key, value=content[:2000], category="openclaw_import")
                stats["memory_imported"] += 1
            except Exception as e:
                stats["errors"].append(f"记忆导入失败 {mf}: {e}")

    # 3. 导入 persona 文件
    for pf in ["SOUL.md", "USER.md", "AGENTS.md", "IDENTITY.md"]:
        full = os.path.join(workspace_path, pf)
        if os.path.exists(full):
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    content = f.read()
                key = f"persona_{pf.replace('.md', '')}"
                if registry and registry.get("remember"):
                    registry.execute("remember", key=key, value=content[:2000], category="persona")
                stats["personas_imported"] += 1
            except Exception as e:
                stats["errors"].append(f"Persona导入失败 {pf}: {e}")

    # 4. 扫描 workflow 配置
    for wf in ["workflow.json", "workflows.json"]:
        if os.path.exists(os.path.join(workspace_path, wf)):
            stats["workflows_imported"] += 1

    logger.info(f"OpenClaw import complete: {stats}")
    return stats


def register_openclaw_tools(registry, workspace_path: Optional[str] = None) -> dict:
    """注册OpenClaw相关工具 + 可选导入workspace"""
    stats = {"tools_registered": 0, "import": None}

    def _import_workspace(path: str) -> str:
        result = import_openclaw_workspace(path, registry=registry)
        lines = [f"OpenClaw workspace导入完成:"]
        lines.append(f"  Skills: {result['skills_imported']}")
        lines.append(f"  记忆: {result['memory_imported']}")
        lines.append(f"  Personas: {result['personas_imported']}")
        if result["errors"]:
            lines.append(f"  错误: {len(result['errors'])}")
            for err in result["errors"][:3]:
                lines.append(f"    - {err}")
        return "\n".join(lines)

    registry.register("import_openclaw", "导入OpenClaw workspace的所有数据（skills、记忆、persona、工作流）到玄玑",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        _import_workspace, "openclaw")
    stats["tools_registered"] += 1

    if workspace_path and os.path.exists(workspace_path):
        stats["import"] = import_openclaw_workspace(workspace_path, registry=registry)

    return stats


# ─────────────────────────────────────────────
# 一键创建终极Agent
# ─────────────────────────────────────────────

def create_ultimate_agent(llm_router, model: Optional[str] = None,
                           memory_manager=None, channel_router=None,
                           max_steps: int = 20,
                           openclaw_workspace: Optional[str] = None) -> 'AgentRunner':
    """创建包含所有可用能力的终极Agent
    
    包含：
    - 9个基础工具（搜索/文件/Shell/计算/天气）
    - 4个记忆工具
    - 2个渠道工具（如果提供channel_router）
    - 5个浏览器工具
    - 4个桌面操控工具
    - 3个截屏工具
    - 2个语音工具
    - 1个OpenClaw导入工具
    总计：30 个工具
    """
    from xuanji.agent_runner import AgentRunner
    from xuanji.natural_agent import register_builtin_tools
    from xuanji.agent_tools import register_memory_tools, register_channel_tools

    runner = AgentRunner(llm_router, model=model, max_steps=max_steps)

    register_builtin_tools(runner.registry)
    register_memory_tools(runner.registry, memory_manager)

    if channel_router:
        register_channel_tools(runner.registry, channel_router)

    register_browser_tools(runner.registry)
    register_hands_tools(runner.registry)
    register_perception_tools(runner.registry)
    register_voice_tools(runner.registry)
    register_openclaw_tools(runner.registry, workspace_path=openclaw_workspace)

    return runner
