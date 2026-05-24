"""
xuanji 失败模式学习系统

从失败中提取教训，沉淀为"坑点记忆"，下次同类任务自动规避。

架构:
  错误捕获 → 分类 → 沉淀记忆 → 下次自动加载规避策略

零外部依赖。
"""

import json
import os
import time
import hashlib
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


# ============================================================
# 错误分类体系
# ============================================================

class ErrorCategory:
    """错误分类常量"""
    # 沙盒相关
    SANDBOX_TOO_STRICT = "sandbox_too_strict"      # 沙盒过严阻止合法操作
    SANDBOX_TOO_LOOSE = "sandbox_too_loose"         # 沙盒过松漏掉危险操作
    SANDBOX_TIMEOUT = "sandbox_timeout"             # 沙盒执行超时
    
    # 代码生成
    LLM_SYNTAX_ERROR = "llm_syntax_error"           # LLM生成代码语法错误
    LLM_LOGIC_ERROR = "llm_logic_error"             # LLM生成代码逻辑错误
    LLM_INCOMPLETE = "llm_incomplete"               # LLM生成代码不完整
    
    # 导入/依赖
    IMPORT_ERROR = "import_error"                   # 模块导入失败
    DEPENDENCY_MISSING = "dependency_missing"       # 依赖缺失
    
    # 运行时
    RUNTIME_ERROR = "runtime_error"                 # 运行时错误
    MEMORY_ERROR = "memory_error"                   # 内存不足
    NETWORK_ERROR = "network_error"                 # 网络错误
    
    # 配置
    CONFIG_ERROR = "config_error"                   # 配置错误
    PATH_ERROR = "path_error"                       # 路径错误
    
    # 未知
    UNKNOWN = "unknown"


# ============================================================
# 坑点记忆格式
# ============================================================

@dataclass
class PitfallMemory:
    """坑点记忆"""
    category: str                           # 错误分类
    task_type: str                          # 任务类型 (cli_app/web_scraper/...)
    error_signature: str                    # 错误签名 (hash of error message)
    error_message: str                      # 原始错误
    root_cause: str                         # 根因分析
    workaround: str                         # 解决方案
    prevention: str                         # 预防策略
    severity: int                           # 严重度 1-5
    confidence: float                       # 置信度 0-1
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0                      # 命中次数
    last_seen: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ============================================================
# 失败学习引擎
# ============================================================

