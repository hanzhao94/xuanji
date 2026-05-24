"""
国内智能路由

功能：
- 优先国内模型（低延迟、免翻墙）
- 自动降级：国内模型挂了 → 国外模型
- 按场景选模型：对话/代码/思考/嵌入
- 健康检查：定期ping国内模型

国内优先列表：
1. 通义千问（通用强）
2. DeepSeek（代码/思考强）
3. 智谱GLM（均衡）
4. 月之暗面（长上下文）
"""

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from ._base import BaseLLMAdapter, LLMError, ResponseCache

# 国内模型优先级
DOMESTIC_PRIORITY = [
    "dashscope",    # 通义千问 — 通用最强
    "deepseek",     # DeepSeek — 代码/思考
    "zhipu",        # 智谱GLM — 均衡
    "moonshot",     # 月之暗面 — 长上下文
    "doubao",       # 豆包 — 字节跳动
    "hunyuan",      # 混元 — 腾讯
    "sensenova",    # 商汤
    "baichuan",     # 百川
    "yi",           # 零一万物
    "minimax",      # MiniMax
    "spark",        # 星火
    "stepfun",      # 阶跃星辰
    "qianfan",      # 文心一言
]

# 国外模型降级链
FOREIGN_FALLBACK = [
    "openai",
    "anthropic",
    "google",
]


class DomesticRouter:
    """国内智能路由器
    
    优先国内模型，自动降级到国外模型。
    支持按场景选择最优模型。
    """
    
    def __init__(self):
        self._adapters: Dict[str, BaseLLMAdapter] = {}
        self._cache = ResponseCache(max_size=256, ttl=600)
        self._enable_cache = True
        
        # 场景模型映射
        self._chat_model: Optional[str] = None       # 对话
        self._code_model: Optional[str] = None       # 代码
        self._thinking_model: Optional[str] = None   # 思考
        self._embed_model: Optional[str] = None      # 嵌入
        self._long_context_model: Optional[str] = None  # 长文本
        
        self._initialized = False
    
    def register(self, name: str, adapter: BaseLLMAdapter) -> None:
        """注册适配器"""
        self._adapters[name] = adapter
    
    def set_model(self, scene: str, name: str) -> None:
        """设置场景模型
        
        Args:
            scene: "chat" / "code" / "thinking" / "embed" / "long_context"
            name: 适配器名
        """
        if name not in self._adapters:
            raise ValueError(f"Adapter '{name}' not registered")
        
        mapping = {
            "chat": "_chat_model",
            "code": "_code_model",
            "thinking": "_thinking_model",
            "embed": "_embed_model",
            "long_context": "_long_context_model",
        }
        if scene in mapping:
            setattr(self, mapping[scene], name)
    
    async def initialize(self) -> Dict[str, bool]:
        """启动时探测所有适配器"""
        results = {}
        tasks = {
            name: asyncio.create_task(adapter.ping())
            for name, adapter in self._adapters.items()
        }
        
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception:
                results[name] = False
        
        # 自动选择最优模型
        if not self._chat_model:
            self._auto_select("chat", results)
        if not self._code_model:
            self._auto_select("code", results)
        if not self._thinking_model:
            self._auto_select("thinking", results)
        if not self._embed_model:
            self._auto_select("embed", results)
        
        self._initialized = True
        return results
    
    def _auto_select(self, scene: str, results: Dict[str, bool]) -> None:
        """自动选择场景最优模型"""
        # 按优先级找第一个可用的
        for name in DOMESTIC_PRIORITY:
            if name in self._adapters and results.get(name, False):
                self.set_model(scene, name)
                return
        
        # 国内没有可用的，找国外的
        for name in FOREIGN_FALLBACK:
            if name in self._adapters and results.get(name, False):
                self.set_model(scene, name)
                return
    
    def _get_candidates(self, scene: str) -> List[str]:
        """获取场景候选模型列表"""
        scene_model = {
            "chat": self._chat_model,
            "code": self._code_model,
            "thinking": self._thinking_model,
            "embed": self._embed_model,
            "long_context": self._long_context_model,
        }.get(scene)
        
        candidates = []
        if scene_model:
            candidates.append(scene_model)
        
        # 国内优先
        for name in DOMESTIC_PRIORITY:
            if name not in candidates and name in self._adapters:
                candidates.append(name)
        
        # 国外降级
        for name in FOREIGN_FALLBACK:
            if name not in candidates and name in self._adapters:
                candidates.append(name)
        
        return candidates
    
    async def chat(self, messages: List[Dict], scene: str = "chat", **kwargs) -> str:
        """智能对话 — 按场景路由+降级"""
        no_cache = kwargs.pop("no_cache", False)
        
        if self._enable_cache and not no_cache:
            cached = self._cache.get(messages, scene=scene, **kwargs)
            if cached is not None:
                return cached
        
        candidates = self._get_candidates(scene)
        if not candidates:
            raise LLMError("No LLM adapters available")
        
        errors = []
        for name in candidates:
            adapter = self._adapters[name]
            if not adapter.available:
                continue
            try:
                result = await adapter.chat(messages, **kwargs)
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result, scene=scene, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        # 尝试不可用的（可能恢复了）
        for name in candidates:
            adapter = self._adapters[name]
            if adapter.available:
                continue
            try:
                result = await adapter.chat(messages, **kwargs)
                adapter.available = True
                if self._enable_cache and not no_cache:
                    self._cache.put(messages, result, scene=scene, **kwargs)
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        raise LLMError(f"All adapters failed.\n" + "\n".join(f"  {e}" for e in errors))
    
    async def stream(self, messages: List[Dict], scene: str = "chat", **kwargs) -> AsyncIterator[str]:
        """流式对话 — 按场景路由+降级"""
        candidates = self._get_candidates(scene)
        if not candidates:
            raise LLMError("No LLM adapters available")
        
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
        
        raise LLMError(f"All adapters failed for streaming.\n" + "\n".join(f"  {e}" for e in errors))
    
    async def embed(self, text: str, **kwargs) -> List[float]:
        """文本向量化"""
        candidates = []
        if self._embed_model:
            candidates.append(self._embed_model)
        for name in DOMESTIC_PRIORITY:
            if name not in candidates and name in self._adapters:
                candidates.append(name)
        
        for name in candidates:
            adapter = self._adapters.get(name)
            if adapter and adapter.available:
                try:
                    return await adapter.embed(text, **kwargs)
                except (NotImplementedError, Exception):
                    continue
        
        raise LLMError("No adapter supports embedding")
    
    def status(self) -> Dict:
        """返回路由器状态"""
        return {
            "initialized": self._initialized,
            "chat_model": self._chat_model,
            "code_model": self._code_model,
            "thinking_model": self._thinking_model,
            "embed_model": self._embed_model,
            "long_context_model": self._long_context_model,
            "adapters": {
                name: adapter.stats() for name, adapter in self._adapters.items()
            },
        }
    
    def __repr__(self) -> str:
        total = len(self._adapters)
        avail = sum(1 for a in self._adapters.values() if a.available)
        return f"<DomesticRouter {avail}/{total} available>"
