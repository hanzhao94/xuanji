"""
xuanji 安全系统

七层防护架构的Python实现层。
与C底座(oa_fs.c/oa_proc.c)形成双重防护。

导出:
    SecurityEngine — 安全引擎主入口，组合所有安全组件
"""

from .sandbox import FileSystemSandbox, ProcessSandbox
from .guard import OperationGuard, OperationLevel
from .sanitizer import InputSanitizer, SanitizedInput
from .audit import AuditLog
from .secrets import SecretStore

# 深度安全模块
from .red_team import RedTeam
from .csp import CSPManager
from .privacy import PrivacyMask
from .audit_standard import AuditStandard
from .deep_security import DeepScanner
from .threat_model import ThreatModel
from .secure_config import SecureConfig

import os


class SecurityEngine:
    """安全引擎 — 组合所有安全组件的统一入口
    
    Usage:
        engine = SecurityEngine(data_dir="~/.xuanji")
        
        # 检查文件操作
        if engine.fs.check("coder", "/etc/passwd", "read"):
            ...
        
        # 检查命令安全
        if engine.proc.is_safe_command("rm -rf /"):
            ...  # False
        
        # 操作分级
        level = engine.guard.classify("delete_file", {"path": "x"})
        
        # 输入消毒
        result = engine.sanitizer.sanitize(user_input, "web")
        
        # 审计日志
        engine.audit.log("coder", "read_file", "a.txt", {}, "ok", "green")
        
        # 密钥管理
        engine.secrets.store("api_key", "sk-xxx")
        
        # 红队测试
        engine.redteam.run_test("command_injection")
        
        # 内容安全策略
        engine.csp.check_url("https://github.com/repo")
        
        # 隐私遮蔽
        engine.privacy.mask("手机号: 13812345678")
        
        # 审计标准化
        engine.audit_std.event("auth", "login", "coder")
        
        # 深度扫描
        engine.scanner.scan_agent("coder")
        
        # 威胁建模
        engine.threat.analyze()
        
        # 安全配置
        engine.sec_config.load_template("strict")
    """
    
    def __init__(self, data_dir: str = "~/.xuanji"):
        self.fs = FileSystemSandbox()
        self.proc = ProcessSandbox()
        self.guard = OperationGuard()
        self.sanitizer = InputSanitizer()
        self.audit = AuditLog(data_dir=data_dir)
        self.secrets = SecretStore(data_dir=data_dir)
        
        # 深度安全模块
        self.redteam = RedTeam(security_engine=self)
        self.csp = CSPManager(config_path=os.path.join(data_dir, "csp_rules.json"))
        self.privacy = PrivacyMask()
        self.audit_std = AuditStandard(output_dir=os.path.join(data_dir, "audit_std"))
        self.scanner = DeepScanner(audit_log=self.audit, csp_manager=self.csp)
        self.threat = ThreatModel()
        self.sec_config = SecureConfig(
            config_path=os.path.join(data_dir, "secure_config.json"),
        )
    
    def configure_agent(
        self,
        agent_id: str,
        allow_read: list = None,
        allow_write: list = None,
        deny: list = None,
    ) -> None:
        """为指定Agent配置文件系统权限"""
        self.fs.configure_agent(
            agent_id,
            allow_read=allow_read or [],
            allow_write=allow_write or [],
            deny=deny or [],
        )


__all__ = [
    "SecurityEngine",
    "FileSystemSandbox",
    "ProcessSandbox",
    "OperationGuard",
    "OperationLevel",
    "InputSanitizer",
    "SanitizedInput",
    "AuditLog",
    "SecretStore",
    # 深度安全模块
    "RedTeam",
    "CSPManager",
    "PrivacyMask",
    "AuditStandard",
    "DeepScanner",
    "ThreatModel",
    "SecureConfig",
]
