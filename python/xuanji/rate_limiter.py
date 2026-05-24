"""
xuanji 精细速率控制模块

令牌桶算法，每个provider独立限制。
支持RPM(请求/分钟) + TPM(token/分钟)。
预设常见provider限制。
零外部依赖。
"""

import time
import threading
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict


# ============================================================
# 预设Provider限制
# ============================================================

# {provider: {"rpm": 请求/分钟, "tpm": token/分钟}}
PRESET_LIMITS: Dict[str, Dict[str, int]] = {
    # OpenAI
    "openai": {"rpm": 60, "tpm": 60000},
    "openai-gpt4": {"rpm": 40, "tpm": 40000},
    "openai-gpt4o": {"rpm": 60, "tpm": 60000},
    "openai-gpt35": {"rpm": 60, "tpm": 60000},

    # DeepSeek
    "deepseek": {"rpm": 300, "tpm": 300000},
    "deepseek-chat": {"rpm": 300, "tpm": 300000},
    "deepseek-coder": {"rpm": 300, "tpm": 300000},

    # Anthropic
    "anthropic": {"rpm": 50, "tpm": 40000},
    "claude": {"rpm": 50, "tpm": 40000},
    "claude-sonnet": {"rpm": 50, "tpm": 40000},
    "claude-opus": {"rpm": 40, "tpm": 40000},

    # Google
    "gemini": {"rpm": 60, "tpm": 120000},
    "gemini-pro": {"rpm": 60, "tpm": 120000},
    "gemini-flash": {"rpm": 120, "tpm": 120000},

    # Qwen
    "qwen": {"rpm": 120, "tpm": 300000},
    "qwen-plus": {"rpm": 120, "tpm": 300000},
    "qwen-turbo": {"rpm": 300, "tpm": 300000},
    "qwen-max": {"rpm": 60, "tpm": 120000},

    # 本地模型
    "ollama": {"rpm": 999, "tpm": 999999},
    "local": {"rpm": 999, "tpm": 999999},

    # 通用默认
    "default": {"rpm": 60, "tpm": 60000},
}


# ============================================================
# 令牌桶
# ============================================================

@dataclass
class BucketState:
    """桶状态"""
    key: str
    tokens: float
    capacity: float
    refill_rate: float  # 每秒补充的令牌数
    last_refill: float
    total_acquired: int = 0
    total_rejected: int = 0
    total_waited_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "tokens": round(self.tokens, 2),
            "capacity": self.capacity,
            "refill_rate": round(self.refill_rate, 2),
            "utilization": round(1 - self.tokens / self.capacity, 2) if self.capacity > 0 else 0,
            "total_acquired": self.total_acquired,
            "total_rejected": self.total_rejected,
            "total_waited_ms": round(self.total_waited_ms, 1),
        }


