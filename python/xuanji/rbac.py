"""
xuanji 权限管理（RBAC）

基于角色的访问控制系统。
支持角色管理、用户管理、权限检查、通配符匹配。
零外部依赖，仅使用标准库。
"""

import json
import time
import threading
import logging
import hashlib
import secrets
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─── 内置角色和权限 ─────────────────────────────────────────

BUILTIN_PERMISSIONS = {
    # Agent相关
    "agent.run",       # 运行Agent
    "agent.stop",      # 停止Agent
    "agent.config",    # 修改Agent配置
    "agent.create",    # 创建Agent
    "agent.delete",    # 删除Agent
    "agent.list",      # 列出Agent

    # 记忆相关
    "memory.read",     # 读取记忆
    "memory.write",    # 写入记忆
    "memory.delete",   # 删除记忆

    # 工具相关
    "tool.use",        # 使用工具
    "tool.install",    # 安装工具
    "tool.config",     # 配置工具

    # 管理相关
    "admin.users",     # 用户管理
    "admin.roles",     # 角色管理
    "admin.system",    # 系统管理
    "admin.audit",     # 审计日志
}

BUILTIN_ROLES: Dict[str, Dict] = {
    "admin": {
        "description": "管理员 — 拥有全部权限",
        "permissions": ["*"],  # 通配符，匹配所有
        "builtin": True,
    },
    "operator": {
        "description": "操作员 — 可运行Agent和修改配置",
        "permissions": [
            "agent.run", "agent.stop", "agent.config", "agent.list",
            "memory.read", "memory.write",
            "tool.use", "tool.config",
        ],
        "builtin": True,
    },
    "viewer": {
        "description": "观察者 — 只读权限",
        "permissions": [
            "agent.list",
            "memory.read",
        ],
        "builtin": True,
    },
}


# ─── 审计日志 ───────────────────────────────────────────────

class AuditEntry:
    """审计日志条目"""

    __slots__ = ("timestamp", "username", "action", "resource",
                 "result", "details")

    def __init__(self, username: str, action: str, resource: str,
                 result: str, details: str = ""):
        self.timestamp = time.time()
        self.username = username
        self.action = action
        self.resource = resource
        self.result = result  # "allow" or "deny"
        self.details = details

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(self.timestamp)),
            "username": self.username,
            "action": self.action,
            "resource": self.resource,
            "result": self.result,
            "details": self.details,
        }


# ─── 权限匹配 ───────────────────────────────────────────────

def _permission_matches(pattern: str, permission: str) -> bool:
    """检查权限模式是否匹配
    
    支持通配符:
    - "*" 匹配所有
    - "agent.*" 匹配 agent.run, agent.config 等
    - "tool.*" 匹配 tool.use, tool.install 等
    """
    if pattern == "*":
        return True
    if pattern == permission:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]  # "agent.*" → "agent"
        return permission.startswith(prefix + ".")
    return False


