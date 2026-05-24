"""
L2 操作安全 — 操作分级与确认机制

三级分类：
  GREEN  — 自动放行（读文件/搜索/计算）
  YELLOW — 记日志（写文件/执行命令/发消息）
  RED    — 需用户确认（删除/安装/发送敏感信息/修改系统）
"""

import enum
from typing import Any, Dict, Optional


class OperationLevel(enum.Enum):
    """操作安全等级"""
    GREEN = "green"    # 自动放行
    YELLOW = "yellow"  # 记日志
    RED = "red"        # 需确认


class OperationGuard:
    """操作安全守卫 — 对操作进行分级
    
    Usage:
        guard = OperationGuard()
        
        level = guard.classify("read_file", {"path": "readme.md"})
        # → OperationLevel.GREEN
        
        level = guard.classify("write_file", {"path": "output.txt"})
        # → OperationLevel.YELLOW
        
        level = guard.classify("delete_file", {"path": "important.db"})
        # → OperationLevel.RED
        
        # 红色操作生成确认提示
        if level == OperationLevel.RED:
            prompt = guard.confirmation_prompt("coder", "delete_file", {"path": "x", "count": 47})
            # → "⚠️ Agent [coder] 请求执行危险操作：..."
    """
    
    # === 操作分类表 ===
    
    _GREEN_OPS = {
        # 读取类
        "read_file", "list_dir", "stat_file", "search_file",
        "read_memory", "search_memory",
        # 计算类
        "calculate", "generate_text", "analyze", "summarize",
        # 查询类
        "web_search", "get_time", "get_weather",
        "query_log", "get_status",
    }
    
    _YELLOW_OPS = {
        # 写入类
        "write_file", "create_file", "append_file", "copy_file",
        "move_file", "rename_file",
        "store_memory", "update_memory",
        # 执行类
        "execute_command", "run_script", "run_shell",
        # 通信类
        "send_message", "send_email", "post_comment",
        # 网络类
        "http_request", "download_file",
    }
    
    _RED_OPS = {
        # 删除类
        "delete_file", "delete_dir", "remove_file", "purge",
        "forget_memory", "clear_data",
        # 安装类
        "install_package", "install_plugin", "pip_install",
        "npm_install", "apt_install",
        # 系统类
        "modify_system", "change_config", "update_settings",
        "create_user", "change_permission",
        # 敏感通信
        "send_sensitive", "share_secret", "publish",
        "send_bulk_message", "broadcast",
        # 金融类
        "transfer_money", "make_payment", "place_order",
    }
    
    # === 参数级别提升规则 ===
    # 某些参数会让操作升级（yellow → red）
    
    _ESCALATION_KEYWORDS = [
        "system", "root", "admin", "sudo",
        "password", "secret", "key", "token", "credential",
        "delete", "remove", "purge", "drop",
        "/etc/", "c:\\windows", "c:/windows",
        "*.db", "*.sqlite", "*.sql",
    ]
    
    def __init__(self):
        self._custom_overrides: Dict[str, OperationLevel] = {}
    
    def classify(
        self,
        operation: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> OperationLevel:
        """对操作进行安全分级
        
        Args:
            operation: 操作名称
            params: 操作参数
        
        Returns:
            OperationLevel (GREEN / YELLOW / RED)
        """
        # 自定义覆盖优先
        if operation in self._custom_overrides:
            return self._custom_overrides[operation]
        
        # 固定分类
        if operation in self._RED_OPS:
            return OperationLevel.RED
        
        if operation in self._YELLOW_OPS:
            # 检查参数是否需要升级到RED
            if params and self._should_escalate(params):
                return OperationLevel.RED
            return OperationLevel.YELLOW
        
        if operation in self._GREEN_OPS:
            return OperationLevel.GREEN
        
        # 未知操作默认YELLOW（记日志但不拦截）
        return OperationLevel.YELLOW
    
    def _should_escalate(self, params: Dict[str, Any]) -> bool:
        """检查参数是否触发升级"""
        # 将所有参数值拼接为小写字符串，统一路径分隔符
        params_str = str(params).lower().replace("\\\\", "/").replace("\\", "/")
        for keyword in self._ESCALATION_KEYWORDS:
            if keyword.lower().replace("\\\\", "/").replace("\\", "/") in params_str:
                return True
        return False
    
    def override(self, operation: str, level: OperationLevel) -> None:
        """自定义某个操作的安全级别
        
        Args:
            operation: 操作名称
            level: 强制设定的级别
        """
        self._custom_overrides[operation] = level
    
    def confirmation_prompt(
        self,
        agent_id: str,
        operation: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """生成红色操作的确认提示
        
        Args:
            agent_id: Agent标识
            operation: 操作名称
            params: 操作参数
            timeout_seconds: 超时秒数（默认5分钟）
        
        Returns:
            格式化的确认提示文本
        """
        detail = ""
        if params:
            # 简洁展示关键参数
            parts = []
            for k, v in params.items():
                parts.append(f"  {k}: {v}")
            detail = "\n".join(parts)
        
        timeout_min = timeout_seconds // 60
        
        lines = [
            f"⚠️ Agent [{agent_id}] 请求执行危险操作：",
            f"操作: {operation}",
        ]
        if detail:
            lines.append(f"参数:\n{detail}")
        lines.append(f"回复 Y 允许 / N 拒绝 / {timeout_min}分钟不回复自动拒绝")
        
        return "\n".join(lines)
    
    def is_auto_approved(self, level: OperationLevel) -> bool:
        """判断该级别是否自动放行
        
        Args:
            level: 操作安全级别
        
        Returns:
            True=自动放行, False=需要记录或确认
        """
        return level == OperationLevel.GREEN
