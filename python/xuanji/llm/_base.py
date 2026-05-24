"""
xuanji LLM 基础适配器

统一接口：chat / stream / embed
内置：指数退避重试、429限频、超时处理、响应缓存
零外部依赖。
"""

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional


class ChatResponse:
    """LLM回复结果——支持思考模式
    
    Attributes:
        content: 最终回复内容
        thinking: 思考过程（如果模型支持）
        model: 使用的模型名
        usage: token用量 {prompt_tokens, completion_tokens, total_tokens}
        raw: 原始响应
    """
    __slots__ = ('content', 'thinking', 'model', 'usage', 'raw',
                 'finish_reason', 'thinking_tokens')
    
    def __init__(self, content: str = '', thinking: str = '',
                 model: str = '', usage: dict = None, raw: dict = None,
                 finish_reason: str = '', thinking_tokens: int = 0):
        self.content = content
        self.thinking = thinking
        self.model = model
        self.usage = usage or {}
        self.raw = raw or {}
        self.finish_reason = finish_reason
        self.thinking_tokens = thinking_tokens
    
    def __str__(self) -> str:
        return self.content
    
    def __len__(self) -> int:
        return len(self.content)
    
    def __bool__(self) -> bool:
        return bool(self.content or self.thinking)
    
    @property
    def has_thinking(self) -> bool:
        return bool(self.thinking)
    
    @property
    def full_text(self) -> str:
        """thinking + content"""
        if self.thinking:
            return f"<thinking>\n{self.thinking}\n</thinking>\n\n{self.content}"
        return self.content
    
    def to_dict(self) -> dict:
        d = {'content': self.content}
        if self.thinking:
            d['thinking'] = self.thinking
            d['thinking_tokens'] = self.thinking_tokens
        if self.model:
            d['model'] = self.model
        if self.usage:
            d['usage'] = self.usage
        if self.finish_reason:
            d['finish_reason'] = self.finish_reason
        return d


class LLMError(Exception):
    """LLM调用基础异常"""
    pass