def _hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """简单密码哈希（SHA256 + salt）"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return hashed, salt


# ─── RBAC 主类 ─────────────────────────────────────────────

class RBAC:
    """基于角色的访问控制
    
    用法::
    
        rbac = RBAC()
        
        # 使用内置角色
        rbac.add_user("alice", roles=["admin"])
        rbac.add_user("bob", roles=["operator"])
        rbac.add_user("carol", roles=["viewer"])
        
        # 检查权限
        rbac.check("alice", "admin.system")   # True
        rbac.check("bob", "agent.run")         # True
        rbac.check("carol", "agent.run")       # False
        
        # 自定义角色
        rbac.add_role("developer", permissions=["agent.*", "tool.*", "memory.read"])
        rbac.add_user("dave", roles=["developer"])
        
        # 审计日志
        logs = rbac.get_audit_log(limit=10)
    """

    def __init__(self, enable_audit: bool = True, max_audit_entries: int = 10000):
        self._roles: Dict[str, Dict] = {}
        self._users: Dict[str, Dict] = {}
        self._audit: List[AuditEntry] = []
        self._max_audit = max_audit_entries
        self._enable_audit = enable_audit
        self._lock = threading.Lock()

        # 加载内置角色
        for name, role_def in BUILTIN_ROLES.items():
            self._roles[name] = dict(role_def)

    # ── 审计 ──

    def _audit_log(self, username: str, action: str, resource: str,
                   result: str, details: str = "") -> None:
        if not self._enable_audit:
            return
        entry = AuditEntry(username, action, resource, result, details)
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > self._max_audit:
                self._audit = self._audit[-self._max_audit:]

    # ── 角色管理 ──

    def add_role(self, name: str, permissions: List[str],
                 description: str = "") -> bool:
        """添加角色
        
        Args:
            name: 角色名
            permissions: 权限列表（支持通配符如 "agent.*"）
            description: 角色描述
        
        Returns:
            是否成功
        """
        with self._lock:
            if name in self._roles and self._roles[name].get("builtin"):
                logger.warning(f"不能修改内置角色: {name}")
                return False
            self._roles[name] = {
                "description": description,
                "permissions": list(permissions),
                "builtin": False,
            }
        logger.info(f"角色已添加: {name} ({len(permissions)} 权限)")
        return True

    def remove_role(self, name: str) -> bool:
        """移除角色"""
        with self._lock:
            if name not in self._roles:
                return False
            if self._roles[name].get("builtin"):
                logger.warning(f"不能删除内置角色: {name}")
                return False
            del self._roles[name]
            # 从所有用户中移除此角色
            for user in self._users.values():
                if name in user.get("roles", []):
                    user["roles"].remove(name)
        return True

    def get_role(self, name: str) -> Optional[Dict]:
        """获取角色信息"""
        with self._lock:
            return self._roles.get(name)

    def list_roles(self) -> Dict[str, Dict]:
        """列出所有角色"""
        with self._lock:
            return dict(self._roles)

    # ── 用户管理 ──

    def add_user(self, username: str, roles: Optional[List[str]] = None,
                 password: Optional[str] = None, metadata: Optional[Dict] = None) -> bool:
        """添加用户
        
        Args:
            username: 用户名
            roles: 角色列表
            password: 密码（可选，用于API认证）
            metadata: 额外元数据
        
        Returns:
            是否成功
        """
        with self._lock:
            if username in self._users:
                logger.warning(f"用户已存在: {username}")
                return False

            user_data: Dict[str, Any] = {
                "username": username,
                "roles": list(roles or []),
                "created_at": time.time(),
                "active": True,
                "metadata": metadata or {},
            }

            if password:
                hashed, salt = _hash_password(password)
                user_data["password_hash"] = hashed
                user_data["password_salt"] = salt

            # 验证角色存在
            for role in user_data["roles"]:
                if role not in self._roles:
                    logger.warning(f"角色不存在: {role}")
                    return False

            self._users[username] = user_data

        self._audit_log(username, "user.create", username, "allow")
        logger.info(f"用户已添加: {username} (角色: {roles})")
        return True

    def remove_user(self, username: str) -> bool:
        """移除用户"""
        with self._lock:
            if username not in self._users:
                return False
            del self._users[username]
        self._audit_log("system", "user.delete", username, "allow")
        return True

    def update_user(self, username: str, **fields) -> bool:
        """更新用户信息"""
        with self._lock:
            if username not in self._users:
                return False

            user = self._users[username]

            if "roles" in fields:
                new_roles = fields["roles"]
                for role in new_roles:
                    if role not in self._roles:
                        logger.warning(f"角色不存在: {role}")
                        return False
                user["roles"] = list(new_roles)

            if "password" in fields:
                hashed, salt = _hash_password(fields["password"])
                user["password_hash"] = hashed
                user["password_salt"] = salt

            if "active" in fields:
                user["active"] = bool(fields["active"])

            if "metadata" in fields:
                user["metadata"].update(fields["metadata"])

        self._audit_log("system", "user.update", username, "allow")
        return True

    def get_user(self, username: str) -> Optional[Dict]:
        """获取用户信息（不含密码）"""
        with self._lock:
            user = self._users.get(username)
            if user is None:
                return None
            safe = dict(user)
            safe.pop("password_hash", None)
            safe.pop("password_salt", None)
            return safe

    def list_users(self) -> List[Dict]:
        """列出所有用户（不含密码）"""
        with self._lock:
            result = []
            for user in self._users.values():
                safe = dict(user)
                safe.pop("password_hash", None)
                safe.pop("password_salt", None)
                result.append(safe)
            return result

    def authenticate(self, username: str, password: str) -> bool:
        """密码认证
        
        Args:
            username: 用户名
            password: 密码
        
        Returns:
            是否通过
        """
        with self._lock:
            user = self._users.get(username)
            if not user or not user.get("active"):
                self._audit_log(username, "auth", "login", "deny", "用户不存在或已禁用")
                return False

            stored_hash = user.get("password_hash")
            salt = user.get("password_salt")
            if not stored_hash or not salt:
                self._audit_log(username, "auth", "login", "deny", "未设置密码")
                return False

            check_hash, _ = _hash_password(password, salt)
            if check_hash == stored_hash:
                self._audit_log(username, "auth", "login", "allow")
                return True
            else:
                self._audit_log(username, "auth", "login", "deny", "密码错误")
                return False

    # ── 权限检查 ──

    def _get_user_permissions(self, username: str) -> Set[str]:
        """获取用户的所有权限（展开角色）"""
        user = self._users.get(username)
        if not user or not user.get("active"):
            return set()

        all_perms: Set[str] = set()
        for role_name in user.get("roles", []):
            role = self._roles.get(role_name)
            if role:
                all_perms.update(role["permissions"])
        return all_perms

    def check(self, username: str, permission: str) -> bool:
        """检查用户是否有指定权限
        
        Args:
            username: 用户名
            permission: 权限标识（如 "agent.run"）
        
        Returns:
            是否有权限
        """
        with self._lock:
            perms = self._get_user_permissions(username)

        allowed = any(_permission_matches(p, permission) for p in perms)

        self._audit_log(
            username, "check", permission,
            "allow" if allowed else "deny",
        )

        return allowed

    def check_any(self, username: str, permissions: List[str]) -> bool:
        """检查用户是否有任一权限"""
        return any(self.check(username, p) for p in permissions)

    def check_all(self, username: str, permissions: List[str]) -> bool:
        """检查用户是否有全部权限"""
        return all(self.check(username, p) for p in permissions)

    def get_user_permissions(self, username: str) -> List[str]:
        """获取用户的有效权限列表"""
        with self._lock:
            return sorted(self._get_user_permissions(username))

    # ── 审计日志 ──

    def get_audit_log(self, limit: int = 50, username: Optional[str] = None,
                      action: Optional[str] = None) -> List[Dict]:
        """获取审计日志
        
        Args:
            limit: 最多返回数量
            username: 按用户过滤
            action: 按动作过滤
        
        Returns:
            审计日志列表
        """
        with self._lock:
            entries = list(self._audit)

        if username:
            entries = [e for e in entries if e.username == username]
        if action:
            entries = [e for e in entries if e.action == action]

        return [e.to_dict() for e in entries[-limit:]]

    # ── 序列化 ──

    def export_config(self) -> Dict:
        """导出RBAC配置（不含密码和审计日志）"""
        with self._lock:
            users = []
            for user in self._users.values():
                safe = {
                    "username": user["username"],
                    "roles": user["roles"],
                    "active": user.get("active", True),
                    "metadata": user.get("metadata", {}),
                }
                users.append(safe)

            return {
                "roles": {k: v for k, v in self._roles.items() if not v.get("builtin")},
                "users": users,
                "builtin_roles": list(BUILTIN_ROLES.keys()),
            }

    def import_config(self, config: Dict) -> None:
        """导入RBAC配置"""
        with self._lock:
            # 导入自定义角色
            for name, role_def in config.get("roles", {}).items():
                if name not in BUILTIN_ROLES:
                    self._roles[name] = role_def

            # 导入用户
            for user_data in config.get("users", []):
                username = user_data["username"]
                if username not in self._users:
                    self._users[username] = {
                        "username": username,
                        "roles": user_data.get("roles", []),
                        "created_at": time.time(),
                        "active": user_data.get("active", True),
                        "metadata": user_data.get("metadata", {}),
                    }

    def export_json(self, indent: int = 2) -> str:
        """导出为JSON字符串"""
        return json.dumps(self.export_config(), ensure_ascii=False, indent=indent)
