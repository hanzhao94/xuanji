"""
L5 内容安全策略 — 限制Agent网络访问范围

控制Agent能访问的URL/IP/域名。
支持通配符、正则、CIDR IP范围。
"""

import ipaddress
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse


class CSPManager:
    """内容安全策略管理器
    
    功能：
    - allow/deny规则列表（支持通配符和正则）
    - CIDR IP范围控制
    - 违规记录+告警
    - 规则持久化
    
    Usage:
        csp = CSPManager(config_path="~/.xuanji/csp_rules.json")
        
        # 添加规则
        csp.allow("*.github.com")
        csp.deny("*.evil.com")
        csp.deny_ip_range("192.168.0.0/16")
        
        # 检查URL
        csp.check_url("https://github.com/repo")      # True
        csp.check_url("https://evil.com/malware")     # False
        
        # 获取违规记录
        violations = csp.get_violations()
    """
    
    # 默认安全规则
    _DEFAULT_DENY = [
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",  # 云元数据
        "169.254.169.254",  # AWS元数据
    ]
    
    _DEFAULT_DENY_IP_RANGES = [
        "10.0.0.0/8",       # 私有网络A
        "172.16.0.0/12",    # 私有网络B
        "192.168.0.0/16",   # 私有网络C
        "169.254.0.0/16",   # 链路本地
        "127.0.0.0/8",      # 回环
        "::1/128",          # IPv6回环
        "fc00::/7",         # IPv6私有
        "fe80::/10",        # IPv6链路本地
    ]
    
    def __init__(self, config_path: str = "~/.xuanji/csp_rules.json"):
        self._config_path = os.path.expanduser(config_path)
        self._allow_patterns: List[str] = []
        self._deny_patterns: List[str] = list(self._DEFAULT_DENY)
        self._allow_regex: List[re.Pattern] = []
        self._deny_regex: List[re.Pattern] = []
        self._allow_ip_ranges: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._deny_ip_ranges: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self._violations: List[Dict] = []
        self._agent_rules: Dict[str, Dict] = {}  # per-agent规则
        
        self._load_ip_ranges(self._DEFAULT_DENY_IP_RANGES, self._deny_ip_ranges)
        self._load()
    
    def allow(self, pattern: str) -> None:
        """添加允许规则
        
        Args:
            pattern: URL模式，支持通配符（*.example.com）或正则（以regex:开头）
        """
        if pattern.startswith("regex:"):
            self._allow_regex.append(re.compile(pattern[6:]))
        else:
            self._allow_patterns.append(pattern)
        self._save()
    
    def deny(self, pattern: str) -> None:
        """添加拒绝规则
        
        Args:
            pattern: URL模式，支持通配符或正则
        """
        if pattern.startswith("regex:"):
            self._deny_regex.append(re.compile(pattern[6:]))
        else:
            self._deny_patterns.append(pattern)
        self._save()
    
    def allow_ip_range(self, cidr: str) -> None:
        """添加允许的IP范围（CIDR格式）
        
        Args:
            cidr: CIDR表示法，如 "203.0.113.0/24"
        """
        self._load_ip_ranges([cidr], self._allow_ip_ranges)
        self._save()
    
    def deny_ip_range(self, cidr: str) -> None:
        """添加拒绝的IP范围（CIDR格式）
        
        Args:
            cidr: CIDR表示法，如 "192.168.0.0/16"
        """
        self._load_ip_ranges([cidr], self._deny_ip_ranges)
        self._save()
    
    def check_url(self, url: str, agent_id: str = "default") -> bool:
        """检查URL是否允许访问
        
        Args:
            url: 要检查的URL
            agent_id: Agent标识（支持per-agent规则）
        
        Returns:
            True=允许, False=拒绝
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
        except Exception:
            return False
        
        # 检查per-agent规则
        agent_rule = self._agent_rules.get(agent_id)
        if agent_rule:
            if not self._check_against_rules(hostname, agent_rule):
                self._record_violation(url, agent_id, "agent_rule")
                return False
        
        # 1. 检查deny列表（优先）
        if self._match_patterns(hostname, self._deny_patterns):
            self._record_violation(url, agent_id, "deny_pattern")
            return False
        
        if self._match_regex(hostname, self._deny_regex):
            self._record_violation(url, agent_id, "deny_regex")
            return False
        
        # 2. 检查IP范围
        ip = self._resolve_ip(hostname)
        if ip:
            if self._ip_in_ranges(ip, self._deny_ip_ranges):
                self._record_violation(url, agent_id, "deny_ip_range")
                return False
            # 如果有allow_ip_ranges，检查是否在其中
            if self._allow_ip_ranges and not self._ip_in_ranges(ip, self._allow_ip_ranges):
                self._record_violation(url, agent_id, "allow_ip_range")
                return False
        
        # 3. 检查allow列表
        if self._allow_patterns or self._allow_regex:
            if not self._match_patterns(hostname, self._allow_patterns) and \
               not self._match_regex(hostname, self._allow_regex):
                self._record_violation(url, agent_id, "not_in_allow_list")
                return False
        
        return True
    
    def configure_agent(self, agent_id: str, allow: List[str] = None, deny: List[str] = None) -> None:
        """为特定Agent配置规则
        
        Args:
            agent_id: Agent标识
            allow: 允许的模式列表
            deny: 拒绝的模式列表
        """
        self._agent_rules[agent_id] = {
            "allow": allow or [],
            "deny": deny or [],
        }
        self._save()
    
    def get_violations(self, agent_id: str = None, limit: int = 100) -> List[Dict]:
        """获取违规记录
        
        Args:
            agent_id: 按Agent过滤
            limit: 最大返回条数
        
        Returns:
            违规记录列表
        """
        results = self._violations
        if agent_id:
            results = [v for v in results if v.get("agent_id") == agent_id]
        return results[-limit:]
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "allow_patterns": len(self._allow_patterns),
            "deny_patterns": len(self._deny_patterns),
            "allow_regex": len(self._allow_regex),
            "deny_regex": len(self._deny_regex),
            "allow_ip_ranges": len(self._allow_ip_ranges),
            "deny_ip_ranges": len(self._deny_ip_ranges),
            "total_violations": len(self._violations),
            "agent_rules": len(self._agent_rules),
        }
    
    def _check_against_rules(self, hostname: str, rules: Dict) -> bool:
        """检查per-agent规则"""
        # deny优先
        if self._match_patterns(hostname, rules.get("deny", [])):
            return False
        # allow检查
        if rules.get("allow"):
            if not self._match_patterns(hostname, rules["allow"]):
                return False
        return True
    
    def _match_patterns(self, hostname: str, patterns: List[str]) -> bool:
        """通配符匹配"""
        lower_host = hostname.lower()
        for pattern in patterns:
            if pattern.startswith("regex:"):
                continue  # 正则单独处理
            # 通配符转正则
            regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
            if re.match(regex, lower_host, re.IGNORECASE):
                return True
        return False
    
    def _match_regex(self, hostname: str, patterns: List[re.Pattern]) -> bool:
        """正则匹配"""
        for pattern in patterns:
            if pattern.search(hostname):
                return True
        return False
    
    def _ip_in_ranges(self, ip_str: str, ranges: List) -> bool:
        """检查IP是否在范围内"""
        try:
            ip = ipaddress.ip_address(ip_str)
            for network in ranges:
                if ip in network:
                    return True
        except ValueError:
            pass
        return False
    
    def _resolve_ip(self, hostname: str) -> Optional[str]:
        """尝试解析hostname为IP（不实际发起DNS请求）"""
        # 如果本身就是IP，直接返回
        try:
            ipaddress.ip_address(hostname)
            return hostname
        except ValueError:
            return None
    
    def _load_ip_ranges(self, cidrs: List[str], target_list: List) -> None:
        """加载CIDR范围"""
        for cidr in cidrs:
            try:
                target_list.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                pass
    
    def _record_violation(self, url: str, agent_id: str, reason: str) -> None:
        """记录违规"""
        self._violations.append({
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "url": url,
            "agent_id": agent_id,
            "reason": reason,
        })
    
    def _save(self) -> None:
        """持久化规则到JSON文件"""
        data = {
            "allow_patterns": self._allow_patterns,
            "deny_patterns": self._deny_patterns,
            "allow_ip_ranges": [str(r) for r in self._allow_ip_ranges],
            "deny_ip_ranges": [str(r) for r in self._deny_ip_ranges],
            "agent_rules": self._agent_rules,
            "updated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
        
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load(self) -> None:
        """从JSON文件加载规则"""
        if not os.path.exists(self._config_path):
            return
        
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            self._allow_patterns = data.get("allow_patterns", [])
            self._deny_patterns = data.get("deny_patterns", list(self._DEFAULT_DENY))
            self._agent_rules = data.get("agent_rules", {})
            
            self._allow_ip_ranges = []
            self._deny_ip_ranges = list(self._deny_ip_ranges)  # 保留默认
            self._load_ip_ranges(data.get("allow_ip_ranges", []), self._allow_ip_ranges)
            self._load_ip_ranges(data.get("deny_ip_ranges", []), self._deny_ip_ranges)
        except (json.JSONDecodeError, OSError):
            pass
    
    def export_rules(self) -> Dict:
        """导出当前规则"""
        return {
            "allow_patterns": self._allow_patterns,
            "deny_patterns": self._deny_patterns,
            "allow_ip_ranges": [str(r) for r in self._allow_ip_ranges],
            "deny_ip_ranges": [str(r) for r in self._deny_ip_ranges],
            "agent_rules": self._agent_rules,
            "stats": self.get_stats(),
        }
