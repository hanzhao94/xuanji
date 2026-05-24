"""
L1 沙箱层 — 文件系统隔离 + 进程安全

与C底座(oa_fs.c/oa_proc.c)形成双重防护。
Python层是第二道防线，C层是第一道。
"""

import os
import re
from typing import Dict, List, Optional


class FileSystemSandbox:
    """文件系统沙箱 — 控制Agent的文件访问权限
    
    设计原则：默认全拒绝，需要什么开什么。
    
    Usage:
        sandbox = FileSystemSandbox()
        sandbox.configure_agent("coder",
            allow_read=["D:\\projects\\"],
            allow_write=["D:\\projects\\my-app\\"],
            deny=["D:\\projects\\my-app\\.env"]
        )
        
        sandbox.check("coder", "D:\\projects\\readme.md", "read")   # True
        sandbox.check("coder", "C:\\Windows\\system32", "read")      # False (硬编码禁止区)
        sandbox.check("coder", "~/.ssh/id_rsa", "read")             # False (硬编码禁止区)
    """
    
    # === 硬编码禁止区 — 任何Agent都不能碰 ===
    
    _FORBIDDEN_PATHS = [
        # Unix 敏感路径
        "/etc/passwd", "/etc/shadow", "/etc/sudoers",
        # SSH
        ".ssh/", ".ssh\\",
        # 环境变量文件
        ".env",
        # Git凭证
        ".git-credentials", ".gitconfig",
        # Shell历史
        ".bash_history", ".zsh_history",
        # AWS/云凭证
        ".aws/", ".aws\\",
        # Docker
        ".docker/", ".docker\\",
        # npm token
        ".npmrc",
    ]
    
    _FORBIDDEN_DIRS_UNIX = [
        "/etc/", "/boot/", "/sbin/", "/usr/sbin/",
        "/proc/", "/sys/", "/dev/",
    ]
    
    _FORBIDDEN_DIRS_WIN = [
        "C:\\Windows\\", "C:\\WINDOWS\\",
        "C:\\Program Files\\", "C:\\Program Files (x86)\\",
        "C:\\ProgramData\\",
    ]
    
    _SENSITIVE_PATTERNS = [
        re.compile(r"[/\\]\.env(\.|$)", re.IGNORECASE),
        re.compile(r"password", re.IGNORECASE),
        re.compile(r"secret", re.IGNORECASE),
        re.compile(r"credential", re.IGNORECASE),
        re.compile(r"[/\\]\.ssh[/\\]", re.IGNORECASE),
        re.compile(r"private[_\-]?key", re.IGNORECASE),
        re.compile(r"id_rsa", re.IGNORECASE),
        re.compile(r"id_ed25519", re.IGNORECASE),
    ]
    
    def __init__(self):
        self._agents: Dict[str, Dict] = {}
    
    def configure_agent(
        self,
        agent_id: str,
        allow_read: List[str] = None,
        allow_write: List[str] = None,
        deny: List[str] = None,
    ) -> None:
        """配置Agent的文件访问权限
        
        Args:
            agent_id: Agent标识
            allow_read: 允许读取的路径前缀列表
            allow_write: 允许写入的路径前缀列表
            deny: 额外拒绝的路径列表
        """
        self._agents[agent_id] = {
            "allow_read": [self._normalize(p) for p in (allow_read or [])],
            "allow_write": [self._normalize(p) for p in (allow_write or [])],
            "deny": [self._normalize(p) for p in (deny or [])],
        }
    
    def check(self, agent_id: str, path: str, op: str = "read") -> bool:
        """检查文件操作是否允许
        
        Args:
            agent_id: Agent标识
            path: 目标文件/目录路径
            op: 操作类型 ("read" / "write" / "delete")
        
        Returns:
            True=允许, False=拒绝
        """
        norm = self._normalize(path)
        
        # 第1层：硬编码禁止区 — 绝对不允许
        if self._is_forbidden(norm):
            return False
        
        # 第2层：敏感文件模式匹配
        if self._is_sensitive(norm):
            return False
        
        # 第3层：用户配置的deny列表
        config = self._agents.get(agent_id)
        if config is None:
            # 未配置的Agent默认全拒绝
            return False
        
        for deny_path in config["deny"]:
            if norm.startswith(deny_path) or norm == deny_path:
                return False
        
        # 第4层：用户配置的allow列表
        if op in ("write", "delete"):
            for allow_path in config["allow_write"]:
                if norm.startswith(allow_path):
                    return True
            return False
        
        # read操作：allow_read 或 allow_write 中的路径都可读
        for allow_path in config["allow_read"] + config["allow_write"]:
            if norm.startswith(allow_path):
                return True
        
        # 默认拒绝
        return False
    
    def _is_forbidden(self, path: str) -> bool:
        """检查是否命中硬编码禁止区"""
        lower = path.lower()
        
        # 检查禁止路径
        for forbidden in self._FORBIDDEN_PATHS:
            if forbidden.lower() in lower:
                return True
        
        # 检查禁止目录（Unix）
        for d in self._FORBIDDEN_DIRS_UNIX:
            if lower.startswith(d.lower()):
                return True
        
        # 检查禁止目录（Windows）
        for d in self._FORBIDDEN_DIRS_WIN:
            if lower.startswith(d.lower()):
                return True
        
        return False
    
    def _is_sensitive(self, path: str) -> bool:
        """检查是否命中敏感文件模式"""
        for pattern in self._SENSITIVE_PATTERNS:
            if pattern.search(path):
                return True
        return False
    
    @staticmethod
    def _normalize(path: str) -> str:
        """路径标准化 — 展开~，转绝对路径"""
        expanded = os.path.expanduser(path)
        try:
            return os.path.abspath(expanded)
        except (OSError, ValueError):
            return expanded


