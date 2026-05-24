"""
L6 密钥管理 — 密钥安全存储

简单混淆存储（hashlib+base64），比明文好。
支持环境变量引用和密钥遮盖。
"""

import base64
import hashlib
import json
import os
import re
from typing import Dict, List, Optional


class SecretStore:
    """密钥安全存储
    
    存储位置：~/.xuanji/secrets.json（混淆加密）
    
    特性：
    - 基于hashlib+base64的简单混淆（不是真AES，但比明文好）
    - 支持环境变量引用：${ENV_VAR}
    - 支持密钥引用：${secret:name}
    - 自动遮盖文本中的已知密钥
    
    Usage:
        store = SecretStore(data_dir="~/.xuanji")
        
        # 存储密钥
        store.store("deepseek_key", "sk-xxx123")
        
        # 获取密钥
        key = store.get("deepseek_key")  # → "sk-xxx123"
        
        # 遮盖文本中的密钥
        safe = store.mask("my key is sk-xxx123")  # → "my key is [***]"
        
        # 列出密钥名（不返回值）
        names = store.list_names()  # → ["deepseek_key"]
        
        # 解析引用
        val = store.resolve("${secret:deepseek_key}")  # → "sk-xxx123"
        val = store.resolve("${HOME}")  # → "/home/user"
    """
    
    def __init__(self, data_dir: str = "~/.xuanji"):
        self._data_dir = os.path.expanduser(data_dir)
        self._store_path = os.path.join(self._data_dir, "secrets.json")
        self._cache: Optional[Dict[str, str]] = None
        
        # 混淆用的盐 — 基于机器特征生成，每台机器不同
        self._salt = self._derive_salt()
    
    def store(self, name: str, value: str) -> None:
        """存储密钥
        
        Args:
            name: 密钥名称
            value: 密钥值
        """
        data = self._load()
        data[name] = self._encode(value)
        self._save(data)
        self._cache = None  # 清缓存
    
    def get(self, name: str) -> Optional[str]:
        """获取密钥
        
        Args:
            name: 密钥名称
        
        Returns:
            密钥值，不存在返回 None
        """
        data = self._load()
        encoded = data.get(name)
        if encoded is None:
            return None
        return self._decode(encoded)
    
    def delete(self, name: str) -> bool:
        """删除密钥
        
        Args:
            name: 密钥名称
        
        Returns:
            True=删除成功, False=不存在
        """
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        self._cache = None
        return True
    
    def list_names(self) -> List[str]:
        """列出所有密钥名（不返回值）
        
        Returns:
            密钥名称列表
        """
        return list(self._load().keys())
    
    def mask(self, text: str) -> str:
        """遮盖文本中出现的已知密钥
        
        Args:
            text: 待遮盖的文本
        
        Returns:
            遮盖后的文本
        """
        data = self._load()
        result = text
        
        for name, encoded in data.items():
            try:
                value = self._decode(encoded)
                if value and len(value) >= 4 and value in result:
                    result = result.replace(value, "[***]")
            except Exception:
                continue
        
        return result
    
    def resolve(self, text: str) -> str:
        """解析文本中的引用
        
        支持：
        - ${ENV_VAR} — 环境变量
        - ${secret:name} — 密钥引用
        
        Args:
            text: 包含引用的文本
        
        Returns:
            解析后的文本
        """
        # 先解析 ${secret:name}
        def _replace_secret(m):
            name = m.group(1)
            val = self.get(name)
            return val if val is not None else m.group(0)
        
        result = re.sub(r'\$\{secret:(\w+)\}', _replace_secret, text)
        
        # 再解析 ${ENV_VAR}
        def _replace_env(m):
            var = m.group(1)
            val = os.environ.get(var)
            return val if val is not None else m.group(0)
        
        result = re.sub(r'\$\{(\w+)\}', _replace_env, result)
        
        return result
    
    # === 内部方法 ===
    
    def _encode(self, value: str) -> str:
        """简单混淆编码（base64 + XOR with salt hash）"""
        value_bytes = value.encode("utf-8")
        key_bytes = self._salt
        
        # XOR混淆
        xored = bytes(
            b ^ key_bytes[i % len(key_bytes)]
            for i, b in enumerate(value_bytes)
        )
        
        return base64.b64encode(xored).decode("ascii")
    
    def _decode(self, encoded: str) -> str:
        """解码"""
        xored = base64.b64decode(encoded)
        key_bytes = self._salt
        
        # XOR还原
        value_bytes = bytes(
            b ^ key_bytes[i % len(key_bytes)]
            for i, b in enumerate(xored)
        )
        
        return value_bytes.decode("utf-8")
    
    def _derive_salt(self) -> bytes:
        """基于机器特征生成盐"""
        # 用用户名 + home目录 + 固定字符串作为种子
        seed = f"xuanji:{os.getenv('USERNAME', os.getenv('USER', 'default'))}:{os.path.expanduser('~')}"
        return hashlib.sha256(seed.encode("utf-8")).digest()
    
    def _load(self) -> Dict[str, str]:
        """加载密钥存储文件"""
        if self._cache is not None:
            return self._cache
        
        if not os.path.isfile(self._store_path):
            return {}
        
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._cache = data
            return data
        except (json.JSONDecodeError, OSError):
            return {}
    
    def _save(self, data: Dict[str, str]) -> None:
        """保存密钥存储文件（原子写入）"""
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        
        tmp_path = self._store_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 原子替换
        if os.path.exists(self._store_path):
            os.replace(tmp_path, self._store_path)
        else:
            os.rename(tmp_path, self._store_path)
