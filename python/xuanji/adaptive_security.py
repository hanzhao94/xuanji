"""
xuanji 安全策略自适配引擎

沙盒严宽策略不是人工调，而是根据任务类型和风险等级自动分级。

架构:
  任务接收 → 风险评估 → 策略分级 → 沙盒配置 → 执行 → 反馈调整

零外部依赖。
"""

import json
import os
import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


# ============================================================
# 风险分级体系
# ============================================================

class RiskLevel:
    """风险等级"""
    LOW = "low"           # 低风险：纯计算、只读操作
    MEDIUM = "medium"     # 中风险：文件读写、网络请求
    HIGH = "high"         # 高风险：进程执行、系统调用
    CRITICAL = "critical" # 极高风险：网络写入、用户数据修改


class TaskCategory:
    """任务分类"""
    PURE_COMPUTE = "pure_compute"         # 纯计算（无IO）
    FILE_READ = "file_read"               # 文件读取
    FILE_WRITE = "file_write"             # 文件写入
    NETWORK_READ = "network_read"         # 网络读取
    NETWORK_WRITE = "network_write"       # 网络写入
    PROCESS_EXEC = "process_exec"         # 进程执行
    SYSTEM_CALL = "system_call"           # 系统调用
    USER_INTERACTION = "user_interaction" # 用户交互


# ============================================================
# 风险评级器
# ============================================================

class RiskAssessor:
    """任务风险评级器
    
    根据任务特征自动评估风险等级。
    """
    
    # 任务分类→基础风险等级映射
    BASE_RISK = {
        TaskCategory.PURE_COMPUTE: RiskLevel.LOW,
        TaskCategory.FILE_READ: RiskLevel.LOW,
        TaskCategory.FILE_WRITE: RiskLevel.MEDIUM,
        TaskCategory.NETWORK_READ: RiskLevel.MEDIUM,
        TaskCategory.NETWORK_WRITE: RiskLevel.HIGH,
        TaskCategory.PROCESS_EXEC: RiskLevel.HIGH,
        TaskCategory.SYSTEM_CALL: RiskLevel.CRITICAL,
        TaskCategory.USER_INTERACTION: RiskLevel.MEDIUM,
    }
    
    # 风险放大器（多个因素叠加时提升等级）
    RISK_MULTIPLIERS = {
        "has_network": 1,       # 有网络操作+1级
        "has_process": 1,       # 有进程执行+1级
        "has_system": 2,        # 有系统调用+2级
        "has_user_data": 1,     # 操作用户数据+1级
        "has_external_input": 1, # 有外部输入+1级
        "is_write_operation": 1, # 是写操作+1级
    }
    
    RISK_LEVELS = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
    
    def assess(self, task_features: Dict) -> str:
        """评估任务风险等级
        
        Args:
            task_features: 任务特征字典
                {
                    "category": "file_write",
                    "has_network": False,
                    "has_process": True,
                    "has_user_data": True,
                    "is_write_operation": True,
                    ...
                }
        
        Returns:
            风险等级字符串
        """
        category = task_features.get("category", TaskCategory.PURE_COMPUTE)
        base_level = self.BASE_RISK.get(category, RiskLevel.MEDIUM)
        
        # 计算风险增幅
        multiplier = 0
        for key, value in task_features.items():
            if key in self.RISK_MULTIPLIERS and value:
                multiplier += self.RISK_MULTIPLIERS[key]
        
        # 提升风险等级
        base_index = self.RISK_LEVELS.index(base_level)
        final_index = min(base_index + multiplier, len(self.RISK_LEVELS) - 1)
        
        return self.RISK_LEVELS[final_index]
    
    def get_risk_reason(self, task_features: Dict, risk_level: str) -> str:
        """获取风险评估原因"""
        category = task_features.get("category", "unknown")
        reasons = [f"基础分类: {category}"]
        
        for key, value in task_features.items():
            if key in self.RISK_MULTIPLIERS and value:
                reasons.append(f"{key}: +{self.RISK_MULTIPLIERS[key]}级")
        
        return " | ".join(reasons)


# ============================================================
# 沙盒策略配置
# ============================================================

@dataclass
class SandboxPolicy:
    """沙盒策略配置"""
    risk_level: str                     # 风险等级
    
    # 模块白名单/黑名单
    allow_modules: List[str] = field(default_factory=list)
    block_modules: List[str] = field(default_factory=list)
    
    # 内置函数白名单/黑名单
    allow_builtins: List[str] = field(default_factory=list)
    block_builtins: List[str] = field(default_factory=list)
    
    # 文件访问
    allow_file_read: bool = True
    allow_file_write: bool = False
    allow_file_delete: bool = False
    file_paths: List[str] = field(default_factory=list)  # 允许的路径
    
    # 网络访问
    allow_network: bool = False
    network_urls: List[str] = field(default_factory=list)  # 允许的URL
    
    # 进程执行
    allow_process: bool = False
    allowed_commands: List[str] = field(default_factory=list)
    
    # 超时
    timeout_seconds: float = 30.0
    
    # 其他
    enable_scan: bool = True            # 启用AST扫描
    require_approval: bool = False      # 需要人工审批
    
    def to_dict(self) -> Dict:
        return asdict(self)