class ProcessSandbox:
    """进程沙箱 — 命令安全检查
    
    硬编码黑名单 + 可配置扩展。
    
    Usage:
        sandbox = ProcessSandbox()
        sandbox.is_safe_command("ls -la")          # True
        sandbox.is_safe_command("rm -rf /")         # False
        sandbox.is_safe_command("curl x | sh")      # False
        
        # 自定义黑名单
        sandbox.add_deny_pattern("docker rm")
    """
    
    # === 硬编码黑名单 — 任何Agent都不能执行 ===
    
    _BLACKLIST_EXACT = [
        "shutdown", "reboot", "halt", "poweroff",
        "init 0", "init 6",
    ]
    
    _BLACKLIST_PATTERNS = [
        # 删除类
        re.compile(r"\brm\s+(-[a-zA-Z]*)?-rf\b", re.IGNORECASE),
        re.compile(r"\brm\s+(-[a-zA-Z]*)?-fr\b", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\s+/\s*$", re.IGNORECASE),
        re.compile(r"\bdel\s+/[sS]\b", re.IGNORECASE),
        re.compile(r"\bdel\s+/[qQ]\s+/[sS]\b", re.IGNORECASE),
        re.compile(r"\bformat\s+[a-zA-Z]:", re.IGNORECASE),
        re.compile(r"\bmkfs\b", re.IGNORECASE),
        re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE),
        
        # 系统管理类
        re.compile(r"\bnet\s+user\b", re.IGNORECASE),
        re.compile(r"\buseradd\b", re.IGNORECASE),
        re.compile(r"\buserdel\b", re.IGNORECASE),
        re.compile(r"\bpasswd\b", re.IGNORECASE),
        re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
        re.compile(r"\bchown\s+root\b", re.IGNORECASE),
        re.compile(r"\bsudo\s+su\b", re.IGNORECASE),
        
        # 远程执行类（管道执行）
        re.compile(r"\bcurl\b.*\|\s*(sh|bash|zsh|python)", re.IGNORECASE),
        re.compile(r"\bwget\b.*\|\s*(sh|bash|zsh|python)", re.IGNORECASE),
        re.compile(r"\bcurl\b.*\|\s*sudo\b", re.IGNORECASE),
        
        # 编码绕过
        re.compile(r"\bpowershell\b.*-[eE]nc", re.IGNORECASE),
        re.compile(r"\bpowershell\b.*-[eE]ncodedCommand", re.IGNORECASE),
        re.compile(r"\bpython\s+-c\s+['\"].*exec\(", re.IGNORECASE),
        re.compile(r"\beval\s*\(", re.IGNORECASE),
        
        # 注册表
        re.compile(r"\breg\s+(add|delete)\b", re.IGNORECASE),
        
        # 防火墙
        re.compile(r"\biptables\s+-F\b", re.IGNORECASE),
        re.compile(r"\bnetsh\s+advfirewall\s+set\b", re.IGNORECASE),
    ]
    
    def __init__(self):
        self._extra_deny: List[re.Pattern] = []
    
    def is_safe_command(self, cmd: str) -> bool:
        """检查命令是否安全
        
        Args:
            cmd: 要执行的命令字符串
        
        Returns:
            True=安全, False=危险（拦截）
        """
        stripped = cmd.strip()
        
        # 精确匹配
        for blacklisted in self._BLACKLIST_EXACT:
            if stripped.lower() == blacklisted.lower():
                return False
        
        # 模式匹配
        for pattern in self._BLACKLIST_PATTERNS:
            if pattern.search(stripped):
                return False
        
        # 用户自定义黑名单
        for pattern in self._extra_deny:
            if pattern.search(stripped):
                return False
        
        return True
    
    def add_deny_pattern(self, pattern: str) -> None:
        """添加自定义拒绝模式
        
        Args:
            pattern: 正则表达式字符串
        """
        self._extra_deny.append(re.compile(pattern, re.IGNORECASE))
    
    def get_violation_reason(self, cmd: str) -> Optional[str]:
        """返回命令被拒绝的原因（调试用）
        
        Args:
            cmd: 命令字符串
        
        Returns:
            拒绝原因，如果安全则返回 None
        """
        stripped = cmd.strip()
        
        for blacklisted in self._BLACKLIST_EXACT:
            if stripped.lower() == blacklisted.lower():
                return f"精确匹配黑名单: {blacklisted}"
        
        for pattern in self._BLACKLIST_PATTERNS:
            if pattern.search(stripped):
                return f"模式匹配黑名单: {pattern.pattern}"
        
        for pattern in self._extra_deny:
            if pattern.search(stripped):
                return f"用户自定义黑名单: {pattern.pattern}"
        
        return None
