"""
L7 审计层 — 所有操作可追溯

Append-only JSONL日志，自动遮盖密钥。
"""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class AuditLog:
    """审计日志 — 所有操作留痕
    
    特性：
    - Append-only JSONL格式（每行一条JSON）
    - 自动遮盖密钥/敏感信息
    - 支持按Agent/级别/时间查询
    - 支持导出CSV/JSON
    
    Usage:
        audit = AuditLog(data_dir="~/.xuanji")
        
        # 记录操作
        audit.log("coder", "read_file", "readme.md", {}, "ok", "green")
        audit.log("coder", "delete_file", "old.db", {"size": 1024}, "confirmed", "red")
        
        # 查询日志
        entries = audit.query(agent_id="coder", level="red")
        
        # 导出
        csv_text = audit.export("csv")
    """
    
    # 密钥模式 — 自动遮盖
    _SECRET_PATTERNS = [
        re.compile(r'(sk-[a-zA-Z0-9]{20,})', re.IGNORECASE),           # OpenAI key
        re.compile(r'(ghp_[a-zA-Z0-9]{36,})', re.IGNORECASE),          # GitHub token
        re.compile(r'(gho_[a-zA-Z0-9]{36,})', re.IGNORECASE),          # GitHub OAuth
        re.compile(r'(AKIA[0-9A-Z]{16})', re.IGNORECASE),              # AWS Access Key
        re.compile(r'(eyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,})'),   # JWT
        re.compile(r'([a-f0-9]{40})', re.IGNORECASE),                   # SHA1 hash (可能是token)
        re.compile(r'(Bearer\s+[a-zA-Z0-9_\-\.]+)', re.IGNORECASE),    # Bearer token
        re.compile(r'(api[_-]?key\s*[=:]\s*\S+)', re.IGNORECASE),      # api_key=xxx
        re.compile(r'(password\s*[=:]\s*\S+)', re.IGNORECASE),          # password=xxx
        re.compile(r'(secret\s*[=:]\s*\S+)', re.IGNORECASE),            # secret=xxx
        re.compile(r'(token\s*[=:]\s*\S+)', re.IGNORECASE),             # token=xxx
    ]
    
    def __init__(self, data_dir: str = "~/.xuanji"):
        self._data_dir = os.path.expanduser(data_dir)
        self._log_dir = os.path.join(self._data_dir, "audit")
        os.makedirs(self._log_dir, exist_ok=True)
    
    def log(
        self,
        agent_id: str,
        action: str,
        target: str,
        params: Optional[Dict[str, Any]] = None,
        result: str = "",
        risk_level: str = "green",
    ) -> Dict:
        """记录一条审计日志
        
        Args:
            agent_id: Agent标识
            action: 操作名称
            target: 操作目标
            params: 操作参数（自动遮盖密钥）
            result: 操作结果
            risk_level: 风险级别 (green/yellow/red)
        
        Returns:
            写入的日志条目
        """
        entry = {
            "timestamp": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "epoch": time.time(),
            "agent_id": agent_id,
            "action": action,
            "target": self._mask_secrets(str(target)),
            "params": self._mask_secrets(json.dumps(params or {}, ensure_ascii=False)),
            "result": self._mask_secrets(str(result)),
            "risk_level": risk_level,
        }
        
        # 写入当天的日志文件
        log_file = self._get_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        
        return entry
    
    def query(
        self,
        agent_id: Optional[str] = None,
        level: Optional[str] = None,
        time_range: Optional[tuple] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """查询审计日志
        
        Args:
            agent_id: 按Agent过滤
            level: 按级别过滤 (green/yellow/red)
            time_range: 时间范围 (start_epoch, end_epoch)
            limit: 最大返回条数
        
        Returns:
            匹配的日志条目列表（最新的在前）
        """
        results = []
        
        # 遍历所有日志文件（倒序）
        log_files = sorted(self._list_log_files(), reverse=True)
        
        for log_file in log_files:
            if len(results) >= limit:
                break
            
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except (OSError, IOError):
                continue
            
            for line in reversed(lines):
                if len(results) >= limit:
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # 过滤
                if agent_id and entry.get("agent_id") != agent_id:
                    continue
                if level and entry.get("risk_level") != level:
                    continue
                if time_range:
                    epoch = entry.get("epoch", 0)
                    if epoch < time_range[0] or epoch > time_range[1]:
                        continue
                
                results.append(entry)
        
        return results
    
    def export(self, fmt: str = "json") -> str:
        """导出所有审计日志
        
        Args:
            fmt: 格式 ("json" 或 "csv")
        
        Returns:
            格式化的日志文本
        """
        all_entries = self.query(limit=10000)
        # 按时间正序
        all_entries.reverse()
        
        if fmt == "csv":
            return self._to_csv(all_entries)
        else:
            return json.dumps(all_entries, ensure_ascii=False, indent=2)
    
    def _to_csv(self, entries: List[Dict]) -> str:
        """转CSV格式"""
        if not entries:
            return "timestamp,agent_id,action,target,params,result,risk_level\n"
        
        headers = ["timestamp", "agent_id", "action", "target", "params", "result", "risk_level"]
        lines = [",".join(headers)]
        
        for entry in entries:
            row = []
            for h in headers:
                val = str(entry.get(h, ""))
                # CSV转义：包含逗号/引号/换行的字段用引号包裹
                if "," in val or '"' in val or "\n" in val:
                    val = '"' + val.replace('"', '""') + '"'
                row.append(val)
            lines.append(",".join(row))
        
        return "\n".join(lines)
    
    def _mask_secrets(self, text: str) -> str:
        """自动遮盖文本中的密钥"""
        masked = text
        for pattern in self._SECRET_PATTERNS:
            masked = pattern.sub("[***]", masked)
        return masked
    
    def _get_log_file(self) -> str:
        """获取当天的日志文件路径"""
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, f"audit-{today}.jsonl")
    
    def _list_log_files(self) -> List[str]:
        """列出所有日志文件"""
        if not os.path.isdir(self._log_dir):
            return []
        
        files = []
        for name in os.listdir(self._log_dir):
            if name.startswith("audit-") and name.endswith(".jsonl"):
                files.append(os.path.join(self._log_dir, name))
        
        return sorted(files)
