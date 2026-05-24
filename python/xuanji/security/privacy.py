"""
L6 隐私遮蔽层 — 自动检测并脱敏敏感信息

支持：手机号/身份证/邮箱/密码/API Key/银行卡/地址等。
在感知层（截屏OCR结果）自动应用。
"""

import re
from typing import Dict, List, Optional, Tuple


class PrivacyMask:
    """隐私信息遮蔽器
    
    功能：
    - 自动检测敏感信息（正则表达式匹配）
    - 脱敏替换（保留部分字符）
    - 支持自定义正则规则
    - 在OCR结果/日志/输出中自动应用
    
    Usage:
        mask = PrivacyMask()
        
        # 检测
        findings = mask.detect("手机号: 13812345678, 邮箱: test@example.com")
        # → [{"type": "phone", "match": "13812345678", ...}, ...]
        
        # 脱敏
        result = mask.mask("手机号: 13812345678")
        # → "手机号: 138****5678"
        
        # 自定义规则
        mask.add_rule("employee_id", r"EMP-\\d{6}", "EMP-******")
    """
    
    # === 内置检测规则 ===
    _DEFAULT_RULES = {
        "phone_cn": {
            "pattern": r"1[3-9]\d{9}",
            "mask": lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:],
            "description": "中国大陆手机号",
        },
        "phone_intl": {
            "pattern": r"\+?\d{1,3}[-.\s]?\d{3,4}[-.\s]?\d{4,8}",
            "mask": lambda m: m.group(0)[:4] + "****" + m.group(0)[-2:],
            "description": "国际手机号",
        },
        "id_card_cn": {
            "pattern": r"\b\d{17}[\dXx]\b",
            "mask": lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:],
            "description": "中国大陆身份证（18位）",
        },
        "id_card_old": {
            "pattern": r"\b\d{15}\b",
            "mask": lambda m: m.group(0)[:6] + "*******" + m.group(0)[-2:],
            "description": "中国大陆身份证（15位旧版）",
        },
        "email": {
            "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "mask": lambda m: m.group(0)[:2] + "***" + m.group(0)[m.group(0).index("@"):] if "@" in m.group(0) else "***",
            "description": "电子邮箱",
        },
        "bank_card": {
            "pattern": r"\b\d{13,19}\b",
            "mask": lambda m: m.group(0)[:4] + "****" + m.group(0)[-4:],
            "description": "银行卡号",
        },
        "api_key": {
            "pattern": r"(?:sk|pk|ak|api[_-]?key)[_-][a-zA-Z0-9]{20,}",
            "mask": lambda m: m.group(0)[:10] + "****",
            "description": "API密钥",
        },
        "bearer_token": {
            "pattern": r"Bearer\s+[a-zA-Z0-9_\-\.]+",
            "mask": lambda m: "Bearer ****",
            "description": "Bearer Token",
        },
        "jwt_token": {
            "pattern": r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}",
            "mask": lambda m: "****",
            "description": "JWT Token",
        },
        "aws_key": {
            "pattern": r"AKIA[0-9A-Z]{16}",
            "mask": lambda m: m.group(0)[:6] + "****",
            "description": "AWS Access Key",
        },
        "github_token": {
            "pattern": r"ghp_[a-zA-Z0-9]{36}",
            "mask": lambda m: m.group(0)[:7] + "****",
            "description": "GitHub Personal Token",
        },
        "password_field": {
            "pattern": r"(?:password|passwd|pwd|密码)\s*[=:]\s*\S+",
            "mask": lambda m: m.group(0).split("=")[0].split(":")[0].rstrip() + "= ****",
            "description": "密码字段",
        },
        "private_key": {
            "pattern": r"-----BEGIN (?:RSA |DSA |EC )?PRIVATE KEY-----",
            "mask": lambda m: "-----BEGIN PRIVATE KEY-----",
            "description": "私钥头",
        },
        "ip_address": {
            "pattern": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            "mask": lambda m: ".".join([
                m.group(0).split(".")[0],
                "***",
                "***",
                m.group(0).split(".")[-1]
            ]),
            "description": "IP地址",
        },
        "address_cn": {
            "pattern": r"(?:省|市|区|县|镇|街道|路|号|栋|单元|室|幢){2,}[\d一二三四五六七八九十百千万号栋单元室幢楼]*",
            "mask": lambda m: "****",
            "description": "中文地址",
        },
        "mac_address": {
            "pattern": r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}",
            "mask": lambda m: m.group(0)[:8] + ":**:**",
            "description": "MAC地址",
        },
        "passport_cn": {
            "pattern": r"[HEHHDH]\d{8}|\d{9}",
            "mask": lambda m: m.group(0)[:2] + "*******",
            "description": "中国护照号",
        },
        "social_credit": {
            "pattern": r"[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}",
            "mask": lambda m: m.group(0)[:3] + "*********" + m.group(0)[-3:],
            "description": "统一社会信用代码",
        },
    }
    
    def __init__(self):
        self._rules: Dict[str, Dict] = {}
        self._compiled: Dict[str, re.Pattern] = {}
        
        # 加载默认规则
        for name, rule in self._DEFAULT_RULES.items():
            self._add_rule(name, rule["pattern"], rule["mask"], rule.get("description", ""))
    
    def mask(self, text: str) -> str:
        """脱敏处理文本
        
        Args:
            text: 原始文本
        
        Returns:
            脱敏后的文本
        """
        result = text
        
        # 按规则长度排序（先匹配长的，避免短规则截断长规则）
        sorted_rules = sorted(self._compiled.items(), key=lambda x: len(x[1].pattern), reverse=True)
        
        for name, pattern in sorted_rules:
            rule = self._rules[name]
            result = pattern.sub(rule["mask"], result)
        
        return result
    
    def detect(self, text: str) -> List[Dict]:
        """检测文本中的敏感信息
        
        Args:
            text: 要检测的文本
        
        Returns:
            找到的敏感信息列表，每项包含：
            {
                "type": 类型名,
                "match": 匹配内容,
                "position": (start, end),
                "description": 描述,
            }
        """
        findings = []
        
        for name, pattern in self._compiled.items():
            for match in pattern.finditer(text):
                findings.append({
                    "type": name,
                    "match": match.group(0),
                    "position": (match.start(), match.end()),
                    "description": self._rules[name].get("description", ""),
                })
        
        # 按位置排序
        findings.sort(key=lambda x: x["position"][0])
        return findings
    
    def add_rule(self, name: str, pattern: str, replacement: str = "****") -> None:
        """添加自定义检测规则
        
        Args:
            name: 规则名称
            pattern: 正则表达式
            replacement: 替换字符串（或lambda函数）
        """
        if callable(replacement):
            mask_func = replacement
        else:
            mask_func = lambda m, r=replacement: r
        
        self._add_rule(name, pattern, mask_func, "自定义规则")
    
    def remove_rule(self, name: str) -> bool:
        """移除规则"""
        if name in self._rules:
            del self._rules[name]
            del self._compiled[name]
            return True
        return False
    
    def get_rules(self) -> Dict:
        """获取所有规则"""
        return {
            name: {
                "pattern": rule["pattern"],
                "description": rule.get("description", ""),
            }
            for name, rule in self._rules.items()
        }
    
    def mask_dict(self, data: Dict, sensitive_keys: List[str] = None) -> Dict:
        """脱敏字典中的敏感值
        
        Args:
            data: 原始字典
            sensitive_keys: 敏感键名列表（如 ["password", "token"]）
        
        Returns:
            脱敏后的字典
        """
        result = {}
        sensitive_keys = [k.lower() for k in (sensitive_keys or [])]
        
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.mask_dict(value, sensitive_keys)
            elif isinstance(value, str):
                # 检查键名是否敏感
                if key.lower() in sensitive_keys or self._is_sensitive_key(key):
                    result[key] = "****"
                else:
                    result[key] = self.mask(value)
            else:
                result[key] = value
        
        return result
    
    def mask_list(self, items: List) -> List:
        """脱敏列表中的字符串"""
        return [self.mask(item) if isinstance(item, str) else item for item in items]
    
    def is_clean(self, text: str) -> bool:
        """检查文本是否不含敏感信息"""
        return len(self.detect(text)) == 0
    
    def _add_rule(self, name: str, pattern: str, mask_func, description: str) -> None:
        """内部添加规则"""
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._rules[name] = {
                "pattern": pattern,
                "mask": mask_func,
                "description": description,
            }
            self._compiled[name] = compiled
        except re.error:
            pass  # 忽略无效正则
    
    def _is_sensitive_key(self, key: str) -> bool:
        """判断键名是否表示敏感信息"""
        sensitive = [
            "password", "passwd", "pwd", "密码",
            "secret", "token", "key", "密钥",
            "credential", "凭证",
            "api_key", "apikey", "api-key",
            "access_token", "refresh_token",
            "private", "隐私",
        ]
        lower = key.lower()
        return any(s in lower for s in sensitive)
    
    def redact_ocr(self, ocr_text: str) -> str:
        """处理OCR识别结果（自动脱敏）
        
        专门用于截屏OCR结果的隐私保护。
        
        Args:
            ocr_text: OCR识别的文本
        
        Returns:
            脱敏后的文本
        """
        return self.mask(ocr_text)
    
    def summary(self, text: str) -> Dict:
        """生成敏感信息摘要"""
        findings = self.detect(text)
        
        type_counts = {}
        for f in findings:
            t = f["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        
        return {
            "total_findings": len(findings),
            "by_type": type_counts,
            "types_found": list(type_counts.keys()),
            "is_clean": len(findings) == 0,
        }
