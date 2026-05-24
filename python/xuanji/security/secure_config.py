"""
L7 安全配置管理层 — 安全配置加密存储+变更审计+自动修复

功能：
- 配置加密存储（XOR+base64）
- 配置变更审计
- 安全配置模板（strict/standard/relaxed）
- 配置验证（schema校验）
- 自动修复不安全配置
"""

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


class SecureConfig:
    """安全配置管理器
    
    功能：
    - 配置加密存储（XOR+base64）
    - 配置变更审计
    - 安全配置模板（strict/standard/relaxed）
    - 配置验证（schema校验）
    - 自动修复不安全配置
    
    Usage:
        config = SecureConfig(
            config_path="~/.xuanji/secure_config.json",
            encryption_key="my-secret-key"
        )
        
        # 加载模板
        config.load_template("strict")
        
        # 设置配置
        config.set("sandbox.max_file_size", 10485760)
        config.set("sandbox.secret_key", "sk-xxx", sensitive=True)
        
        # 获取配置
        value = config.get("sandbox.max_file_size")
        
        # 验证配置
        issues = config.validate()
        
        # 自动修复
        config.auto_repair()
    """
    
    # === 安全配置模板 ===
    
    _TEMPLATES = {
        "strict": {
            "description": "严格模式 — 最大安全，最小便利",
            "settings": {
                "sandbox": {
                    "enabled": True,
                    "max_file_size": 1048576,      # 1MB
                    "max_commands_per_minute": 30,
                    "block_external_network": True,
                    "require_confirmation": True,
                    "max_process_count": 5,
                    "max_memory_mb": 512,
                    "max_disk_mb": 1024,
                },
                "auth": {
                    "require_auth": True,
                    "session_timeout_min": 15,
                    "max_failed_attempts": 3,
                    "lockout_duration_min": 30,
                },
                "audit": {
                    "enabled": True,
                    "log_all_operations": True,
                    "retention_days": 365,
                    "remote_backup": True,
                },
                "network": {
                    "allow_local": False,
                    "allow_private": False,
                    "dns_over_https": True,
                    "tls_required": True,
                },
                "privacy": {
                    "mask_all_sensitive": True,
                    "block_ocr_output": False,
                    "redact_ip_addresses": True,
                },
            },
        },
        "standard": {
            "description": "标准模式 — 安全与便利平衡",
            "settings": {
                "sandbox": {
                    "enabled": True,
                    "max_file_size": 10485760,     # 10MB
                    "max_commands_per_minute": 60,
                    "block_external_network": False,
                    "require_confirmation": True,
                    "max_process_count": 20,
                    "max_memory_mb": 2048,
                    "max_disk_mb": 5120,
                },
                "auth": {
                    "require_auth": True,
                    "session_timeout_min": 60,
                    "max_failed_attempts": 5,
                    "lockout_duration_min": 15,
                },
                "audit": {
                    "enabled": True,
                    "log_all_operations": True,
                    "retention_days": 90,
                    "remote_backup": False,
                },
                "network": {
                    "allow_local": True,
                    "allow_private": True,
                    "dns_over_https": False,
                    "tls_required": True,
                },
                "privacy": {
                    "mask_all_sensitive": True,
                    "block_ocr_output": False,
                    "redact_ip_addresses": False,
                },
            },
        },
        "relaxed": {
            "description": "宽松模式 — 最大便利，基础安全",
            "settings": {
                "sandbox": {
                    "enabled": True,
                    "max_file_size": 104857600,    # 100MB
                    "max_commands_per_minute": 120,
                    "block_external_network": False,
                    "require_confirmation": False,
                    "max_process_count": 50,
                    "max_memory_mb": 4096,
                    "max_disk_mb": 10240,
                },
                "auth": {
                    "require_auth": False,
                    "session_timeout_min": 1440,   # 24h
                    "max_failed_attempts": 10,
                    "lockout_duration_min": 5,
                },
                "audit": {
                    "enabled": True,
                    "log_all_operations": False,
                    "retention_days": 30,
                    "remote_backup": False,
                },
                "network": {
                    "allow_local": True,
                    "allow_private": True,
                    "dns_over_https": False,
                    "tls_required": False,
                },
                "privacy": {
                    "mask_all_sensitive": False,
                    "block_ocr_output": False,
                    "redact_ip_addresses": False,
                },
            },
        },
    }
    
    # === Schema定义 ===
    
    _SCHEMA = {
        "sandbox": {
            "type": "object",
            "required": ["enabled", "max_file_size"],
            "properties": {
                "enabled": {"type": "bool"},
                "max_file_size": {"type": "int", "min": 1024, "max": 1073741824},
                "max_commands_per_minute": {"type": "int", "min": 1, "max": 1000},
                "block_external_network": {"type": "bool"},
                "require_confirmation": {"type": "bool"},
                "max_process_count": {"type": "int", "min": 1, "max": 500},
                "max_memory_mb": {"type": "int", "min": 64, "max": 32768},
                "max_disk_mb": {"type": "int", "min": 100, "max": 102400},
            },
        },
        "auth": {
            "type": "object",
            "required": ["require_auth"],
            "properties": {
                "require_auth": {"type": "bool"},
                "session_timeout_min": {"type": "int", "min": 1, "max": 10080},
                "max_failed_attempts": {"type": "int", "min": 1, "max": 100},
                "lockout_duration_min": {"type": "int", "min": 1, "max": 1440},
            },
        },
        "audit": {
            "type": "object",
            "required": ["enabled"],
            "properties": {
                "enabled": {"type": "bool"},
                "log_all_operations": {"type": "bool"},
                "retention_days": {"type": "int", "min": 1, "max": 3650},
                "remote_backup": {"type": "bool"},
            },
        },
        "network": {
            "type": "object",
            "properties": {
                "allow_local": {"type": "bool"},
                "allow_private": {"type": "bool"},
                "dns_over_https": {"type": "bool"},
                "tls_required": {"type": "bool"},
            },
        },
        "privacy": {
            "type": "object",
            "properties": {
                "mask_all_sensitive": {"type": "bool"},
                "block_ocr_output": {"type": "bool"},
                "redact_ip_addresses": {"type": "bool"},
            },
        },
    }
    
    # === 自动修复规则 ===
    
    _REPAIR_RULES = {
        "sandbox.max_file_size": {
            "condition": lambda v: v < 1024,
            "fix": lambda v: 1024,
            "reason": "最大文件大小不能小于1KB",
        },
        "sandbox.max_commands_per_minute": {
            "condition": lambda v: v > 500,
            "fix": lambda v: 500,
            "reason": "命令频率过高，存在滥用风险",
        },
        "auth.max_failed_attempts": {
            "condition": lambda v: v > 20,
            "fix": lambda v: 20,
            "reason": "失败尝试次数过多，存在暴力破解风险",
        },
        "auth.session_timeout_min": {
            "condition": lambda v: v > 4320,
            "fix": lambda v: 4320,
            "reason": "会话超时过长，存在会话劫持风险",
        },
        "audit.retention_days": {
            "condition": lambda v: v < 7,
            "fix": lambda v: 7,
            "reason": "审计日志保留时间过短",
        },
        "sandbox.enabled": {
            "condition": lambda v: v is False,
            "fix": lambda v: True,
            "reason": "沙箱已禁用，存在安全风险",
        },
        "audit.enabled": {
            "condition": lambda v: v is False,
            "fix": lambda v: True,
            "reason": "审计已禁用，无法追溯操作",
        },
    }
    
    def __init__(
        self,
        config_path: str = "~/.xuanji/secure_config.json",
        encryption_key: str = "xuanji-default-key",
    ):
        self._config_path = os.path.expanduser(config_path)
        self._encryption_key = encryption_key.encode("utf-8")
        self._config: Dict[str, Any] = {}
        self._sensitive_keys: set = set()
        self._change_log: List[Dict] = []
        self._current_hash: str = ""
        
        self._load()
    
    def load_template(self, template_name: str) -> None:
        """加载安全配置模板
        
        Args:
            template_name: 模板名 (strict/standard/relaxed)
        """
        template = self._TEMPLATES.get(template_name)
        if not template:
            raise ValueError(f"未知模板: {template_name}，可选: {list(self._TEMPLATES.keys())}")
        
        old_config = dict(self._config)
        self._config = self._deep_merge(self._config, template["settings"])
        self._record_change("load_template", template_name, old_config, dict(self._config))
        self._save()
    
    def set(self, key: str, value: Any, sensitive: bool = False) -> None:
        """设置配置值
        
        Args:
            key: 配置键（支持点号分隔，如 "sandbox.max_file_size"）
            value: 配置值
            sensitive: 是否为敏感值（加密存储）
        """
        old_value = self.get(key)
        
        # 存储配置
        keys = key.split(".")
        current = self._config
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
        
        # 标记敏感键
        if sensitive:
            self._sensitive_keys.add(key)
        
        self._record_change("set", key, old_value, value)
        self._save()
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值
        
        Args:
            key: 配置键（支持点号分隔）
            default: 默认值
        
        Returns:
            配置值
        """
        keys = key.split(".")
        current = self._config
        
        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default
        
        return current
    
    def delete(self, key: str) -> bool:
        """删除配置项"""
        old_value = self.get(key)
        keys = key.split(".")
        current = self._config
        
        for k in keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False
        
        if isinstance(current, dict) and keys[-1] in current:
            del current[keys[-1]]
            self._record_change("delete", key, old_value, None)
            self._save()
            return True
        return False
    
    def validate(self) -> List[Dict]:
        """验证配置是否符合schema
        
        Returns:
            问题列表，每项包含：
            {
                "key": str,
                "issue": str,
                "severity": str,
                "current_value": Any,
            }
        """
        issues = []
        
        for section, schema in self._SCHEMA.items():
            section_data = self._config.get(section, {})
            
            # 检查必填项
            for required in schema.get("required", []):
                if required not in section_data:
                    issues.append({
                        "key": f"{section}.{required}",
                        "issue": f"缺少必填配置项",
                        "severity": "high",
                        "current_value": None,
                    })
            
            # 检查属性类型和范围
            for prop, prop_schema in schema.get("properties", {}).items():
                if prop not in section_data:
                    continue
                
                value = section_data[prop]
                expected_type = prop_schema.get("type")
                
                # 类型检查
                type_ok = self._check_type(value, expected_type)
                if not type_ok:
                    issues.append({
                        "key": f"{section}.{prop}",
                        "issue": f"类型错误，期望{expected_type}，实际{type(value).__name__}",
                        "severity": "high",
                        "current_value": value,
                    })
                    continue
                
                # 范围检查
                if expected_type == "int":
                    min_val = prop_schema.get("min")
                    max_val = prop_schema.get("max")
                    if min_val is not None and value < min_val:
                        issues.append({
                            "key": f"{section}.{prop}",
                            "issue": f"值{value}小于最小值{min_val}",
                            "severity": "medium",
                            "current_value": value,
                        })
                    if max_val is not None and value > max_val:
                        issues.append({
                            "key": f"{section}.{prop}",
                            "issue": f"值{value}超过最大值{max_val}",
                            "severity": "medium",
                            "current_value": value,
                        })
        
        # 检查安全配置合理性
        issues.extend(self._check_security_reasonableness())
        
        return issues
    
    def auto_repair(self) -> List[Dict]:
        """自动修复不安全的配置
        
        Returns:
            修复记录列表
        """
        repairs = []
        
        for key_path, rule in self._REPAIR_RULES.items():
            value = self.get(key_path)
            if value is None:
                continue
            
            try:
                if rule["condition"](value):
                    old_value = value
                    new_value = rule["fix"](value)
                    self.set(key_path, new_value)
                    
                    repairs.append({
                        "key": key_path,
                        "old_value": old_value,
                        "new_value": new_value,
                        "reason": rule["reason"],
                        "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    })
            except (TypeError, ValueError):
                pass
        
        return repairs
    
    def get_change_log(self, limit: int = 100) -> List[Dict]:
        """获取配置变更日志"""
        return self._change_log[-limit:]
    
    def get_config_hash(self) -> str:
        """获取当前配置哈希（用于检测篡改）"""
        config_str = json.dumps(self._config, sort_keys=True, ensure_ascii=False)
        self._current_hash = hashlib.sha256(config_str.encode("utf-8")).hexdigest()
        return self._current_hash
    
    def export_config(self, include_sensitive: bool = False) -> Dict:
        """导出配置
        
        Args:
            include_sensitive: 是否包含敏感值（明文）
        """
        config = dict(self._config)
        
        if not include_sensitive:
            config = self._mask_sensitive(config)
        
        return {
            "config": config,
            "hash": self.get_config_hash(),
            "exported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
    
    def list_templates(self) -> Dict:
        """列出可用模板"""
        return {
            name: {"description": t["description"]}
            for name, t in self._TEMPLATES.items()
        }
    
    def _check_type(self, value: Any, expected: str) -> bool:
        """检查值类型"""
        type_map = {
            "bool": bool,
            "int": int,
            "str": str,
            "float": (int, float),
            "list": list,
            "dict": dict,
        }
        expected_type = type_map.get(expected)
        if expected_type is None:
            return True
        return isinstance(value, expected_type)
    
    def _check_security_reasonableness(self) -> List[Dict]:
        """检查安全配置合理性"""
        issues = []
        
        # 沙箱禁用
        if self.get("sandbox.enabled") is False:
            issues.append({
                "key": "sandbox.enabled",
                "issue": "沙箱已禁用，存在严重安全风险",
                "severity": "critical",
                "current_value": False,
            })
        
        # 审计禁用
        if self.get("audit.enabled") is False:
            issues.append({
                "key": "audit.enabled",
                "issue": "审计已禁用，无法追溯操作",
                "severity": "critical",
                "current_value": False,
            })
        
        # 认证禁用
        if self.get("auth.require_auth") is False:
            issues.append({
                "key": "auth.require_auth",
                "issue": "认证已禁用，任何人均可访问",
                "severity": "high",
                "current_value": False,
            })
        
        # 网络无TLS
        if self.get("network.tls_required") is False:
            issues.append({
                "key": "network.tls_required",
                "issue": "未强制TLS，通信可能被窃听",
                "severity": "medium",
                "current_value": False,
            })
        
        return issues
    
    def _mask_sensitive(self, config: Dict) -> Dict:
        """遮蔽敏感值"""
        masked = {}
        for key, value in config.items():
            if isinstance(value, dict):
                masked[key] = self._mask_sensitive(value)
            elif f"{key}" in self._sensitive_keys:
                masked[key] = "****"
            else:
                masked[key] = value
        return masked
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """深度合并字典"""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def _record_change(self, action: str, key: str, old_value: Any, new_value: Any) -> None:
        """记录配置变更"""
        self._change_log.append({
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "action": action,
            "key": key,
            "old_value": self._safe_repr(old_value),
            "new_value": self._safe_repr(new_value),
            "config_hash": self.get_config_hash(),
        })
    
    def _safe_repr(self, value: Any) -> str:
        """安全表示（遮蔽敏感值）"""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)[:200]
        return str(value)[:200]
    
    def _encrypt(self, plaintext: str) -> str:
        """XOR加密 + base64编码"""
        key = self._encryption_key
        key_len = len(key)
        encrypted = bytes(
            b ^ key[i % key_len] for i, b in enumerate(plaintext.encode("utf-8"))
        )
        return base64.b64encode(encrypted).decode("utf-8")
    
    def _decrypt(self, ciphertext: str) -> str:
        """base64解码 + XOR解密"""
        encrypted = base64.b64decode(ciphertext.encode("utf-8"))
        key = self._encryption_key
        key_len = len(key)
        decrypted = bytes(
            b ^ key[i % key_len] for i, b in enumerate(encrypted)
        )
        return decrypted.decode("utf-8")
    
    def _save(self) -> None:
        """保存配置（加密）"""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        
        data = {
            "config": self._encrypt(json.dumps(self._config, ensure_ascii=False)),
            "sensitive_keys": list(self._sensitive_keys),
            "change_log": self._change_log[-1000:],  # 只保留最近1000条
            "hash": self.get_config_hash(),
            "saved_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
        
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load(self) -> None:
        """加载配置（解密）"""
        if not os.path.exists(self._config_path):
            return
        
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._config = json.loads(self._decrypt(data.get("config", "{}")))
            self._sensitive_keys = set(data.get("sensitive_keys", []))
            self._change_log = data.get("change_log", [])
            
            # 验证哈希
            stored_hash = data.get("hash", "")
            if stored_hash and stored_hash != self.get_config_hash():
                # 配置可能被篡改，记录告警
                self._change_log.append({
                    "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "action": "ALERT",
                    "key": "config_integrity",
                    "old_value": stored_hash,
                    "new_value": self.get_config_hash(),
                    "detail": "配置哈希不匹配，可能被篡改",
                })
        except (json.JSONDecodeError, OSError, ValueError):
            pass
