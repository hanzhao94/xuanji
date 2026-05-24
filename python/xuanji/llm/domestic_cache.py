"""
国内响应缓存

针对国内网络特点优化的缓存层：
- 更长的TTL（国内网络波动大）
- 更大的缓存容量
- 按模型分组缓存
- 支持持久化（可选）
"""

import hashlib
import json
import time
from typing import Any, Dict, List, Optional


class DomesticCache:
    """国内优化的响应缓存
    
    特点：
    - 默认TTL 600s（国内网络波动大，可复用结果更多）
    - 默认容量512（比通用缓存大）
    - 按模型分组，避免不同模型结果混淆
    """
    
    def __init__(
        self,
        max_size: int = 512,
        ttl: float = 600.0,
        *,
        per_model: bool = True,
    ):
        """
        Args:
            max_size: 最大缓存条目数
            ttl: 缓存有效期（秒），国内默认600s
            per_model: 是否按模型分组缓存
        """
        self.max_size = max_size
        self.ttl = ttl
        self.per_model = per_model
        
        # 缓存存储
        self._cache: Dict[str, dict] = {}
        self._order: list = []
        
        # 统计
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    
    def _make_key(self, messages: List[Dict], model: str = "", **kwargs) -> str:
        """生成缓存key"""
        # 忽略非确定性参数
        stable_kwargs = {k: v for k, v in kwargs.items()
                        if k not in ("stream", "no_cache", "adapter")}
        
        if self.per_model and model:
            # 按模型分组：相同prompt不同模型不共享缓存
            raw = json.dumps(
                {"m": messages, "k": stable_kwargs, "model": model},
                sort_keys=True,
                ensure_ascii=False,
            )
        else:
            raw = json.dumps(
                {"m": messages, "k": stable_kwargs},
                sort_keys=True,
                ensure_ascii=False,
            )
        
        return hashlib.md5(raw.encode("utf-8")).hexdigest()
    
    def get(self, messages: List[Dict], model: str = "", **kwargs) -> Optional[str]:
        """查找缓存"""
        key = self._make_key(messages, model, **kwargs)
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
    
    def put(self, messages: List[Dict], value: str, model: str = "", **kwargs) -> None:
        """写入缓存"""
        key = self._make_key(messages, model, **kwargs)
        
        # 如果已存在，更新
        if key in self._cache:
            self._cache[key] = {"value": value, "ts": time.time()}
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return
        
        # 驱逐旧条目
        while len(self._cache) >= self.max_size:
            if self._order:
                old_key = self._order.pop(0)
                self._cache.pop(old_key, None)
                self.evictions += 1
            else:
                break
        
        self._cache[key] = {"value": value, "ts": time.time()}
        self._order.append(key)
    
    def _evict(self, key: str) -> None:
        self._cache.pop(key, None)
        if key in self._order:
            self._order.remove(key)
    
    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._order.clear()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    
    def stats(self) -> Dict:
        """统计信息"""
        total = self.hits + self.misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": round(self.hits / max(total, 1), 4),
        }
    
    def __len__(self) -> int:
        return len(self._cache)
    
    def __repr__(self) -> str:
        total = self.hits + self.misses
        rate = self.hits / max(total, 1)
        return f"<DomesticCache {len(self._cache)}/{self.max_size} hit={rate:.1%}>"