class FailureLearner:
    """失败模式学习引擎
    
    Usage:
        learner = FailureLearner(data_dir="D:\\openagent\\workspace\\pitfalls")
        
        # 记录失败
        learner.record(
            category=ErrorCategory.SANDBOX_TOO_STRICT,
            task_type="cli_app",
            error="禁止导入模块: os",
            root_cause="沙盒黑名单包含os，但os.path是安全的",
            workaround="从BLOCKED_MODULES移除os，用visit_Attribute拦截os.system",
            prevention="下次任务自动调宽沙盒白名单",
            severity=3,
            confidence=0.95
        )
        
        # 查询规避策略
        strategies = learner.query(task_type="cli_app")
        for s in strategies:
            print(f"[{s.severity}] {s.prevention}")
    """
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "workspace", "pitfalls"
        )
        self.db_path = os.path.join(self.data_dir, "pitfalls.json")
        self._pitfalls: List[PitfallMemory] = []
        self._load()
    
    def _load(self):
        """加载坑点数据库"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._pitfalls = [PitfallMemory(**p) for p in data]
            except Exception:
                self._pitfalls = []
    
    def _save(self):
        """持久化坑点数据库"""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump([p.to_dict() for p in self._pitfalls], 
                     f, ensure_ascii=False, indent=2)
    
    def _compute_signature(self, error_msg: str) -> str:
        """计算错误签名（用于去重）"""
        # 提取关键错误模式（去掉行号等动态信息）
        import re
        normalized = re.sub(r'line \d+', 'line N', error_msg.lower())
        normalized = re.sub(r'0x[0-9a-f]+', '0xADDR', normalized)
        return hashlib.md5(normalized.encode()).hexdigest()[:12]
    
    def record(
        self,
        category: str,
        task_type: str,
        error: str,
        root_cause: str,
        workaround: str,
        prevention: str,
        severity: int = 3,
        confidence: float = 0.8,
    ) -> str:
        """记录一个坑点
        
        Returns:
            error_signature: 错误签名（可用于后续查询）
        """
        signature = self._compute_signature(error)
        
        # 检查是否已存在（去重）
        existing = self._find_by_signature(signature)
        if existing:
            existing.hit_count += 1
            existing.last_seen = time.time()
            existing.confidence = min(1.0, existing.confidence + 0.05)
            self._save()
            return signature
        
        # 新建坑点
        pitfall = PitfallMemory(
            category=category,
            task_type=task_type,
            error_signature=signature,
            error_message=error,
            root_cause=root_cause,
            workaround=workaround,
            prevention=prevention,
            severity=severity,
            confidence=confidence,
        )
        self._pitfalls.append(pitfall)
        self._save()
        return signature
    
    def _find_by_signature(self, signature: str) -> Optional[PitfallMemory]:
        for p in self._pitfalls:
            if p.error_signature == signature:
                return p
        return None
    
    def query(
        self,
        task_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[PitfallMemory]:
        """查询坑点记忆
        
        Args:
            task_type: 任务类型过滤
            category: 错误分类过滤
            limit: 返回数量上限
        
        Returns:
            按严重度+置信度排序的坑点列表
        """
        results = self._pitfalls
        
        if task_type:
            results = [p for p in results if p.task_type == task_type]
        if category:
            results = [p for p in results if p.category == category]
        
        # 排序：严重度高优先，置信度高优先，命中多优先
        results.sort(key=lambda p: (p.severity * p.confidence, p.hit_count), reverse=True)
        
        return results[:limit]
    
    def get_prevention_strategies(self, task_type: str) -> List[Dict]:
        """获取某类任务的预防策略列表
        
        Returns:
            [{"prevention": "...", "category": "...", "severity": N}, ...]
        """
        pitfalls = self.query(task_type=task_type)
        return [
            {
                "prevention": p.prevention,
                "category": p.category,
                "severity": p.severity,
                "confidence": p.confidence,
            }
            for p in pitfalls
        ]
    
    def should_adjust_sandbox(self, task_type: str) -> Dict:
        """判断是否需要调整沙盒策略
        
        Returns:
            {"adjust": True/False, "reason": "...", "allow_modules": [...], "block_modules": [...]}
        """
        pitfalls = self.query(
            task_type=task_type,
            category=ErrorCategory.SANDBOX_TOO_STRICT,
        )
        
        if not pitfalls:
            return {"adjust": False}
        
        # 收集需要放行的模块
        allow_modules = set()
        reasons = []
        for p in pitfalls:
            if "移除" in p.workaround or "放行" in p.workaround:
                # 简单解析：提取模块名
                import re
                mods = re.findall(r'模块[：:]\s*(\w+)', p.workaround)
                allow_modules.update(mods)
            reasons.append(p.root_cause)
        
        return {
            "adjust": True,
            "reason": "; ".join(reasons[:3]),
            "allow_modules": list(allow_modules),
            "severity": max(p.severity for p in pitfalls),
        }
    
    def stats(self) -> Dict:
        """统计信息"""
        by_category = {}
        by_task = {}
        for p in self._pitfalls:
            by_category[p.category] = by_category.get(p.category, 0) + 1
            by_task[p.task_type] = by_task.get(p.task_type, 0) + 1
        
        return {
            "total": len(self._pitfalls),
            "by_category": by_category,
            "by_task_type": by_task,
            "total_hits": sum(p.hit_count for p in self._pitfalls),
        }


# ============================================================
# 自动规避集成点
# ============================================================

def auto_adjust_for_task(learner: FailureLearner, task_type: str, config: Dict) -> Dict:
    """根据坑点记忆自动调整任务配置
    
    在任务启动前调用，自动加载历史教训。
    
    Args:
        learner: FailureLearner 实例
        task_type: 任务类型
        config: 原始配置
    
    Returns:
        调整后的配置
    """
    strategies = learner.get_prevention_strategies(task_type)
    if not strategies:
        return config
    
    adjusted = dict(config)
    adjusted["_prevention_strategies"] = strategies
    
    # 沙盒策略调整
    sandbox_adj = learner.should_adjust_sandbox(task_type)
    if sandbox_adj.get("adjust"):
        adjusted["_sandbox_adjustment"] = sandbox_adj
    
    return adjusted


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile
    
    # 创建临时数据库
    tmpdir = tempfile.mkdtemp()
    learner = FailureLearner(data_dir=tmpdir)
    
    # 记录今天的坑点
    print("=== 记录坑点 ===")
    
    learner.record(
        category=ErrorCategory.SANDBOX_TOO_STRICT,
        task_type="cli_app",
        error="禁止导入模块: os (line 1)",
        root_cause="沙盒黑名单包含os，但os.path/os.makedirs是安全操作",
        workaround="从BLOCKED_MODULES移除os，用visit_Attribute拦截os.system/popen",
        prevention="下次cli_app任务自动调宽沙盒白名单，允许os导入",
        severity=3,
        confidence=0.95,
    )
    
    learner.record(
        category=ErrorCategory.SANDBOX_TOO_STRICT,
        task_type="cli_app",
        error="禁止调用内置函数: exec() (line 1)",
        root_cause="exec在BLOCKED_BUILTINS中，但Python import机制内部用exec执行模块代码",
        workaround="从BLOCKED_BUILTINS移除exec，AST扫描器已静态拦截exec()调用",
        prevention="下次任务保留exec/compile/__import__，由AST扫描器负责拦截",
        severity=4,
        confidence=0.95,
    )
    
    learner.record(
        category=ErrorCategory.LLM_SYNTAX_ERROR,
        task_type="cli_app",
        error="SyntaxError: unterminated triple-quoted string literal (detected at line 91)",
        root_cause="Ollama本地模型生成的代码有未闭合的三引号字符串",
        workaround="用更强模型（qwen3.6-27b/GPT-4）生成代码，或加语法检查后处理",
        prevention="代码生成后先AST解析验证，失败则重试",
        severity=2,
        confidence=0.8,
    )
    
    print(f"  记录完成，共 {len(learner._pitfalls)} 条坑点")
    
    # 查询
    print("\n=== 查询 cli_app 任务的预防策略 ===")
    strategies = learner.get_prevention_strategies("cli_app")
    for s in strategies:
        print(f"  [{s['severity']}] {s['prevention']}")
    
    # 沙盒调整建议
    print("\n=== 沙盒调整建议 ===")
    adj = learner.should_adjust_sandbox("cli_app")
    print(f"  需要调整: {adj.get('adjust')}")
    if adj.get("adjust"):
        print(f"  原因: {adj['reason']}")
        print(f"  允许模块: {adj.get('allow_modules')}")
    
    # 统计
    print(f"\n=== 统计 ===")
    print(f"  {learner.stats()}")
