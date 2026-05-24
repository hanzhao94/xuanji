"""
LLM 智能路由 + 降级链

功能：
- 管理多个LLM后端
- 从config.toml的[llm]配置自动创建适配器
- 降级链：primary挂了 → fallback[0] → fallback[1] → 报错（进程不退出！）
- 智能路由：简单任务→小模型，复杂任务→大模型（按token数判断）
- 响应缓存：相似prompt不重复调用
- 启动时探测：逐个ping模型，标记可用/不可用
"""

import asyncio
import hashlib
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from ._base import BaseLLMAdapter, ChatResponse, LLMError, ResponseCache


# 按token数判断任务复杂度的阈值
SIMPLE_TASK_THRESHOLD = 500    # ≤500 tokens视为简单任务
COMPLEX_TASK_THRESHOLD = 2000  # ≥2000 tokens视为复杂任务


def _estimate_tokens(messages: List[Dict]) -> int:
    """粗略估算消息的token数
    
    规则：英文 ~4字符/token，中文 ~2字符/token
    这里用简单的字符数/3作为估算
    """
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return max(total_chars // 3, 1)


class LLMRouter:
    """LLM智能路由器
    
    管理多个LLM后端，提供统一的chat/stream接口。
    自动处理降级、路由、缓存。
    """
    
    def __init__(self):
        # 适配器注册表
        self._adapters: Dict[str, BaseLLMAdapter] = {}
        
        # 角色分配
        self._primary: Optional[str] = None       # 主力模型
        self._fallbacks: List[str] = []            # 降级链
        self._simple_model: Optional[str] = None   # 简单任务模型（小模型）
        self._complex_model: Optional[str] = None  # 复杂任务模型（大模型）
        self._embed_model: Optional[str] = None    # 向量化模型
        
        # 全局缓存（跨适配器）
        self._cache = ResponseCache(max_size=256, ttl=600)
        self._enable_cache = True
        
        # 状态
        self._initialized = False
    
    def register(self, name: str, adapter: BaseLLMAdapter) -> None:
        """注册适配器"""
        self._adapters[name] = adapter
    
    def set_primary(self, name: str) -> None:
        """设置主力模型"""
        if name not in self._adapters:
            raise ValueError(f"Adapter '{name}' not registered")
        self._primary = name
    
    def set_fallbacks(self, names: List[str]) -> None:
        """设置降级链"""
        for n in names:
            if n not in self._adapters:
                raise ValueError(f"Adapter '{n}' not registered")
        self._fallbacks = names
    
    def set_simple_model(self, name: str) -> None:
        """设置简单任务模型"""
        self._simple_model = name
    
    def set_complex_model(self, name: str) -> None:
        """设置复杂任务模型"""
        self._complex_model = name
    
    def set_embed_model(self, name: str) -> None:
        """设置向量化模型"""
        self._embed_model = name
    
    # === 初始化 ===
    
    def _auto_register_from_env(self) -> None:
        """从环境变量自动注册适配器
        
        扫描常见环境变量，自动创建并注册适配器：
        - DASHSCOPE_API_KEY → dashscope
        - DEEPSEEK_API_KEY → deepseek
        - ZHIPU_API_KEY → zhipu
        - OPENAI_API_KEY → openai_compat
        - OPENROUTER_API_KEY → openrouter
        """
        import os
        
        env_to_adapter = {
            "DASHSCOPE_API_KEY": ("dashscope", ".dashscope_adapter", "DashscopeAdapter"),
            "DEEPSEEK_API_KEY": ("deepseek", ".deepseek_adapter", "DeepseekAdapter"),
            "ZHIPU_API_KEY": ("zhipu", ".zhipu_adapter", "ZhipuAdapter"),
            "OPENAI_API_KEY": ("openai", ".openai_compat", "OpenAICompatAdapter"),
            "OPENROUTER_API_KEY": ("openrouter", ".openrouter_adapter", "OpenRouterAdapter"),
            "SILICONFLOW_API_KEY": ("siliconflow", ".siliconflow_adapter", "SiliconflowAdapter"),
        }
        
        for env_var, (name, module_path, class_name) in env_to_adapter.items():
            key = os.environ.get(env_var)
            if not key:
                continue
            if name in self._adapters:
                continue  # 已注册
            
            try:
                # 动态导入适配器类
                from .. import llm
                mod = importlib.import_module(module_path, package=llm.__name__)
                adapter_cls = getattr(mod, class_name)
                
                adapter = adapter_cls(name, {
                    "api_key": key,
                    "base_url": os.environ.get(f"{env_var.replace('_API_KEY', '_BASE_URL')}", ""),
                })
                self._adapters[name] = adapter
            except Exception:
                pass  # 静默失败，不影响其他适配器
    
    async def initialize(self) -> Dict[str, bool]:
        """启动时探测所有适配器
        
        功能：
        1. 自动从环境变量注册适配器（DASHSCOPE_API_KEY等）
        2. 探测所有已注册适配器可用性
        
        Returns:
            各适配器可用性 {name: bool}
        """
        # 自动从环境变量注册适配器
        self._auto_register_from_env()
        
        results = {}
        
        # 并发探测所有适配器
        tasks = {
            name: asyncio.create_task(adapter.ping())
            for name, adapter in self._adapters.items()
        }
        
        for name, task in tasks.items():
            try:
                available = await task
                results[name] = available
            except Exception as e:
                results[name] = False
                self._adapters[name].available = False
                self._adapters[name].last_error = str(e)
        
        # 如果primary不可用，尝试从fallback中找一个替代
        if self._primary and not results.get(self._primary, False):
            for fb in self._fallbacks:
                if results.get(fb, False):
                    # 不改primary配置，只在路由时自动降级
                    break
        
        self._initialized = True
        return results
    
    # === 路由逻辑 ===
    
    def _select_adapter(self, messages: List[Dict], **kwargs) -> List[str]:
        """选择适配器（返回优先级列表）
        
        策略：
        1. 如果指定了adapter名 → 直接用
        2. 智能路由：按任务复杂度选模型
        3. 降级链兜底
        """
        # 显式指定
        if "adapter" in kwargs:
            name = kwargs.pop("adapter")
            if name in self._adapters:
                return [name]
        
        # 智能路由
        token_est = _estimate_tokens(messages)
        
        candidates = []
        
        if token_est <= SIMPLE_TASK_THRESHOLD and self._simple_model:
            # 简单任务 → 小模型优先
            candidates.append(self._simple_model)
        elif token_est >= COMPLEX_TASK_THRESHOLD and self._complex_model:
            # 复杂任务 → 大模型优先
            candidates.append(self._complex_model)
        
        # 主力模型
        if self._primary:
            candidates.append(self._primary)
        
        # 降级链
        candidates.extend(self._fallbacks)
        
        # 如果没有配置任何角色，用所有已注册的
        if not candidates:
            candidates = list(self._adapters.keys())
        
        # 去重保序
        seen = set()
        result = []
        for c in candidates:
            if c not in seen and c in self._adapters:
                seen.add(c)
                result.append(c)
        
        return result
    
    # === 公开接口 ===
    
    async def chat(self, messages, **kwargs) -> ChatResponse:
        """智能对话 — 自动路由+降级
        
        Args:
            messages: 纯字符串 或 OpenAI格式消息列表
                字符串时自动转为 [{"role": "user", "content": messages}]
            **kwargs: 额外参数
                adapter: 强制指定适配器名
                no_cache: 跳过缓存
                其他参数传给适配器
        
        Returns:
            模型回复文本
        
        Raises:
            LLMError: 所有适配器都失败
        """
        # 兼容纯字符串输入
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        no_cache = kwargs.pop("no_cache", False)
        
        # 查缓存
        if self._enable_cache and not no_cache:
            cached = self._cache.get(messages, **kwargs)
            if cached is not None:
                return cached
        
        # 选适配器
        candidates = self._select_adapter(messages, **kwargs)
        
        if not candidates:
            raise LLMError("No LLM adapters available. Check your config.")
        
        # 按优先级尝试
        errors = []
        for name in candidates:
            adapter = self._adapters[name]
            if not adapter.available:
                continue
            try:
                result = await adapter.chat(messages, **kwargs)
                # 写缓存
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        # 所有可用的都失败了，尝试不可用的（可能恢复了）
        for name in candidates:
            adapter = self._adapters[name]
            if adapter.available:
                continue  # 已经试过了
            try:
                result = await adapter.chat(messages, **kwargs)
                adapter.available = True  # 恢复了！
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        # 全部失败 — 报错但不退出进程！
        raise LLMError(
            f"All LLM adapters failed.\n"
            + "\n".join(f"  {err}" for err in errors)
        )
    
    async def chat_response(self, messages, **kwargs) -> ChatResponse:
        """智能对话 — 返回完整ChatResponse（含thinking）
        
        Args:
            messages: 纯字符串 或 OpenAI格式消息列表
                字符串时自动转为 [{"role": "user", "content": messages}]
            **kwargs: 额外参数
                adapter: 强制指定适配器名
                no_cache: 跳过缓存
                thinking: 是否返回思考过程
                其他参数传给适配器
        
        Returns:
            ChatResponse（含content, thinking, usage等）
        
        Raises:
            LLMError: 所有适配器都失败
        """
        # 兼容纯字符串输入
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        
        no_cache = kwargs.pop("no_cache", False)
        
        # 查缓存
        if self._enable_cache and not no_cache:
            cached = self._cache.get(messages, **kwargs)
            if cached is not None:
                return ChatResponse(content=cached)
        
        # 选适配器
        candidates = self._select_adapter(messages, **kwargs)
        
        if not candidates:
            raise LLMError("No LLM adapters available. Check your config.")
        
        # 按优先级尝试
        errors = []
        for name in candidates:
            adapter = self._adapters[name]
            if not adapter.available:
                continue
            try:
                result = await adapter.chat_response(messages, **kwargs)
                # 写缓存（只缓存content）
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result.content, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        # 所有可用的都失败了，尝试不可用的
        for name in candidates:
            adapter = self._adapters[name]
            if adapter.available:
                continue
            try:
                result = await adapter.chat_response(messages, **kwargs)
                adapter.available = True
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result.content, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        raise LLMError(
            f"All LLM adapters failed.\n"
            + "\n".join(f"  {err}" for err in errors)
        )
    
    async def stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话 — 自动路由+降级
        
        Yields:
            逐块文本
        """
        candidates = self._select_adapter(messages, **kwargs)
        
        if not candidates:
            raise LLMError("No LLM adapters available. Check your config.")
        
        errors = []
        for name in candidates:
            adapter = self._adapters[name]
            if not adapter.available:
                continue
            try:
                async for chunk in adapter.stream(messages, **kwargs):
                    yield chunk
                return
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        raise LLMError(
            f"All LLM adapters failed for streaming.\n"
            + "\n".join(f"  {err}" for err in errors)
        )
    
    async def embed(self, text: str, **kwargs) -> List[float]:
        """文本向量化
        
        优先用embed_model，否则用primary。
        """
        # 优先用专门的embed模型
        candidates = []
        if self._embed_model:
            candidates.append(self._embed_model)
        if self._primary:
            candidates.append(self._primary)
        candidates.extend(self._fallbacks)
        
        for name in candidates:
            adapter = self._adapters.get(name)
            if adapter and adapter.available:
                try:
                    return await adapter.embed(text, **kwargs)
                except NotImplementedError:
                    continue
                except Exception:
                    continue
        
        raise LLMError("No adapter supports embedding")
    
    def capabilities(self) -> dict:
        """返回各适配器能力列表
        
        Returns:
            {
                "adapters": {
                    "name": {
                        "model": str,
                        "available": bool,
                        "thinking": bool,       # 是否支持思考模式
                        "thinking_models": [],  # 支持思考的模型列表
                        "stream": bool,
                        "embed": bool,
                    }
                }
            }
        """
        result = {}
        thinking_adapters = set()
        
        for name, adapter in self._adapters.items():
            info = {
                "model": getattr(adapter, 'model', ''),
                "available": adapter.available,
                "thinking": False,
                "thinking_models": [],
                "stream": True,  # 所有adapter都支持stream
                "embed": hasattr(adapter, 'embed'),
            }
            
            # 检查是否支持思考模式
            thinking_models = getattr(adapter, 'THINKING_MODELS', set())
            if thinking_models:
                info["thinking"] = True
                info["thinking_models"] = sorted(thinking_models)
                thinking_adapters.add(name)
            
            result[name] = info
        
        return {
            "adapters": result,
            "thinking_adapters": sorted(thinking_adapters),
            "total": len(result),
        }
    
    # === 状态 ===
    
    def status(self) -> Dict:
        """返回路由器状态"""
        adapters = {}
        for name, adapter in self._adapters.items():
            adapters[name] = adapter.stats()
        
        return {
            "initialized": self._initialized,
            "primary": self._primary,
            "fallbacks": self._fallbacks,
            "simple_model": self._simple_model,
            "complex_model": self._complex_model,
            "embed_model": self._embed_model,
            "adapters": adapters,
            "cache": self._cache.stats() if self._enable_cache else None,
        }
    
    def available_adapters(self) -> List[str]:
        """返回可用的适配器列表"""
        return [
            name for name, adapter in self._adapters.items()
            if adapter.available
        ]
    
    def __repr__(self) -> str:
        total = len(self._adapters)
        avail = len(self.available_adapters())
        return f"<LLMRouter {avail}/{total} adapters available, primary={self._primary}>"


# === 工厂函数 ===

def create_llm_from_config(llm_config: Dict[str, Any]) -> LLMRouter:
    """从config.toml的[llm]配置创建LLMRouter
    
    config.toml格式示例：
    
    [llm]
    primary = "deepseek"
    fallbacks = ["dashscope", "openai"]
    simple_model = "groq"         # 简单任务用小模型
    complex_model = "deepseek"    # 复杂任务用大模型
    embed_model = "dashscope"     # 向量化
    cache = true
    
    [llm.deepseek]
    key = "sk-xxx"
    
    [llm.dashscope]
    key = "sk-xxx"
    model = "qwen-max"
    
    [llm.ollama]
    base_url = "http://localhost:11434"
    model = "llama3"
    
    Args:
        llm_config: [llm] section的配置dict
    
    Returns:
        配置好的LLMRouter
    """
    from ..config import PROVIDER_DEFAULTS, resolve_llm_config
    from .ollama import OllamaAdapter
    from .openai_compat import OpenAICompatAdapter
    
    # 国内专属适配器映射
    DOMESTIC_ADAPTERS = {
        "dashscope": (".dashscope_adapter", "DashscopeAdapter"),
        "zhipu": (".zhipu_adapter", "ZhipuAdapter"),
        "qianfan": (".qianfan_adapter", "QianfanAdapter"),
        "moonshot": (".moonshot_adapter", "MoonshotAdapter"),
        "deepseek": (".deepseek_adapter", "DeepseekAdapter"),
        "minimax": (".minimax_adapter", "MinimaxAdapter"),
        "spark": (".spark_adapter", "SparkAdapter"),
        "baichuan": (".baichuan_adapter", "BaichuanAdapter"),
        "yi": (".yi_adapter", "YiAdapter"),
        "stepfun": (".stepfun_adapter", "StepfunAdapter"),
        "doubao": (".doubao_adapter", "DoubaoAdapter"),
        "hunyuan": (".hunyuan_adapter", "HunyuanAdapter"),
        "sensenova": (".sensenova_adapter", "SensenovaAdapter"),
    }
    
    # 聚合平台适配器映射
    AGGREGATOR_ADAPTERS = {
        "openrouter": (".openrouter_adapter", "OpenRouterAdapter"),
        "together": (".together_adapter", "TogetherAdapter"),
        "groq": (".groq_adapter", "GroqAdapter"),
        "siliconflow": (".siliconflow_adapter", "SiliconflowAdapter"),
    }
    
    router = LLMRouter()
    
    # 全局设置
    primary = llm_config.get("primary")
    fallbacks = llm_config.get("fallbacks", [])
    simple_model = llm_config.get("simple_model")
    complex_model = llm_config.get("complex_model")
    embed_model = llm_config.get("embed_model")
    enable_cache = llm_config.get("cache", True)
    
    router._enable_cache = enable_cache
    
    # 遍历配置，创建适配器
    skip_keys = {"primary", "fallbacks", "simple_model", "complex_model",
                 "embed_model", "cache", "cache_size", "cache_ttl"}
    
    for name, value in llm_config.items():
        if name in skip_keys:
            continue
        
        # 解析完整配置
        full_config = resolve_llm_config(name, value)
        
        # 适配器公共参数
        adapter_kwargs = {
            "enable_cache": False,  # 路由器层统一缓存
        }
        
        # 根据provider类型选适配器
        if name == "ollama":
            adapter = OllamaAdapter(name, full_config, **adapter_kwargs)
        elif name in ("vllm", "llamacpp"):
            adapter = OpenAICompatAdapter(name, full_config, **adapter_kwargs)
        elif name in DOMESTIC_ADAPTERS:
            # 国内专属适配器
            import importlib
            module_name, class_name = DOMESTIC_ADAPTERS[name]
            module = importlib.import_module(module_name, "xuanji.llm")
            adapter_cls = getattr(module, class_name)
            adapter = adapter_cls(name, full_config, **adapter_kwargs)
        elif name in AGGREGATOR_ADAPTERS:
            # 聚合平台适配器
            import importlib
            module_name, class_name = AGGREGATOR_ADAPTERS[name]
            module = importlib.import_module(module_name, "xuanji.llm")
            adapter_cls = getattr(module, class_name)
            adapter = adapter_cls(name, full_config, **adapter_kwargs)
        else:
            # 其他走OpenAI兼容接口
            adapter = OpenAICompatAdapter(name, full_config, **adapter_kwargs)
        
        router.register(name, adapter)
    
    # 设置角色
    if primary:
        try:
            router.set_primary(primary)
        except ValueError:
            pass  # 配置了但没有对应的key，忽略
    
    if fallbacks:
        valid_fbs = [fb for fb in fallbacks if fb in router._adapters]
        router._fallbacks = valid_fbs
    
    if simple_model and simple_model in router._adapters:
        router.set_simple_model(simple_model)
    if complex_model and complex_model in router._adapters:
        router.set_complex_model(complex_model)
    if embed_model and embed_model in router._adapters:
        router.set_embed_model(embed_model)
    
    return router
