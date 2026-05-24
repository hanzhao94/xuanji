"""
L7+ 审计标准化层 — 输出CEF/Syslog格式审计日志

与现有security/audit.py集成，支持输出到文件/网络/SIEM。
"""

import json
import os
import socket
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class AuditStandard:
    """审计日志标准化 — 输出CEF/Syslog格式
    
    功能：
    - CEF格式输出（ArcSight/QRadar兼容）
    - Syslog格式输出（RFC 5424）
    - 支持输出到文件/网络/SIEM
    - 事件分类：认证/授权/操作/安全/系统
    - 与现有AuditLog集成
    
    Usage:
        standard = AuditStandard(output_dir="~/.xuanji/audit_std")
        
        # 记录事件
        event = standard.event(
            category="auth",
            action="login",
            agent_id="coder",
            result="success",
            details={"ip": "192.168.1.100"}
        )
        
        # 输出CEF
        cef = standard.to_cef(event)
        
        # 输出Syslog
        syslog = standard.to_syslog(event)
        
        # 发送到SIEM
        standard.send_to_siem(event, "siem.example.com", 514)
    """
    
    # === 事件分类 ===
    
    CATEGORIES = {
        "auth": "认证",
        "authz": "授权",
        "operation": "操作",
        "security": "安全",
        "system": "系统",
    }
    
    # CEF严重级别映射
    SEVERITY_MAP = {
        "info": 0,
        "low": 3,
        "medium": 5,
        "high": 7,
        "critical": 9,
        "emergency": 10,
    }
    
    # Syslog严重级别映射（RFC 5424）
    SYSLOG_SEVERITY = {
        "info": 6,      # Informational
        "low": 6,
        "medium": 4,    # Warning
        "high": 3,      # Error
        "critical": 2,  # Critical
        "emergency": 0, # Emergency
    }
    
    SYSLOG_FACILITY = 1  # user-level
    
    def __init__(
        self,
        output_dir: str = "~/.xuanji/audit_std",
        product_name: str = "xuanji",
        product_version: str = "1.0",
    ):
        self._output_dir = os.path.expanduser(output_dir)
        self._product_name = product_name
        self._product_version = product_version
        self._events: List[Dict] = []
        self._siem_config: Optional[Dict] = None
        
        os.makedirs(self._output_dir, exist_ok=True)
    
    def event(
        self,
        category: str,
        action: str,
        agent_id: str = "",
        result: str = "",
        severity: str = "info",
        details: Optional[Dict] = None,
        resource: str = "",
    ) -> Dict:
        """创建标准化事件
        
        Args:
            category: 事件分类 (auth/authz/operation/security/system)
            action: 操作名称
            agent_id: Agent标识
            result: 操作结果
            severity: 严重级别 (info/low/medium/high/critical/emergency)
            details: 额外详情
            resource: 资源路径/URL
        
        Returns:
            标准化事件字典
        """
        now = datetime.now(timezone(timedelta(hours=8)))
        
        event = {
            "event_id": f"OA-{int(time.time() * 1000)}-{id(event) & 0xFFFF:04x}",
            "timestamp": now.isoformat(),
            "epoch": now.timestamp(),
            "category": category,
            "category_name": self.CATEGORIES.get(category, category),
            "action": action,
            "agent_id": agent_id,
            "result": result,
            "severity": severity,
            "resource": resource,
            "details": details or {},
            "product": self._product_name,
            "version": self._product_version,
        }
        
        self._events.append(event)
        self._write_to_file(event)
        
        return event
    
    def to_cef(self, event: Dict) -> str:
        """转换为CEF格式字符串
        
        CEF = Common Event Format (ArcSight标准)
        
        Format:
        CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension
        
        Args:
            event: 事件字典
        
        Returns:
            CEF格式字符串
        """
        vendor = self._product_name
        product = self._product_name
        version = self._product_version
        sig_id = event.get("category", "unknown")
        name = event.get("action", "unknown")
        severity = self.SEVERITY_MAP.get(event.get("severity", "info"), 0)
        
        # 构建Extension字段（key=value对）
        extensions = self._build_cef_extensions(event)
        
        return f"CEF:0|{vendor}|{product}|{version}|{sig_id}|{name}|{severity}|{extensions}"
    
    def to_syslog(self, event: Dict) -> str:
        """转换为Syslog格式字符串（RFC 5424）
        
        Format:
        <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID MSG
        
        Args:
            event: 事件字典
        
        Returns:
            Syslog格式字符串
        """
        priority = (self.SYSLOG_FACILITY * 8) + self.SYSLOG_SEVERITY.get(
            event.get("severity", "info"), 6
        )
        
        timestamp = event.get("timestamp", datetime.now(timezone(timedelta(hours=8))).isoformat())
        hostname = socket.gethostname()
        app_name = self._product_name
        proc_id = "-"
        msg_id = event.get("event_id", "-")
        
        # 消息体
        msg = self._build_syslog_msg(event)
        
        return f"<{priority}>{1} {timestamp} {hostname} {app_name} {proc_id} {msg_id} {msg}"
    
    def send_to_siem(self, event: Dict, host: str, port: int, protocol: str = "udp") -> bool:
        """发送事件到SIEM系统
        
        Args:
            event: 事件字典
            host: SIEM主机地址
            port: SIEM端口
            protocol: 协议 (udp/tcp)
        
        Returns:
            发送是否成功
        """
        syslog_msg = self.to_syslog(event)
        
        try:
            if protocol == "udp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(syslog_msg.encode("utf-8"), (host, port))
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((host, port))
                sock.sendall(syslog_msg.encode("utf-8"))
            sock.close()
            
            self._siem_config = {"host": host, "port": port, "protocol": protocol}
            return True
        except (socket.error, OSError):
            return False
    
    def export_cef(self, events: List[Dict] = None) -> str:
        """批量导出CEF格式"""
        events = events or self._events
        return "\n".join(self.to_cef(e) for e in events)
    
    def export_syslog(self, events: List[Dict] = None) -> str:
        """批量导出Syslog格式"""
        events = events or self._events
        return "\n".join(self.to_syslog(e) for e in events)
    
    def export_json(self, events: List[Dict] = None) -> str:
        """批量导出JSON格式"""
        events = events or self._events
        return json.dumps(events, ensure_ascii=False, indent=2)
    
    def query(
        self,
        category: str = None,
        agent_id: str = None,
        severity: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """查询事件"""
        results = self._events
        
        if category:
            results = [e for e in results if e.get("category") == category]
        if agent_id:
            results = [e for e in results if e.get("agent_id") == agent_id]
        if severity:
            results = [e for e in results if e.get("severity") == severity]
        
        return results[-limit:]
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        category_counts = {}
        severity_counts = {}
        
        for event in self._events:
            cat = event.get("category", "unknown")
            sev = event.get("severity", "info")
            category_counts[cat] = category_counts.get(cat, 0) + 1
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        
        return {
            "total_events": len(self._events),
            "by_category": category_counts,
            "by_severity": severity_counts,
            "siem_config": self._siem_config,
        }
    
    def integrate_with_audit(self, audit_log) -> None:
        """与现有AuditLog集成
        
        将AuditLog的日志条目转换为标准化格式。
        
        Args:
            audit_log: AuditLog实例
        """
        try:
            entries = audit_log.query(limit=1000)
            for entry in entries:
                # 映射AuditLog字段到标准事件
                action = entry.get("action", "unknown")
                risk = entry.get("risk_level", "green")
                
                severity_map = {"green": "info", "yellow": "medium", "red": "critical"}
                severity = severity_map.get(risk, "info")
                
                # 根据action推断category
                category = self._infer_category(action)
                
                self.event(
                    category=category,
                    action=action,
                    agent_id=entry.get("agent_id", ""),
                    result=entry.get("result", ""),
                    severity=severity,
                    details={"target": entry.get("target", ""), "params": entry.get("params", "")},
                    timestamp=entry.get("timestamp", ""),
                )
        except Exception:
            pass
    
    def _build_cef_extensions(self, event: Dict) -> str:
        """构建CEF Extension字段"""
        parts = []
        
        # 标准CEF字段
        if event.get("agent_id"):
            parts.append(f"rt={event['timestamp']}")
            parts.append(f"src={event.get('details', {}).get('ip', '')}")
            parts.append(f"dst={event.get('resource', '')}")
            parts.append(f"act={event['action']}")
            parts.append(f"outcome={event.get('result', '')}")
            parts.append(f"sourceUserName={event.get('agent_id', '')}")
            parts.append(f"deviceCustomString1={event.get('category_name', '')}")
            
            # 额外详情
            details = event.get("details", {})
            if isinstance(details, dict):
                for i, (k, v) in enumerate(details.items()):
                    if i >= 4:  # 最多4个自定义字段
                        break
                    parts.append(f"deviceCustomString{i+2}={k}={v}")
        
        return " ".join(parts)
    
    def _build_syslog_msg(self, event: Dict) -> str:
        """构建Syslog消息体"""
        msg_data = [
            f"eventID={event.get('event_id', '')}",
            f"category={event.get('category', '')}",
            f"action={event.get('action', '')}",
            f"agent={event.get('agent_id', '')}",
            f"result={event.get('result', '')}",
            f"severity={event.get('severity', '')}",
        ]
        
        details = event.get("details", {})
        if isinstance(details, dict):
            for k, v in details.items():
                msg_data.append(f"{k}={v}")
        
        return "{" + " ".join(msg_data) + "}"
    
    def _infer_category(self, action: str) -> str:
        """根据action推断category"""
        auth_ops = {"login", "logout", "authenticate", "authorize", "token_refresh"}
        authz_ops = {"permission_check", "role_assign", "access_grant", "access_deny"}
        security_ops = {"violation", "intrusion", "anomaly", "alert", "block"}
        system_ops = {"start", "stop", "restart", "config_change", "update"}
        
        if action in auth_ops:
            return "auth"
        if action in authz_ops:
            return "authz"
        if action in security_ops:
            return "security"
        if action in system_ops:
            return "system"
        return "operation"
    
    def _write_to_file(self, event: Dict) -> None:
        """写入当天的审计文件"""
        now = datetime.now(timezone(timedelta(hours=8)))
        date_str = now.strftime("%Y-%m-%d")
        
        # JSONL格式
        jsonl_path = os.path.join(self._output_dir, f"audit_std_{date_str}.jsonl")
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        
        # CEF格式
        cef_path = os.path.join(self._output_dir, f"audit_std_{date_str}.cef")
        with open(cef_path, "a", encoding="utf-8") as f:
            f.write(self.to_cef(event) + "\n")
        
        # Syslog格式
        syslog_path = os.path.join(self._output_dir, f"audit_std_{date_str}.syslog")
        with open(syslog_path, "a", encoding="utf-8") as f:
            f.write(self.to_syslog(event) + "\n")