# 预定义策略模板
POLICY_TEMPLATES = {
    RiskLevel.LOW: SandboxPolicy(
        risk_level=RiskLevel.LOW,
        allow_modules=["json", "os", "math", "datetime", "typing", "collections"],
        block_modules=["subprocess", "socket", "http", "urllib"],
        block_builtins=["exec", "eval", "compile"],
        allow_file_read=True,
        allow_file_write=False,
        allow_network=False,
        allow_process=False,
        timeout_seconds=60.0,
        enable_scan=True,
        require_approval=False,
    ),
    
    RiskLevel.MEDIUM: SandboxPolicy(
        risk_level=RiskLevel.MEDIUM,
        allow_modules=["json", "os", "math", "datetime", "typing", "collections", 
                      "argparse", "pathlib", "tempfile", "importlib"],
        block_modules=["subprocess", "socket", "http", "urllib"],
        block_builtins=["eval", "compile"],  # exec保留给import机制
        allow_file_read=True,
        allow_file_write=True,
        allow_file_delete=False,
        allow_network=False,
        allow_process=False,
        timeout_seconds=120.0,
        enable_scan=True,
        require_approval=False,
    ),
    
    RiskLevel.HIGH: SandboxPolicy(
        risk_level=RiskLevel.HIGH,
        allow_modules=["json", "os", "math", "datetime", "typing", "collections",
                      "argparse", "pathlib", "tempfile", "importlib", "urllib"],
        block_modules=["subprocess", "socket", "http"],
        block_builtins=["eval", "compile"],
        allow_file_read=True,
        allow_file_write=True,
        allow_file_delete=False,
        allow_network=True,
        network_urls=["https://*.python.org", "https://pypi.org/*"],
        allow_process=True,
        allowed_commands=["python", "pip"],
        timeout_seconds=300.0,
        enable_scan=True,
        require_approval=True,  # 高风险需要审批
    ),
    
    RiskLevel.CRITICAL: SandboxPolicy(
        risk_level=RiskLevel.CRITICAL,
        allow_modules=[],  # 全量检查
        block_modules=[],
        block_builtins=["eval"],
        allow_file_read=True,
        allow_file_write=True,
        allow_file_delete=True,
        allow_network=True,
        allow_process=True,
        allowed_commands=[],  # 全量检查
        timeout_seconds=600.0,
        enable_scan=True,
        require_approval=True,  # 极高风险必须审批
    ),
}


# ============================================================
# 自适应策略引擎
# ============================================================

class AdaptiveSecurityEngine:
    """安全策略自适配引擎
    
    Usage:
        engine = AdaptiveSecurityEngine(
            config_path="D:\\openagent\\workspace\\security_policy.json"
        )
        
        # 根据任务获取策略
        task_features = {
            "category": "file_write",
            "has_process": True,
            "has_user_data": True,
        }
        policy = engine.get_policy(task_features)
        
        # 反馈执行结果，自动调整
        engine.feedback(task_features, policy, success=True, error=None)
    """
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "workspace", "security_policy.json"
        )
        self.assessor = RiskAssessor()
        self._history: List[Dict] = []
        self._custom_policies: Dict[str, SandboxPolicy] = {}
        self._load()
    
    def _load(self):
        """加载自定义策略配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for level, policy_data in data.get("custom_policies", {}).items():
                    self._custom_policies[level] = SandboxPolicy(**policy_data)
                self._history = data.get("history", [])
            except Exception:
                pass
    
    def _save(self):
        """持久化配置"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        data = {
            "custom_policies": {k: v.to_dict() for k, v in self._custom_policies.items()},
            "history": self._history[-100:],  # 只保留最近100条
        }
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def get_policy(self, task_features: Dict) -> SandboxPolicy:
        """根据任务特征获取沙盒策略
        
        Returns:
            SandboxPolicy 配置
        """
        # 1. 风险评估
        risk_level = self.assessor.assess(task_features)
        
        # 2. 获取策略模板（优先用自定义）
        if risk_level in self._custom_policies:
            policy = self._custom_policies[risk_level]
        else:
            policy = POLICY_TEMPLATES[risk_level]
        
        # 3. 根据任务特征微调
        policy = self._fine_tune(policy, task_features)
        
        return policy
    
    def _fine_tune(self, policy: SandboxPolicy, task_features: Dict) -> SandboxPolicy:
        """根据任务特征微调策略"""
        adjusted = SandboxPolicy(**policy.to_dict())  # 深拷贝
        
        # 如果是CLI应用任务，放宽importlib
        if task_features.get("is_cli_app"):
            if "importlib" not in adjusted.allow_modules:
                adjusted.allow_modules.append("importlib")
        
        # 如果是代码生成任务，保留exec/compile
        if task_features.get("is_code_gen"):
            if "exec" in adjusted.block_builtins:
                adjusted.block_builtins.remove("exec")
            if "compile" in adjusted.block_builtins:
                adjusted.block_builtins.remove("compile")
        
        # 如果有外部输入，加强扫描
        if task_features.get("has_external_input"):
            adjusted.enable_scan = True
        
        return adjusted
    
    def feedback(
        self,
        task_features: Dict,
        policy: SandboxPolicy,
        success: bool,
        error: Optional[str] = None,
    ):
        """反馈执行结果，用于策略调整
        
        长期积累后，可以自动调整策略严宽度。
        """
        record = {
            "timestamp": time.time(),
            "task_features": task_features,
            "policy_risk_level": policy.risk_level,
            "success": success,
            "error": error,
        }
        self._history.append(record)
        
        # 如果连续失败，记录到历史（未来可用于自动调整）
        if not success:
            self._save()  # 只在失败时保存（避免频繁IO）
    
    def get_policy_stats(self) -> Dict:
        """策略使用统计"""
        by_level = {}
        success_rate = {}
        
        for record in self._history:
            level = record["policy_risk_level"]
            by_level[level] = by_level.get(level, 0) + 1
            if level not in success_rate:
                success_rate[level] = {"total": 0, "success": 0}
            success_rate[level]["total"] += 1
            if record["success"]:
                success_rate[level]["success"] += 1
        
        # 计算成功率
        for level in success_rate:
            total = success_rate[level]["total"]
            success_rate[level]["rate"] = (
                success_rate[level]["success"] / total if total > 0 else 0
            )
        
        return {
            "total_executions": len(self._history),
            "by_risk_level": by_level,
            "success_rate_by_level": success_rate,
        }
    
    def set_custom_policy(self, risk_level: str, policy: SandboxPolicy):
        """设置自定义策略"""
        self._custom_policies[risk_level] = policy
        self._save()
    
    def reset_to_defaults(self):
        """重置为默认策略"""
        self._custom_policies.clear()
        self._history.clear()
        self._save()


