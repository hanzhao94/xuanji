"""
xuanji 配置系统

核心原则：用户给key，框架管其他一切。
一行配一个API，框架自动补全base_url/模型名/认证方式。
"""

from typing import Any, Dict, Optional


# 预置所有主流API的默认配置
# 用户不需要知道base_url/模型ID/认证方式

PROVIDER_DEFAULTS = {
    # === 国内 ===
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "max_tokens": 8192,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "max_tokens": 8192,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "max_tokens": 8192,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "MiniMax-Text-01",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "spark": {
        "base_url": "https://spark-api-open.xf-yun.com/v1",
        "default_model": "generalv3.5",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "baichuan": {
        "base_url": "https://api.baichuan-ai.com/v1",
        "default_model": "Baichuan4",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "yi": {
        "base_url": "https://api.lingyiwanwu.com/v1",
        "default_model": "yi-lightning",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "stepfun": {
        "base_url": "https://api.stepfun.com/v1",
        "default_model": "step-2-16k",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-1.5-pro-32k",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "hunyuan": {
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "default_model": "hunyuan-turbo",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "sensenova": {
        "base_url": "https://api.sensenova.cn/compatible-mode/v1",
        "default_model": "SenseChat-5",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    
    # === 国外 ===
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "auth_header": "x-api-key",
        "auth_prefix": "",  # Anthropic不需要Bearer前缀
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.0-flash",
        "max_tokens": 4096,
        "auth_header": "x-goog-api-key",
        "auth_prefix": "",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "cohere": {
        "base_url": "https://api.cohere.ai/v2",
        "default_model": "command-r-plus",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-3",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "perplexity": {
        "base_url": "https://api.perplexity.ai",
        "default_model": "sonar-pro",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "default_model": "llama-3.3-70b",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "sambanova": {
        "base_url": "https://api.sambanova.ai/v1",
        "default_model": "Meta-Llama-3.3-70B-Instruct",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "novita": {
        "base_url": "https://api.novita.ai/v3/openai",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    
    # === 聚合平台 ===
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "auto",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "auto",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "auto",
        "max_tokens": 4096,
        "auth_header": "Authorization",
        "auth_prefix": "Bearer",
    },
    
    # === 本地 ===
    "ollama": {
        "base_url": "http://localhost:11434",
        "default_model": "auto",  # 自动扫描已有模型
        "max_tokens": 4096,
        "auth_header": None,
        "auth_prefix": None,
    },
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "default_model": "auto",
        "max_tokens": 4096,
        "auth_header": None,
        "auth_prefix": None,
    },
    "llamacpp": {
        "base_url": "http://localhost:8080/v1",
        "default_model": "auto",
        "max_tokens": 4096,
        "auth_header": None,
        "auth_prefix": None,
    },
}


# 通信渠道默认配置
CHANNEL_DEFAULTS = {
    "telegram": {
        "base_url": "https://api.telegram.org",
        "polling": True,
        "parse_mode": "Markdown",
    },
    "discord": {
        "gateway": "wss://gateway.discord.gg",
        "intents": 3276799,
    },
    "qq": {
        "sandbox": False,
    },
    "email": {
        "imap_port": 993,
        "smtp_port": 465,
        "ssl": True,
    },
    "slack": {
        "base_url": "https://slack.com/api",
    },
    "wechat": {
        "base_url": "https://api.weixin.qq.com",
    },
    "dingtalk": {
        "base_url": "https://oapi.dingtalk.com",
    },
    "feishu": {
        "base_url": "https://open.feishu.cn/open-apis",
    },
}


# 思考模型映射——每个provider的思考模型名
# 用户配 thinking=True 时自动切换到思考模型
THINKING_MODELS = {
    # 国内
    "deepseek": {
        "default": "deepseek-chat",
        "thinking": "deepseek-reasoner",  # DeepSeek-R1
        "thinking_field": "reasoning_content",  # R1用这个字段
    },
    "dashscope": {
        "default": "qwen-plus",
        "thinking": "qwen3-235b-a22b",  # QwQ
        "thinking_field": "content",  # <think>标签在content里
        "thinking_tag": True,  # 用<think></think>标签
    },
    "zhipu": {
        "default": "glm-4-plus",
        "thinking": "glm-4-plus",  # GLM4支持思考
        "thinking_field": "content",
    },
    "moonshot": {
        "default": "moonshot-v1-8k",
        "thinking": "moonshot-v1-8k",  # Kimi思考
        "thinking_field": "content",
    },
    "doubao": {
        "default": "doubao-1.5-pro-32k",
        "thinking": "doubao-1.5-thinking-pro-32k",
        "thinking_field": "reasoning_content",
    },
    # 国外
    "openai": {
        "default": "gpt-4o",
        "thinking": "o4-mini",  # o系列思考模型
        "thinking_field": "content",  # 内部思考
    },
    "anthropic": {
        "default": "claude-sonnet-4-20250514",
        "thinking": "claude-sonnet-4-20250514",  # Claude extended thinking
        "thinking_field": "thinking",
        "thinking_param": {"thinking": {"type": "enabled", "budget_tokens": 10000}},
    },
    "google": {
        "default": "gemini-2.0-flash",
        "thinking": "gemini-2.0-flash-thinking-exp",
        "thinking_field": "content",
    },
    "xai": {
        "default": "grok-3",
        "thinking": "grok-3-mini",  # 带think
        "thinking_field": "reasoning_content",
    },
    # 本地
    "ollama": {
        "default": "auto",
        "thinking": "auto",  # Ollama自动返回thinking字段
        "thinking_field": "thinking",
    },
    "llamacpp": {
        "default": "auto",
        "thinking": "auto",
        "thinking_field": "content",
    },
}


# 国内模型优先级列表（国内网络优先，低延迟免翻墙）
# 按综合能力+价格排序
DOMESTIC_PRIORITY = [
    "dashscope",    # 通义千问 — 通用最强，价格适中
    "deepseek",     # DeepSeek — 代码/思考强，性价比最高
    "zhipu",        # 智谱GLM — 均衡，有免费额度
    "moonshot",     # 月之暗面 — 长上下文（128k）
    "doubao",       # 豆包 — 字节跳动，速度快
    "hunyuan",      # 混元 — 腾讯，稳定
    "sensenova",    # 商汤 — 中文理解好
    "baichuan",     # 百川 — 中文优化
    "yi",           # 零一万物 — yi-lightning性价比极高
    "minimax",      # MiniMax — 长文本
    "spark",        # 星火 — 中文对话
    "stepfun",      # 阶跃星辰 — step-1（1T参数）
    "qianfan",      # 文心一言 — ERNIE系列
]

# 国外模型降级链
FOREIGN_FALLBACK = [
    "openai",
    "anthropic",
    "google",
    "mistral",
    "xai",
]

# 聚合平台
AGGREGATOR_PLATFORMS = [
    "openrouter",
    "together",
    "groq",
    "siliconflow",
]

# 免费/有免费额度的模型
FREE_MODELS = {
    "glm-4-flash", "glm-4-flashx",  # 智谱免费
    "ernie-speed", "ernie-lite",     # 文心免费
    "hunyuan-lite",                  # 混元免费
}


def resolve_llm_config(name: str, value) -> Dict:
    """解析LLM配置 — 一个key自动展开成完整配置
    
    Args:
        name: provider名 ("deepseek", "openai", ...)
        value: 配置值（可以是key字符串、地址、或详细dict）
    
    Returns:
        完整的LLM配置dict
    """
    defaults = PROVIDER_DEFAULTS.get(name, {})
    
    if isinstance(value, str):
        if value.startswith("sk-") or value.startswith("key-") or len(value) > 20:
            # 纯key
            return {**defaults, "api_key": value}
        elif "localhost" in value or _is_ip(value):
            # 本地地址
            base = f"http://{value}" if "://" not in value else value
            # 如果是Ollama且没有端口，加默认端口11434
            if name == "ollama" and ":" not in value.replace("localhost", ""):
                base = f"http://{value}:11434"
            elif name == "llamacpp" and ":" not in value.replace("localhost", ""):
                base = f"http://{value}:8080"
            elif name == "vllm" and ":" not in value.replace("localhost", ""):
                base = f"http://{value}:8000"
            return {**defaults, "base_url": base}
        else:
            # 可能是key
            return {**defaults, "api_key": value}
    
    elif isinstance(value, dict):
        # 详细配置，合并默认值
        result = {**defaults}
        # key字段统一为api_key
        if "key" in value:
            value["api_key"] = value.pop("key")
        result.update(value)
        return result
    
    return defaults


def resolve_channel_config(name: str, value) -> Dict:
    """解析通信渠道配置"""
    defaults = CHANNEL_DEFAULTS.get(name, {})
    
    if isinstance(value, str):
        # 简写格式
        if ":" in value and "@" in value:
            # email格式: user:pass@host
            parts = value.split("@")
            user_pass = parts[0]
            host = parts[1]
            user, password = user_pass.split(":", 1)
            return {**defaults, "username": user, "password": password, 
                    "imap_host": host, "smtp_host": host}
        elif ":" in value:
            # app_id:secret格式
            app_id, secret = value.split(":", 1)
            return {**defaults, "app_id": app_id, "app_secret": secret}
        else:
            # 纯token
            return {**defaults, "token": value}
    
    elif isinstance(value, dict):
        return {**defaults, **value}
    
    return defaults


def _is_ip(s: str) -> bool:
    """简单判断是否是IP地址"""
    parts = s.split(".")
    if len(parts) == 4:
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return False
