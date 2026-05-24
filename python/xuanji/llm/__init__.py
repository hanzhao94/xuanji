"""
xuanji LLM适配器层

统一接口管理多个LLM后端，支持：
- 国内大模型：通义千问/DeepSeek/智谱/月之暗面/文心/豆包/混元/百川/零一/星火/阶跃/商汤/MiniMax
- 国外大模型：OpenAI/Anthropic/Google/Mistral/Cohere/xAI等
- 聚合平台：OpenRouter/Together/Groq/硅基流动
- Ollama本地模型
- 智能路由 + 降级链（国内优先）
- 自动重试 + 429限频处理
- 响应缓存 + Token预算

用法：
    from xuanji.llm import LLMRouter, create_llm_from_config
    
    router = create_llm_from_config(llm_config)
    await router.initialize()
    reply = await router.chat([{"role": "user", "content": "你好"}])
"""

from ._base import (
    AuthError,
    BaseLLMAdapter,
    ChatResponse,
    LLMError,
    ModelNotFoundError,
    RateLimitError,
    ResponseCache,
    TimeoutError,
)
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .router import LLMRouter, create_llm_from_config

# 国内专属适配器
from .dashscope_adapter import DashscopeAdapter
from .zhipu_adapter import ZhipuAdapter
from .qianfan_adapter import QianfanAdapter
from .moonshot_adapter import MoonshotAdapter
from .deepseek_adapter import DeepseekAdapter
from .minimax_adapter import MinimaxAdapter
from .spark_adapter import SparkAdapter
from .baichuan_adapter import BaichuanAdapter
from .yi_adapter import YiAdapter
from .stepfun_adapter import StepfunAdapter
from .doubao_adapter import DoubaoAdapter
from .hunyuan_adapter import HunyuanAdapter
from .sensenova_adapter import SensenovaAdapter

# 聚合平台适配器
from .openrouter_adapter import OpenRouterAdapter
from .together_adapter import TogetherAdapter
from .groq_adapter import GroqAdapter
from .siliconflow_adapter import SiliconflowAdapter

# 国内优化
from .domestic_router import DomesticRouter, DOMESTIC_PRIORITY, FOREIGN_FALLBACK
from .domestic_budget import DomesticBudget, DOMESTIC_PRICING, estimate_cost
from .domestic_cache import DomesticCache
from .domestic_aggregator import DomesticAggregator

__all__ = [
    # 路由器
    "LLMRouter",
    "create_llm_from_config",
    "DomesticRouter",
    "DomesticAggregator",
    # 基础
    "BaseLLMAdapter",
    "OpenAICompatAdapter",
    "OllamaAdapter",
    # 国内适配器
    "DashscopeAdapter",
    "ZhipuAdapter",
    "QianfanAdapter",
    "MoonshotAdapter",
    "DeepseekAdapter",
    "MinimaxAdapter",
    "SparkAdapter",
    "BaichuanAdapter",
    "YiAdapter",
    "StepfunAdapter",
    "DoubaoAdapter",
    "HunyuanAdapter",
    "SensenovaAdapter",
    # 聚合平台适配器
    "OpenRouterAdapter",
    "TogetherAdapter",
    "GroqAdapter",
    "SiliconflowAdapter",
    # 异常
    "LLMError",
    "RateLimitError",
    "ModelNotFoundError",
    "TimeoutError",
    "AuthError",
    # 缓存
    "ResponseCache",
    "DomesticCache",
    # 预算
    "DomesticBudget",
    "DOMESTIC_PRICING",
    "estimate_cost",
    # 路由配置
    "DOMESTIC_PRIORITY",
    "FOREIGN_FALLBACK",
]