# ============================================================
# 便捷函数
# ============================================================

def get_sandbox_policy(task_type: str, features: Dict = None) -> SandboxPolicy:
    """快速获取某类任务的沙盒策略
    
    Usage:
        policy = get_sandbox_policy("cli_app")
        print(f"允许模块: {policy.allow_modules}")
        print(f"允许文件写入: {policy.allow_file_write}")
    """
    engine = AdaptiveSecurityEngine()
    
    # 根据任务类型推断特征
    task_features = features or {
        "category": _infer_category(task_type),
        "is_cli_app": task_type == "cli_app",
        "is_code_gen": task_type in ("code_gen", "code_review"),
    }
    
    return engine.get_policy(task_features)


def _infer_category(task_type: str) -> str:
    """根据任务类型推断风险分类"""
    mapping = {
        "cli_app": TaskCategory.FILE_WRITE,
        "web_scraper": TaskCategory.NETWORK_READ,
        "code_gen": TaskCategory.FILE_WRITE,
        "data_analysis": TaskCategory.PURE_COMPUTE,
        "file_processor": TaskCategory.FILE_WRITE,
    }
    return mapping.get(task_type, TaskCategory.PURE_COMPUTE)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile
    
    tmpdir = tempfile.mkdtemp()
    engine = AdaptiveSecurityEngine(config_path=os.path.join(tmpdir, "policy.json"))
    
    # 测试不同任务的策略
    print("=== CLI应用任务策略 ===")
    policy = engine.get_policy({
        "category": TaskCategory.FILE_WRITE,
        "is_cli_app": True,
        "has_process": True,
    })
    print(f"  风险等级: {policy.risk_level}")
    print(f"  允许模块: {policy.allow_modules}")
    print(f"  允许文件写入: {policy.allow_file_write}")
    print(f"  允许进程: {policy.allow_process}")
    print(f"  需要审批: {policy.require_approval}")
    
    print("\n=== 网页抓取任务策略 ===")
    policy = engine.get_policy({
        "category": TaskCategory.NETWORK_READ,
        "has_network": True,
    })
    print(f"  风险等级: {policy.risk_level}")
    print(f"  允许网络: {policy.allow_network}")
    print(f"  超时: {policy.timeout_seconds}秒")
    
    print("\n=== 系统管理任务策略 ===")
    policy = engine.get_policy({
        "category": TaskCategory.SYSTEM_CALL,
        "has_system": True,
        "has_user_data": True,
    })
    print(f"  风险等级: {policy.risk_level}")
    print(f"  需要审批: {policy.require_approval}")
    
    # 反馈
    print("\n=== 反馈执行结果 ===")
    engine.feedback(
        task_features={"category": TaskCategory.FILE_WRITE, "is_cli_app": True},
        policy=policy,
        success=True,
        error=None,
    )
    print(f"  统计: {engine.get_policy_stats()}")
    
    # 快速获取
    print("\n=== 快速获取策略 ===")
    policy = get_sandbox_policy("cli_app")
    print(f"  cli_app: 风险={policy.risk_level}, 文件写入={policy.allow_file_write}")