class RateLimitError(LLMError):
    """429限频异常"""
    def __init__(self, message: str = "Rate limited", retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


class ModelNotFoundError(LLMError):
    """模型不存在"""
    pass


class TimeoutError(LLMError):
    """请求超时"""
    pass


class AuthError(LLMError):
    """认证失败"""
    pass


class ResponseCache:
    """简单的LRU响应缓存
    
    用于缓存完全相同的prompt请求结果，避免重复调用。
    线程安全通过asyncio保证（单线程事件循环）。
    """
    
    def __init__(self, max_size: int = 128, ttl: float = 300.0):
        """
        Args:
            max_size: 最大缓存条目数
            ttl: 缓存有效期（秒）
        """
        self._cache: Dict[str, dict] = {}  # key -> {"value": ..., "ts": ...}
        self._order: list = []  # LRU顺序
        self.max_size = max_size
        self.ttl = ttl
        self.hits = 0
        self.misses = 0
    
    def _make_key(self, messages: List[Dict], **kwargs) -> str:
        """生成缓存key"""
        raw = json.dumps({"m": messages, "k": kwargs}, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()
    
    def get(self, messages: List[Dict], **kwargs) -> Optional[str]:
        """查找缓存"""
        key = self._make_key(messages, **kwargs)
        entry = self._cache.get(key)
        if entry is None:
            self.misses += 1
            return None
        # 检查TTL
        if time.time() - entry["ts"] > self.ttl:
            self._evict(key)
            self.misses += 1
            return None
        # 更新LRU
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
        self.hits += 1
        return entry["value"]
    
    def put(self, messages: List[Dict], value: str, **kwargs) -> None:
        """写入缓存"""
        key = self._make_key(messages, **kwargs)
        # 驱逐
        while len(self._cache) >= self.max_size:
            if self._order:
                old_key = self._order.pop(0)
                self._cache.pop(old_key, None)
            else:
                break
        self._cache[key] = {"value": value, "ts": time.time()}
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
    
    def _evict(self, key: str) -> None:
        self._cache.pop(key, None)
        if key in self._order:
            self._order.remove(key)
    
    def clear(self) -> None:
        self._cache.clear()
        self._order.clear()
        self.hits = 0
        self.misses = 0
    
    def stats(self) -> Dict:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / max(self.hits + self.misses, 1),
        }


class BaseLLMAdapter(ABC):
    """LLM适配器基类
    
    所有LLM后端都继承此类，统一接口。
    内置重试、限频、超时、缓存机制。
    """
    
    def __init__(
        self,
        name: str,
        config: Dict[str, Any],
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        timeout: float = 120.0,
        enable_cache: bool = False,
        cache_size: int = 128,
        cache_ttl: float = 300.0,
    ):
        """
        Args:
            name: 适配器名（如 "deepseek", "openai"）
            config: 完整配置（已经过resolve_llm_config展开）
            max_retries: 最大重试次数
            base_delay: 重试基础延迟（秒）
            max_delay: 最大重试延迟（秒）
            timeout: 请求超时（秒）
            enable_cache: 是否启用响应缓存
            cache_size: 缓存大小
            cache_ttl: 缓存TTL（秒）
        """
        self.name = name
        self.config = config
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        
        # 可选缓存
        self._cache: Optional[ResponseCache] = None
        if enable_cache:
            self._cache = ResponseCache(max_size=cache_size, ttl=cache_ttl)
        
        # 状态
        self.available = False
        self.last_error: Optional[str] = None
        self._total_calls = 0
        self._total_errors = 0
        self._total_tokens = 0
    
    # === 子类必须实现 ===
    
    @abstractmethod
    async def _do_chat(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """实际的chat调用（子类实现）"""
        ...
    
    async def _do_chat_response(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """实际的chat调用，返回ChatResponse（子类覆盖以支持thinking）"""
        content = await self._do_chat(messages, **kwargs)
        return ChatResponse(content=content)
    
    @abstractmethod
    async def _do_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """实际的流式调用（子类实现）"""
        ...
    
    @abstractmethod
    async def ping(self) -> bool:
        """探测模型是否可用"""
        ...
    
    # === 公开接口（带重试+缓存） ===
    
    async def chat(self, messages: List[Dict], **kwargs) -> str:
        """对话接口（带重试、缓存、限频处理）
        
        Args:
            messages: OpenAI格式消息列表
            **kwargs: 额外参数（temperature, max_tokens等）
        
        Returns:
            模型回复文本
        """
        # 查缓存
        if self._cache is not None:
            cached = self._cache.get(messages, **kwargs)
            if cached is not None:
                return cached
        
        # 带重试调用
        result = await self._retry(self._do_chat, messages, **kwargs)
        
        # 写缓存
        if self._cache is not None:
            self._cache.put(messages, result, **kwargs)
        
        self._total_calls += 1
        return result
    
    async def chat_response(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """对话接口，返回完整ChatResponse（含thinking）
        
        Args:
            messages: OpenAI格式消息列表
            **kwargs: 额外参数（temperature, max_tokens, thinking等）
        
        Returns:
            ChatResponse对象（含content, thinking, usage等）
        """
        self._total_calls += 1
        return await self._retry(self._do_chat_response, messages, **kwargs)
    
    async def stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话接口（带重试，不缓存）
        
        Yields:
            逐块文本
        """
        # 流式不能用_retry（async generator不能await）
        # 直接调_do_stream，出错重试整个流
        self._total_calls += 1
        last_err = None
        for attempt in range(max(1, self.max_retries)):
            try:
                async for chunk in self._do_stream(messages, **kwargs):
                    yield chunk
                return  # 成功完成
            except Exception as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    wait = min(2 ** attempt + 0.5, 30)
                    await asyncio.sleep(wait)
        if last_err:
            raise last_err
    
    async def embed(self, text: str) -> List[float]:
        """文本向量化（可选，子类覆盖）
        
        Args:
            text: 输入文本
        
        Returns:
            向量
        """
        raise NotImplementedError(f"{self.name} does not support embedding")
    
    # === 重试引擎 ===
    
    async def _retry(self, fn, *args, **kwargs):
        """指数退避重试
        
        处理：
        - 429 Rate Limit → 等待retry_after或指数退避
        - 超时 → 重试
        - 其他临时错误 → 重试
        - 认证/模型不存在 → 直接抛出不重试
        """
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    fn(*args, **kwargs),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                last_exc = TimeoutError(
                    f"[{self.name}] Request timed out after {self.timeout}s "
                    f"(attempt {attempt + 1}/{self.max_retries + 1})"
                )
                self.last_error = str(last_exc)
            except RateLimitError as e:
                last_exc = e
                self.last_error = str(e)
                # 429：用服务端的retry_after，或指数退避
                wait = e.retry_after if e.retry_after > 0 else self._backoff(attempt)
                await asyncio.sleep(wait)
                continue
            except (AuthError, ModelNotFoundError):
                # 不可重试的错误
                self._total_errors += 1
                raise
            except LLMError as e:
                last_exc = e
                self.last_error = str(e)
            except Exception as e:
                last_exc = LLMError(f"[{self.name}] Unexpected error: {e}")
                self.last_error = str(last_exc)
            
            # 等待后重试
            if attempt < self.max_retries:
                wait = self._backoff(attempt)
                await asyncio.sleep(wait)
        
        self._total_errors += 1
        self.available = False
        raise last_exc or LLMError(f"[{self.name}] All retries exhausted")
    
    def _backoff(self, attempt: int) -> float:
        """指数退避计算"""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        # 加点抖动，避免惊群
        import random
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter
    
    # === 状态 ===
    
    def stats(self) -> Dict:
        """返回适配器统计信息"""
        result = {
            "name": self.name,
            "available": self.available,
            "total_calls": self._total_calls,
            "total_errors": self._total_errors,
            "last_error": self.last_error,
        }
        if self._cache:
            result["cache"] = self._cache.stats()
        return result
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        return f"<{self.__class__.__name__} [{status}] {self.name}>"
