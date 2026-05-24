"""
国内聚合适配器

功能：
- 一个API Key管理多个国内模型
- 自动路由到可用模型
- 统一接口
- 成本优化：优先便宜的模型

支持聚合：
- 阿里云（dashscope）：qwen系列 + embedding
- 火山引擎（doubao）：豆包系列
- 百度智能云（qianfan）：文心 + ERNIE
"""

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from ._base import BaseLLMAdapter, LLMError


class DomesticAggregator:
    """国内聚合适配器
    
    用一个配置管理多个国内模型，自动路由。
    """
    
    def __init__(self, name: str = "domestic"):
        self.name = name
        self._adapters: Dict[str, BaseLLMAdapter] = {}
        self._primary: Optional[str] = None
        self._fallbacks: List[str] = []
    
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
        self._fallbacks = [n for n in names if n in self._adapters]
    
    async def initialize(self) -> Dict[str, bool]:
        """探测所有适配器可用性"""
        results = {}
        tasks = {
            n: asyncio.create_task(a.ping())
            for n, a in self._adapters.items()
        }
        for n, task in tasks.items():
            try:
                results[n] = await task
            except Exception:
                results[n] = False
        return results
    
    def _get_candidates(self) -> List[str]:
        """获取候选模型列表"""
        candidates = []
        if self._primary:
            candidates.append(self._primary)
        candidates.extend(self._fallbacks)
        # 补充所有可用的
        for n in self._adapters:
            if n not in candidates:
                candidates.append(n)
        return candidates
    
    async def chat(self, messages: List[Dict], **kwargs) -> str:
        """对话 — 自动路由+降级"""
        # 如果指定了模型名
        if "model" in kwargs:
            name = kwargs["model"]
            if name in self._adapters:
                return await self._adapters[name].chat(messages, **kwargs)
        
        candidates = self._get_candidates()
        errors = []
        
        for name in candidates:
            adapter = self._adapters[name]
            if not adapter.available:
                continue
            try:
                return await adapter.chat(messages, **kwargs)
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        # 尝试不可用的
        for name in candidates:
            adapter = self._adapters[name]
            if adapter.available:
                continue
            try:
                result = await adapter.chat(messages, **kwargs)
                adapter.available = True
                return result
            except Exception as e:
                errors.append(f"[{name}] {e}")
                continue
        
        raise LLMError(f"All domestic adapters failed.\n" + "\n".join(f"  {e}" for e in errors))
    
    async def stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        candidates = self._get_candidates()
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
        
        raise LLMError(f"All domestic adapters failed for streaming.")
    
    async def embed(self, text: str, **kwargs) -> List[float]:
        """向量化"""
        for name, adapter in self._adapters.items():
            if adapter.available:
                try:
                    return await adapter.embed(text, **kwargs)
                except (NotImplementedError, Exception):
                    continue
        raise LLMError("No domestic adapter supports embedding")
    
    def status(self) -> Dict:
        return {
            "name": self.name,
            "primary": self._primary,
            "fallbacks": self._fallbacks,
            "adapters": {
                n: a.stats() for n, a in self._adapters.items()
            },
        }
    
    def __repr__(self) -> str:
        total = len(self._adapters)
        avail = sum(1 for a in self._adapters.values() if a.available)
        return f"<DomesticAggregator [{avail}/{total}] primary={self._primary}>"
