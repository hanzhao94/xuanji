"""
L0 深度安全扫描层 — 深度扫描Agent行为

基于规则+统计的混合检测，生成安全评分。
扫描类型：异常行为/数据泄露/权限滥用/资源滥用/通信异常。
"""

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class DeepScanner:
    """深度安全扫描器
    
    功能：
    - 异常行为检测（频率/模式/时间异常）
    - 数据泄露检测（大量外发/敏感数据外传）
    - 权限滥用检测（越权操作/频繁失败）
    - 资源滥用检测（CPU/内存/磁盘异常）
    - 通信异常检测（非常规端口/外连可疑IP）
    
    Usage:
        scanner = DeepScanner(audit_log=audit)
        
        # 扫描单个Agent
        result = scanner.scan_agent("coder")
        print(result["risk_score"])  # 0-100
        
        # 扫描所有Agent
        all_results = scanner.scan_all()
        
        # 生成安全评分
        score = scanner.security_score()
    """
    
    # === 检测规则 ===
    
    _RULES = {
        # 异常行为
        "high_frequency_ops": {
            "description": "高频操作（可能自动化攻击）",
            "threshold": 100,  # 每分钟操作数
            "severity": "high",
            "weight": 15,
        },
        "off_hours_activity": {
            "description": "非工作时间活动（0-6点）",
            "threshold": 10,   # 非工作时间操作数
            "severity": "medium",
            "weight": 8,
        },
        "rapid_sequential_ops": {
            "description": "快速连续操作（脚本化行为）",
            "threshold": 50,   # 10秒内操作数
            "severity": "high",
            "weight": 12,
        },
        "unusual_operations": {
            "description": "非常规操作组合",
            "threshold": 5,    # 异常操作类型数
            "severity": "medium",
            "weight": 10,
        },
        
        # 数据泄露
        "large_data_transfer": {
            "description": "大量数据传输",
            "threshold": 10485760,  # 10MB
            "severity": "critical",
            "weight": 20,
        },
        "sensitive_data_access": {
            "description": "访问敏感数据",
            "threshold": 3,    # 敏感文件访问次数
            "severity": "high",
            "weight": 15,
        },
        "data_export_pattern": {
            "description": "数据导出模式",
            "threshold": 5,    # 导出操作次数
            "severity": "high",
            "weight": 12,
        },
        
        # 权限滥用
        "permission_denied_spam": {
            "description": "频繁权限拒绝（探测行为）",
            "threshold": 20,   # 拒绝次数
            "severity": "high",
            "weight": 15,
        },
        "privilege_escalation_attempt": {
            "description": "权限提升尝试",
            "threshold": 1,    # 尝试次数
            "severity": "critical",
            "weight": 25,
        },
        "cross_agent_access": {
            "description": "跨Agent数据访问",
            "threshold": 3,    # 跨Agent访问次数
            "severity": "medium",
            "weight": 10,
        },
        
        # 资源滥用
        "disk_usage_spike": {
            "description": "磁盘使用量突增",
            "threshold": 1073741824,  # 1GB
            "severity": "medium",
            "weight": 8,
        },
        "process_spawn_flood": {
            "description": "进程创建风暴",
            "threshold": 50,   # 每分钟进程数
            "severity": "high",
            "weight": 12,
        },
        
        # 通信异常
        "unusual_ports": {
            "description": "非常规端口通信",
            "threshold": 3,    # 非常规端口数
            "severity": "medium",
            "weight": 8,
        },
        "external_connections": {
            "description": "外部连接（非白名单域名）",
            "threshold": 5,    # 外部连接数
            "severity": "medium",
            "weight": 10,
        },
        "encrypted_tunnel": {
            "description": "加密隧道检测",
            "threshold": 1,
            "severity": "high",
            "weight": 15,
        },
    }
    
    _SENSITIVE_PATHS = [
        ".env", ".ssh", ".aws", ".git-credentials",
        "password", "secret", "credential", "token",
        "shadow", "passwd", "id_rsa", "id_ed25519",
    ]
    
    _UNUSUAL_PORTS = {4444, 5555, 6666, 7777, 8888, 9999, 1337, 31337, 6667, 6668, 6669}
    
    def __init__(self, audit_log=None, csp_manager=None):
        """
        Args:
            audit_log: AuditLog实例（用于获取审计数据）
            csp_manager: CSPManager实例（用于检查网络访问）
        """
        self._audit = audit_log
        self._csp = csp_manager
        self._findings: Dict[str, List[Dict]] = defaultdict(list)
        self._scan_history: List[Dict] = []
        
        # 自定义阈值
        self._custom_thresholds: Dict[str, Any] = {}
    
    def scan_agent(self, agent_id: str) -> Dict:
        """扫描单个Agent的安全状况
        
        Args:
            agent_id: Agent标识
        
        Returns:
            {
                "agent_id": str,
                "risk_score": int (0-100),
                "findings": [finding, ...],
                "recommendations": [str, ...],
                "scan_time": str,
            }
        """
        self._findings[agent_id] = []
        
        # 获取Agent的审计数据
        entries = self._get_agent_entries(agent_id)
        
        # 运行所有检测规则
        self._check_frequency(agent_id, entries)
        self._check_off_hours(agent_id, entries)
        self._check_data_leak(agent_id, entries)
        self._check_permission_abuse(agent_id, entries)
        self._check_resource_abuse(agent_id, entries)
        self._check_communication(agent_id, entries)
        
        # 计算风险评分
        risk_score = self._calculate_risk(agent_id)
        
        # 生成建议
        recommendations = self._generate_recommendations(agent_id)
        
        result = {
            "agent_id": agent_id,
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "findings": self._findings[agent_id],
            "recommendations": recommendations,
            "scan_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "total_findings": len(self._findings[agent_id]),
        }
        
        self._scan_history.append(result)
        return result
    
    def scan_all(self) -> Dict:
        """扫描所有Agent"""
        if self._audit is None:
            return {"error": "需要AuditLog实例"}
        
        # 获取所有Agent ID
        all_entries = self._audit.query(limit=10000)
        agent_ids = set(e.get("agent_id", "") for e in all_entries if e.get("agent_id"))
        
        results = {}
        for agent_id in agent_ids:
            results[agent_id] = self.scan_agent(agent_id)
        
        # 总体评分
        scores = [r["risk_score"] for r in results.values()]
        avg_score = sum(scores) / len(scores) if scores else 0
        
        return {
            "agents_scanned": len(results),
            "average_risk_score": round(avg_score, 1),
            "max_risk_score": max(scores) if scores else 0,
            "high_risk_agents": [
                aid for aid, r in results.items() if r["risk_score"] >= 70
            ],
            "results": results,
            "scan_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
    
    def security_score(self) -> Dict:
        """生成整体安全评分"""
        scan_result = self.scan_all()
        
        avg_risk = scan_result.get("average_risk_score", 0)
        # 风险评分转安全评分（反向）
        security_score = max(0, 100 - avg_risk)
        
        grade = "A+" if security_score >= 95 else \
                "A" if security_score >= 90 else \
                "B+" if security_score >= 85 else \
                "B" if security_score >= 80 else \
                "C" if security_score >= 70 else \
                "D" if security_score >= 60 else "F"
        
        return {
            "security_score": round(security_score, 1),
            "grade": grade,
            "risk_score": round(avg_risk, 1),
            "agents_scanned": scan_result.get("agents_scanned", 0),
            "high_risk_agents": scan_result.get("high_risk_agents", []),
            "total_findings": sum(
                r.get("total_findings", 0)
                for r in scan_result.get("results", {}).values()
            ),
        }
    
    def set_threshold(self, rule_name: str, value: Any) -> None:
        """自定义检测阈值"""
        self._custom_thresholds[rule_name] = value
    
    def get_findings(self, agent_id: str = None, severity: str = None) -> List[Dict]:
        """获取检测结果"""
        results = []
        agents = [agent_id] if agent_id else list(self._findings.keys())
        
        for aid in agents:
            for finding in self._findings.get(aid, []):
                if severity and finding.get("severity") != severity:
                    continue
                results.append({"agent_id": aid, **finding})
        
        return results
    
    def _get_agent_entries(self, agent_id: str) -> List[Dict]:
        """获取Agent的审计条目"""
        if self._audit is None:
            return []
        return self._audit.query(agent_id=agent_id, limit=10000)
    
    def _check_frequency(self, agent_id: str, entries: List[Dict]) -> None:
        """检测操作频率异常"""
        threshold = self._custom_thresholds.get("high_frequency_ops", self._RULES["high_frequency_ops"]["threshold"])
        
        if len(entries) > threshold:
            self._add_finding(agent_id, "high_frequency_ops", {
                "count": len(entries),
                "threshold": threshold,
            })
    
    def _check_off_hours(self, agent_id: str, entries: List[Dict]) -> None:
        """检测非工作时间活动"""
        threshold = self._custom_thresholds.get("off_hours_activity", self._RULES["off_hours_activity"]["threshold"])
        
        off_hours_count = 0
        for entry in entries:
            ts = entry.get("timestamp", "")
            try:
                hour = int(ts[11:13]) if len(ts) >= 13 else 12
                if 0 <= hour < 6:
                    off_hours_count += 1
            except (ValueError, IndexError):
                pass
        
        if off_hours_count >= threshold:
            self._add_finding(agent_id, "off_hours_activity", {
                "off_hours_count": off_hours_count,
                "threshold": threshold,
            })
    
    def _check_data_leak(self, agent_id: str, entries: List[Dict]) -> None:
        """检测数据泄露风险"""
        # 敏感数据访问
        sensitive_threshold = self._custom_thresholds.get(
            "sensitive_data_access", self._RULES["sensitive_data_access"]["threshold"]
        )
        sensitive_count = 0
        
        for entry in entries:
            target = str(entry.get("target", "")).lower()
            params = str(entry.get("params", "")).lower()
            
            for sp in self._SENSITIVE_PATHS:
                if sp in target or sp in params:
                    sensitive_count += 1
                    break
        
        if sensitive_count >= sensitive_threshold:
            self._add_finding(agent_id, "sensitive_data_access", {
                "count": sensitive_count,
                "threshold": sensitive_threshold,
            })
        
        # 数据导出模式
        export_ops = {"export", "download", "transfer", "send", "upload", "copy"}
        export_count = sum(1 for e in entries if e.get("action", "") in export_ops)
        
        export_threshold = self._custom_thresholds.get(
            "data_export_pattern", self._RULES["data_export_pattern"]["threshold"]
        )
        if export_count >= export_threshold:
            self._add_finding(agent_id, "data_export_pattern", {
                "count": export_count,
                "threshold": export_threshold,
            })
    
    def _check_permission_abuse(self, agent_id: str, entries: List[Dict]) -> None:
        """检测权限滥用"""
        # 权限拒绝
        denied_threshold = self._custom_thresholds.get(
            "permission_denied_spam", self._RULES["permission_denied_spam"]["threshold"]
        )
        denied_count = sum(1 for e in entries if e.get("risk_level") == "red" or "denied" in str(e.get("result", "")).lower())
        
        if denied_count >= denied_threshold:
            self._add_finding(agent_id, "permission_denied_spam", {
                "count": denied_count,
                "threshold": denied_threshold,
            })
        
        # 权限提升尝试
        escalation_keywords = {"sudo", "su ", "chmod 777", "useradd", "userdel", "passwd", "crontab"}
        for entry in entries:
            action = str(entry.get("action", "")).lower()
            params = str(entry.get("params", "")).lower()
            combined = action + " " + params
            
            for kw in escalation_keywords:
                if kw in combined:
                    self._add_finding(agent_id, "privilege_escalation_attempt", {
                        "action": entry.get("action", ""),
                        "target": entry.get("target", ""),
                    })
                    break
    
    def _check_resource_abuse(self, agent_id: str, entries: List[Dict]) -> None:
        """检测资源滥用"""
        # 进程创建风暴
        process_threshold = self._custom_thresholds.get(
            "process_spawn_flood", self._RULES["process_spawn_flood"]["threshold"]
        )
        process_ops = {"execute_command", "run_script", "run_shell", "execute"}
        process_count = sum(1 for e in entries if e.get("action", "") in process_ops)
        
        if process_count >= process_threshold:
            self._add_finding(agent_id, "process_spawn_flood", {
                "count": process_count,
                "threshold": process_threshold,
            })
    
    def _check_communication(self, agent_id: str, entries: List[Dict]) -> None:
        """检测通信异常"""
        if self._csp is None:
            return
        
        # 外部连接检测
        external_threshold = self._custom_thresholds.get(
            "external_connections", self._RULES["external_connections"]["threshold"]
        )
        external_count = 0
        
        for entry in entries:
            target = entry.get("target", "")
            if isinstance(target, str) and ("http" in target or "://" in target):
                if not self._csp.check_url(target, agent_id):
                    external_count += 1
        
        if external_count >= external_threshold:
            self._add_finding(agent_id, "external_connections", {
                "count": external_count,
                "threshold": external_threshold,
            })
    
    def _add_finding(self, agent_id: str, rule_name: str, details: Dict) -> None:
        """添加检测结果"""
        rule = self._RULES.get(rule_name, {})
        
        self._findings[agent_id].append({
            "rule": rule_name,
            "description": rule.get("description", rule_name),
            "severity": rule.get("severity", "info"),
            "weight": rule.get("weight", 5),
            "details": details,
            "detected_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        })
    
    def _calculate_risk(self, agent_id: str) -> int:
        """计算风险评分（0-100）"""
        findings = self._findings.get(agent_id, [])
        if not findings:
            return 0
        
        total_weight = sum(f.get("weight", 5) for f in findings)
        # 归一化到0-100
        score = min(100, total_weight)
        return score
    
    def _risk_level(self, score: int) -> str:
        """风险等级"""
        if score >= 80:
            return "critical"
        elif score >= 60:
            return "high"
        elif score >= 40:
            return "medium"
        elif score >= 20:
            return "low"
        return "info"
    
    def _generate_recommendations(self, agent_id: str) -> List[str]:
        """生成安全建议"""
        findings = self._findings.get(agent_id, [])
        recommendations = []
        
        rule_suggestions = {
            "high_frequency_ops": "降低操作频率，检查是否存在自动化攻击或异常行为",
            "off_hours_activity": "确认非工作时间活动是否为授权行为",
            "sensitive_data_access": "审查敏感数据访问权限，实施最小权限原则",
            "data_export_pattern": "检查数据导出行为，防止数据泄露",
            "permission_denied_spam": "检查Agent权限配置，确认是否存在权限探测",
            "privilege_escalation_attempt": "立即调查权限提升尝试，可能表示入侵",
            "process_spawn_flood": "限制进程创建频率，防止资源耗尽攻击",
            "external_connections": "审查外部连接目标，确认是否在白名单内",
        }
        
        seen_rules = set()
        for finding in findings:
            rule = finding.get("rule", "")
            if rule in rule_suggestions and rule not in seen_rules:
                recommendations.append(rule_suggestions[rule])
                seen_rules.add(rule)
        
        if not recommendations:
            recommendations.append("当前未发现安全风险，继续保持监控")
        
        return recommendations
    
    def export_report(self, agent_id: str = None) -> str:
        """导出扫描报告"""
        if agent_id:
            result = self.scan_agent(agent_id)
        else:
            result = self.security_score()
        
        return json.dumps(result, ensure_ascii=False, indent=2)
