"""
xuanji 交互对话模式 — 开箱即用

任何人装上就能跟AI聊天，不需要配API Key，不需要懂代码。

用法:
    xuanji chat                    # 自动检测最佳LLM，开始对话
    xuanji --chat                  # 同上
"""

import json
import os
import sys
import time
import urllib.request
from typing import List, Optional

from xuanji.llm._base import BaseLLMAdapter, ChatResponse, LLMError


# ─────────────────────────────────────────────
# LLM 自动检测
# ─────────────────────────────────────────────

def _detect_ollama() -> Optional[BaseLLMAdapter]:
    """检测Ollama是否在线并返回适配器"""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    # 选一个最好的模型
                    from xuanji.llm.ollama import OllamaAdapter
                    adapter = OllamaAdapter("ollama", {
                        "base_url": "http://localhost:11434",
                        "model": _pick_best_model(models),
                    })
                    adapter._models = models
                    return adapter, models
    except Exception:
        pass
    return None, []


def _pick_best_model(models: List[str]) -> str:
    """从可用模型中选一个最好的"""
    # 优先级：qwen > gemma > llama > 其他
    prefs = ["qwen3.6", "qwen3.5", "qwen2.5", "gemma2", "gemma", "llama3", "llama"]
    for pref in prefs:
        for m in models:
            if pref in m.lower():
                return m
    return models[0]


def _detect_cloud_api() -> Optional[BaseLLMAdapter]:
    """检测环境变量中的云API Key"""
    # DeepSeek
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key and key != "sk-xxx":
        from xuanji.llm.deepseek_adapter import DeepSeekAdapter
        return DeepSeekAdapter("deepseek", {"api_key": key}), "deepseek-chat"

    # OpenAI兼容
    key = os.environ.get("OPENAI_API_KEY", "")
    if key and key != "sk-xxx":
        from xuanji.llm.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter("openai", {"api_key": key}), "gpt-3.5-turbo"

    # 通义千问
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if key and key != "sk-xxx":
        from xuanji.llm.dashscope_adapter import DashScopeAdapter
        return DashScopeAdapter("dashscope", {"api_key": key}), "qwen-turbo"

    # 智谱
    key = os.environ.get("ZHIPU_API_KEY", "")
    if key and key != "sk-xxx":
        from xuanji.llm.zhipu_adapter import ZhipuAdapter
        return ZhipuAdapter("zhipu", {"api_key": key}), "glm-4-flash"

    return None, None


# ─────────────────────────────────────────────
# 简单对话循环
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """你是玄机 (XuanJi)，一个智能助手。
- 用中文回答
- 简洁明了
- 不知道的就说不知道"""

WELCOME = """
╔══════════════════════════════════════════╗
║  玄机 (XuanJi) v1.0.4                    ║
║  输入问题，回车发送                      ║
║  输入 quit/exit/退出 结束对话            ║
╚══════════════════════════════════════════╝
"""

QUIT_WORDS = {"quit", "exit", "退出", "q"}


async def chat_loop(adapter: BaseLLMAdapter, model: str,
                    system_prompt: Optional[str] = None):
    """交互对话循环"""
    system = system_prompt or SYSTEM_PROMPT
    history = [{"role": "system", "content": system}]

    print(WELCOME)
    print(f"🤖 使用模型: {adapter.name} / {model}")
    print(f"{'='*50}\n")

    total_msgs = 0
    total_tokens = 0

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in QUIT_WORDS:
            print(f"\n👋 再见！共对话{total_msgs}轮")
            break

        # 添加到历史
        history.append({"role": "user", "content": user_input})
        total_msgs += 1

        # 调用LLM
        start = time.time()
        try:
            resp = await adapter.chat_response(history, model=model)
            reply = resp.content or ""
            if resp.usage:
                total_tokens += resp.usage.get("total_tokens", 0)
        except LLMError as e:
            reply = f"❌ 出错: {e}"
        except Exception as e:
            reply = f"❌ 未知错误: {type(e).__name__}: {e}"

        elapsed = time.time() - start

        # 打印回复
        print(f"\n🤖 {reply}")
        print(f"   ⏱ {elapsed:.1f}s")
        print()

        # 添加到历史
        history.append({"role": "assistant", "content": reply})

        # 限制历史长度（防止token溢出）
        if len(history) > 20:
            # 保留system + 最近18条
            history = [history[0]] + history[-18:]


def start_chat():
    """入口函数 — 同步启动"""
    import asyncio

    # 1. 先试Ollama（免费，本地）
    print("🔍 正在检测可用模型...")
    ollama, models = _detect_ollama()
    if ollama:
        print(f"✅ 找到 Ollama ({len(models)}个模型)")
        model = _pick_best_model(models)
        print(f"   使用: {model}")
        asyncio.run(chat_loop(ollama, model))
        return

    # 2. 试云API
    print("   Ollama未运行，检测云API...")
    cloud, model = _detect_cloud_api()
    if cloud:
        print(f"✅ 找到云API: {cloud.name}")
        asyncio.run(chat_loop(cloud, model))
        return

    # 3. 都没有 — 提示用户
    print()
    print("❌ 未找到可用的LLM后端")
    print()
    print("请选择一种方式:")
    print()
    print("  方式1（推荐，免费）: 安装Ollama")
    print("    1. 访问 https://ollama.com 下载安装")
    print("    2. 下载模型: ollama pull qwen3.5:9b")
    print("    3. 重新运行: xuanji chat")
    print()
    print("  方式2: 配置云API Key")
    print("    设置环境变量:")
    print("    $env:DEEPSEEK_API_KEY='sk-xxx'")
    print("    或编辑 config.toml")
    print()
    print("  方式3: 使用完整Runtime")
    print("    cd D:\\openagent")
    print("    pip install -e .")
    print("    xuanji run")