class TokenBucket:
    """令牌桶 — 线程安全"""

    def __init__(self, key: str, capacity: float, refill_rate: float):
        """
        Args:
            key: 桶标识
            capacity: 桶容量
            refill_rate: 每秒补充令牌数
        """
        self.key = key
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._total_acquired = 0
        self._total_rejected = 0
        self._total_waited_ms = 0.0

    def _refill(self):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            added = elapsed * self.refill_rate
            self._tokens = min(self.capacity, self._tokens + added)
            self._last_refill = now

    def acquire(self, tokens: float = 1.0) -> bool:
        """尝试获取令牌（非阻塞）

        Args:
            tokens: 需要的令牌数

        Returns:
            True=成功获取
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._total_acquired += 1
                return True
            else:
                self._total_rejected += 1
                return False

    def wait_for(self, tokens: float = 1.0, max_wait: float = 60.0) -> bool:
        """阻塞等待直到有令牌

        Args:
            tokens: 需要的令牌数
            max_wait: 最大等待秒数

        Returns:
            True=成功获取, False=超时
        """
        start = time.monotonic()
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    waited = (time.monotonic() - start) * 1000
                    self._total_acquired += 1
                    self._total_waited_ms += waited
                    return True

            # 计算需要等待多久
            with self._lock:
                deficit = tokens - self._tokens
                wait_time = deficit / self.refill_rate if self.refill_rate > 0 else max_wait

            elapsed = time.monotonic() - start
            if elapsed >= max_wait:
                with self._lock:
                    self._total_rejected += 1
                return False

            # 等待（最多等到超时）
            sleep_time = min(wait_time, max_wait - elapsed, 0.1)
            time.sleep(max(sleep_time, 0.01))

    def peek(self) -> float:
        """查看当前可用令牌数（不消耗）"""
        with self._lock:
            self._refill()
            return self._tokens

    def reset(self):
        """重置桶到满"""
        with self._lock:
            self._tokens = self.capacity
            self._last_refill = time.monotonic()

    @property
    def state(self) -> BucketState:
        """获取桶状态"""
        with self._lock:
            self._refill()
            return BucketState(
                key=self.key,
                tokens=self._tokens,
                capacity=self.capacity,
                refill_rate=self.refill_rate,
                last_refill=self._last_refill,
                total_acquired=self._total_acquired,
                total_rejected=self._total_rejected,
                total_waited_ms=self._total_waited_ms,
            )


# ============================================================
# 速率限制器
# ============================================================

class RateLimiter:
    """速率限制器 — 每个provider独立的RPM/TPM控制

    用法:
        limiter = RateLimiter()

        # 请求前检查
        if limiter.acquire("openai"):
            # 发送请求
            pass

        # 带token计数
        if limiter.acquire("openai", tokens=500):  # 500 tokens
            pass

        # 阻塞等待
        limiter.wait_for("deepseek", tokens=1)

        # 查看状态
        print(limiter.stats("openai"))
    """

    def __init__(self, custom_limits: Optional[Dict[str, Dict[str, int]]] = None):
        """
        Args:
            custom_limits: 自定义限制，覆盖预设
                格式: {"provider": {"rpm": 60, "tpm": 60000}}
        """
        self._limits: Dict[str, Dict[str, int]] = dict(PRESET_LIMITS)
        if custom_limits:
            self._limits.update(custom_limits)

        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def acquire(self, key: str, tokens: int = 1) -> bool:
        """获取令牌（非阻塞）

        同时检查RPM和TPM两个桶。

        Args:
            key: provider名称
            tokens: 消耗的token数（1=仅计请求数）

        Returns:
            True=可以发送请求
        """
        rpm_bucket = self._get_bucket(key, "rpm")
        tpm_bucket = self._get_bucket(key, "tpm")

        # 两个桶都要通过
        rpm_ok = rpm_bucket.acquire(1)
        if not rpm_ok:
            return False

        if tokens > 1:
            tpm_ok = tpm_bucket.acquire(tokens)
            if not tpm_ok:
                # RPM已扣，但TPM不够 → 回滚RPM？
                # 简化处理：不回滚，下次RPM会自然恢复
                return False

        return True

    def wait_for(
        self,
        key: str,
        tokens: int = 1,
        max_wait: float = 60.0,
    ) -> bool:
        """阻塞等待直到有令牌

        Args:
            key: provider名称
            tokens: token数
            max_wait: 最大等待秒数

        Returns:
            True=成功
        """
        rpm_bucket = self._get_bucket(key, "rpm")
        tpm_bucket = self._get_bucket(key, "tpm")

        # 先等RPM
        if not rpm_bucket.wait_for(1, max_wait=max_wait):
            return False

        # 再等TPM
        if tokens > 1:
            if not tpm_bucket.wait_for(tokens, max_wait=max_wait):
                return False

        return True

    def stats(self, key: str) -> Dict[str, Any]:
        """获取provider的桶状态

        Args:
            key: provider名称

        Returns:
            {rpm: BucketState, tpm: BucketState, limits: ...}
        """
        rpm_bucket = self._get_bucket(key, "rpm")
        tpm_bucket = self._get_bucket(key, "tpm")
        limits = self._get_limits(key)

        return {
            "key": key,
            "limits": limits,
            "rpm": rpm_bucket.state.to_dict(),
            "tpm": tpm_bucket.state.to_dict(),
        }

    # ----------------------------------------------------------
    # 管理API
    # ----------------------------------------------------------

    def set_limits(self, key: str, rpm: int, tpm: int):
        """设置/更新provider限制

        Args:
            key: provider名称
            rpm: 请求/分钟
            tpm: token/分钟
        """
        self._limits[key] = {"rpm": rpm, "tpm": tpm}

        # 重建桶
        rpm_key = f"{key}:rpm"
        tpm_key = f"{key}:tpm"
        with self._lock:
            self._buckets.pop(rpm_key, None)
            self._buckets.pop(tpm_key, None)

    def reset(self, key: str):
        """重置provider的桶"""
        rpm_key = f"{key}:rpm"
        tpm_key = f"{key}:tpm"
        with self._lock:
            if rpm_key in self._buckets:
                self._buckets[rpm_key].reset()
            if tpm_key in self._buckets:
                self._buckets[tpm_key].reset()

    def reset_all(self):
        """重置所有桶"""
        with self._lock:
            for bucket in self._buckets.values():
                bucket.reset()

    def list_providers(self) -> List[str]:
        """列出所有配置的provider"""
        return sorted(self._limits.keys())

    def all_stats(self) -> Dict[str, Dict]:
        """所有活跃provider的统计"""
        result = {}
        seen = set()
        with self._lock:
            for bkey in self._buckets:
                provider = bkey.rsplit(":", 1)[0]
                if provider not in seen:
                    seen.add(provider)
        for provider in seen:
            result[provider] = self.stats(provider)
        return result

    def available_tokens(self, key: str) -> Dict[str, float]:
        """查看可用令牌数"""
        rpm_bucket = self._get_bucket(key, "rpm")
        tpm_bucket = self._get_bucket(key, "tpm")
        return {
            "rpm": rpm_bucket.peek(),
            "tpm": tpm_bucket.peek(),
        }

    # ----------------------------------------------------------
    # 装饰器
    # ----------------------------------------------------------

    def limit(self, key: str, tokens: int = 1, max_wait: float = 60.0):
        """装饰器：自动限速

        用法:
            @limiter.limit("openai")
            def call_api():
                ...
        """
        def decorator(func):
            def wrapper(*args, **kwargs):
                if not self.wait_for(key, tokens=tokens, max_wait=max_wait):
                    raise RuntimeError(
                        f"速率限制: {key} 等待超时 ({max_wait}s)"
                    )
                return func(*args, **kwargs)
            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper
        return decorator

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _get_limits(self, key: str) -> Dict[str, int]:
        """获取provider限制"""
        if key in self._limits:
            return self._limits[key]
        # 尝试前缀匹配
        for preset_key in self._limits:
            if key.startswith(preset_key):
                return self._limits[preset_key]
        return self._limits.get("default", {"rpm": 60, "tpm": 60000})

    def _get_bucket(self, key: str, bucket_type: str) -> TokenBucket:
        """获取/创建令牌桶"""
        bucket_key = f"{key}:{bucket_type}"

        if bucket_key not in self._buckets:
            with self._lock:
                if bucket_key not in self._buckets:
                    limits = self._get_limits(key)
                    if bucket_type == "rpm":
                        capacity = limits["rpm"]
                        refill_rate = limits["rpm"] / 60.0  # 每秒
                    else:  # tpm
                        capacity = limits["tpm"]
                        refill_rate = limits["tpm"] / 60.0
                    self._buckets[bucket_key] = TokenBucket(
                        key=bucket_key,
                        capacity=capacity,
                        refill_rate=refill_rate,
                    )

        return self._buckets[bucket_key]


# ============================================================
# 便捷函数
# ============================================================

_default_limiter: Optional[RateLimiter] = None


def get_limiter(**kwargs) -> RateLimiter:
    """获取/创建默认限速器"""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter(**kwargs)
    return _default_limiter


def acquire(key: str, tokens: int = 1) -> bool:
    """快速获取令牌"""
    return get_limiter().acquire(key, tokens)


def wait_for(key: str, tokens: int = 1, max_wait: float = 60.0) -> bool:
    """快速等待令牌"""
    return get_limiter().wait_for(key, tokens, max_wait)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    limiter = RateLimiter()

    print("=== 预设Provider限制 ===")
    for p in ["openai", "deepseek", "claude", "gemini", "qwen"]:
        limits = limiter._get_limits(p)
        print(f"  {p}: RPM={limits['rpm']}, TPM={limits['tpm']}")

    print("\n=== 基本获取 ===")
    for i in range(5):
        ok = limiter.acquire("openai")
        print(f"  attempt {i+1}: {'OK' if ok else 'REJECTED'}")

    print("\n=== 带token获取 ===")
    ok = limiter.acquire("openai", tokens=1000)
    print(f"  1000 tokens: {'OK' if ok else 'REJECTED'}")

    print("\n=== 自定义限制 ===")
    limiter.set_limits("my_api", rpm=5, tpm=1000)
    results = []
    for i in range(8):
        ok = limiter.acquire("my_api")
        results.append("OK" if ok else "X")
    print(f"  8 attempts (limit 5): {' '.join(results)}")

    print("\n=== 等待测试 ===")
    limiter.set_limits("slow_api", rpm=2, tpm=10000)
    limiter.acquire("slow_api")
    limiter.acquire("slow_api")
    start = time.monotonic()
    ok = limiter.wait_for("slow_api", max_wait=3)
    elapsed = (time.monotonic() - start) * 1000
    print(f"  waited {elapsed:.0f}ms: {'OK' if ok else 'TIMEOUT'}")

    print("\n=== 装饰器 ===")
    @limiter.limit("openai")
    def my_api_call():
        return "success"

    result = my_api_call()
    print(f"  decorated call: {result}")

    print("\n=== 状态 ===")
    stats = limiter.stats("openai")
    import json
    print(f"  openai: {json.dumps(stats, indent=2)}")
    print(f"\n=== 所有活跃统计 ===")
    for k, v in limiter.all_stats().items():
        print(f"  {k}: rpm_avail={v['rpm']['tokens']:.0f}, tpm_avail={v['tpm']['tokens']:.0f}")
