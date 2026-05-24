"""
xuanji 成功模式复用系统

从跑通的任务中提取可复用套路，沉淀为"做事模板"，下次同类任务直接套用。

架构:
  任务完成 → 提取要素 → 生成模板 → 下次任务自动加载

零外部依赖。
"""

import json
import os
import time
import hashlib
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


# ============================================================
# 成功模式模板格式
# ============================================================

@dataclass
class SuccessPattern:
    """成功模式模板"""
    pattern_id: str                           # 模式ID
    name: str                                 # 模式名称
    task_type: str                            # 适用任务类型
    description: str                          # 模式描述
    
    # 团队分工模板
    team_roles: List[Dict] = field(default_factory=list)
    # [{"role": "developer", "skills": [...], "tasks": [...]}]
    
    # 代码结构模板
    code_structure: Dict = field(default_factory=dict)
    # {"files": [{"name": "...", "purpose": "...", "template": "..."}]}
    
    # 执行流程
    workflow: List[Dict] = field(default_factory=list)
    # [{"step": N, "action": "...", "output": "...", "verify": "..."}]
    
    # 测试策略
    test_strategy: Dict = field(default_factory=dict)
    # {"unit_test": "...", "integration_test": "...", "sandbox_test": "..."}
    
    # 效果数据
    metrics: Dict = field(default_factory=dict)
    # {"time_seconds": 90, "tokens": 15000, "quality_score": 0.85}
    
    # 泛化能力
    generalization: List[str] = field(default_factory=list)
    # 可泛化到的任务类型列表
    
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    confidence: float = 0.8
    
    def to_dict(self) -> Dict:
        return asdict(self)


# ============================================================
# 模式提取器
# ============================================================

class PatternExtractor:
    """从完成任务中提取成功模式"""
    
    def extract_cli_app_pattern(self, task_result: Dict) -> SuccessPattern:
        """从CLI应用任务中提取模式
        
        Args:
            task_result: 任务结果数据，包含团队分工、代码结构、测试结果等
        
        Returns:
            SuccessPattern 模板
        """
        return SuccessPattern(
            pattern_id=self._gen_id("cli_app"),
            name="CLI应用开发标准流程",
            task_type="cli_app",
            description="开发命令行应用的标准化流程：数据模型→持久化→核心逻辑→CLI接口→测试→文档",
            
            team_roles=[
                {"role": "developer", "focus": "数据模型+持久化+核心逻辑", "priority": 1},
                {"role": "developer", "focus": "CLI接口层", "priority": 2},
                {"role": "tester", "focus": "单元测试", "priority": 3},
                {"role": "developer", "focus": "集成+文档", "priority": 4},
            ],
            
            code_structure={
                "files": [
                    {"name": "main.py", "purpose": "CLI入口+argparse解析", "template": "argparse+subcommands"},
                    {"name": "models.py", "purpose": "数据模型定义", "template": "dataclass+typing"},
                    {"name": "storage.py", "purpose": "数据持久化", "template": "json_file_io"},
                    {"name": "core.py", "purpose": "核心业务逻辑", "template": "crud_functions"},
                    {"name": "test_main.py", "purpose": "单元测试", "template": "unittest+mock"},
                ]
            },
            
            workflow=[
                {"step": 1, "action": "定义数据模型", "output": "models.py", "verify": "AST解析通过"},
                {"step": 2, "action": "实现持久化层", "output": "storage.py", "verify": "沙盒读写测试"},
                {"step": 3, "action": "实现核心逻辑", "output": "core.py", "verify": "单元测试"},
                {"step": 4, "action": "构建CLI接口", "output": "main.py", "verify": "命令行调用测试"},
                {"step": 5, "action": "集成测试", "output": "test_main.py", "verify": "沙盒完整运行"},
                {"step": 6, "action": "生成文档", "output": "README.md", "verify": "Markdown格式"},
            ],
            
            test_strategy={
                "unit_test": "每个模块独立测试，用unittest",
                "integration_test": "沙盒中importlib加载完整模块",
                "sandbox_test": "禁止exec/eval，用importlib.util安全加载",
            },
            
            metrics={
                "time_seconds": 90,
                "tokens": 15000,
                "files_generated": 5,
                "test_pass_rate": 1.0,
            },
            
            generalization=["cli_tool", "crud_app", "data_manager"],
        )
    
    def extract_web_scraper_pattern(self, task_result: Dict) -> SuccessPattern:
        """从网页抓取任务中提取模式"""
        return SuccessPattern(
            pattern_id=self._gen_id("web_scraper"),
            name="网页抓取+分析标准流程",
            task_type="web_scraper",
            description="搜索→抓取→阅读→分析→报告→沉淀，6步闭环",
            
            team_roles=[
                {"role": "searcher", "focus": "搜索引擎+关键词优化", "priority": 1},
                {"role": "crawler", "focus": "网页抓取+去重", "priority": 2},
                {"role": "analyst", "focus": "LLM分析+报告生成", "priority": 3},
            ],
            
            workflow=[
                {"step": 1, "action": "搜索引擎查询", "output": "搜索结果列表", "verify": "≥3结果"},
                {"step": 2, "action": "抓取网页内容", "output": "原始HTML", "verify": "≥2网页成功"},
                {"step": 3, "action": "深度阅读提取", "output": "正文文本", "verify": "≥500字符/页"},
                {"step": 4, "action": "LLM分析", "output": "分析结果", "verify": "≥100字符"},
                {"step": 5, "action": "生成报告", "output": "Markdown报告", "verify": "≥500字符"},
                {"step": 6, "action": "记忆沉淀", "output": "经验记录", "verify": "≥1条"},
            ],
            
            test_strategy={
                "unit_test": "每个模块独立测试",
                "integration_test": "完整链路测试（搜索→报告）",
                "sandbox_test": "N/A（联网任务）",
            },
            
            metrics={
                "time_seconds": 47,
                "tokens": 4270,
                "pages_crawled": 3,
                "report_length": 798,
            },
            
            generalization=["web_research", "content_analysis", "trend_report"],
        )
    
    def _gen_id(self, prefix: str) -> str:
        return f"{prefix}_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"


# ============================================================
# 模式存储库
# ============================================================

class PatternLibrary:
    """成功模式存储库
    
    Usage:
        lib = PatternLibrary(data_dir="D:\\openagent\\workspace\\patterns")
        
        # 提取并保存模式
        extractor = PatternExtractor()
        pattern = extractor.extract_cli_app_pattern(task_result)
        lib.save(pattern)
        
        # 查询适用模式
        matches = lib.find(task_type="cli_app")
        for m in matches:
            print(f"[{m.confidence}] {m.name}")
        
        # 加载模板
        template = lib.load_template("cli_app")
    """
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "workspace", "patterns"
        )
        self.db_path = os.path.join(self.data_dir, "patterns.json")
        self._patterns: Dict[str, SuccessPattern] = {}
        self._load()
    
    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for p in data:
                    pattern = SuccessPattern(**p)
                    self._patterns[pattern.pattern_id] = pattern
            except Exception:
                self._patterns = {}
    
    def _save(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.db_path, 'w', encoding='utf-8') as f:
            json.dump([p.to_dict() for p in self._patterns.values()],
                     f, ensure_ascii=False, indent=2)
    
    def save(self, pattern: SuccessPattern):
        """保存或更新模式"""
        self._patterns[pattern.pattern_id] = pattern
        self._save()
    
    def find(
        self,
        task_type: Optional[str] = None,
        name_contains: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> List[SuccessPattern]:
        """查找适用模式"""
        results = list(self._patterns.values())
        
        if task_type:
            # 匹配 task_type 或 generalization
            results = [
                p for p in results
                if p.task_type == task_type or task_type in p.generalization
            ]
        
        if name_contains:
            results = [p for p in results if name_contains in p.name]
        
        if min_confidence > 0:
            results = [p for p in results if p.confidence >= min_confidence]
        
        # 按使用次数+置信度排序
        results.sort(key=lambda p: (p.usage_count, p.confidence), reverse=True)
        
        return results
    
    def load_template(self, task_type: str) -> Optional[Dict]:
        """加载任务类型的完整模板
        
        Returns:
            {
                "team_roles": [...],
                "code_structure": {...},
                "workflow": [...],
                "test_strategy": {...},
                "expected_metrics": {...},
            }
        """
        matches = self.find(task_type=task_type, min_confidence=0.7)
        if not matches:
            return None
        
        best = matches[0]
        best.usage_count += 1
        self._save()
        
        return {
            "pattern_id": best.pattern_id,
            "name": best.name,
            "team_roles": best.team_roles,
            "code_structure": best.code_structure,
            "workflow": best.workflow,
            "test_strategy": best.test_strategy,
            "expected_metrics": best.metrics,
        }
    
    def stats(self) -> Dict:
        return {
            "total": len(self._patterns),
            "by_task_type": self._count_by("task_type"),
            "total_usage": sum(p.usage_count for p in self._patterns.values()),
        }
    
    def _count_by(self, key: str) -> Dict:
        counts = {}
        for p in self._patterns.values():
            val = getattr(p, key, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts


# ============================================================
# 自动模板加载
# ============================================================

def auto_load_template(lib: PatternLibrary, task_type: str) -> Optional[Dict]:
    """根据任务类型自动加载模板
    
    在任务启动前调用，自动加载历史成功模式。
    
    Args:
        lib: PatternLibrary 实例
        task_type: 任务类型
    
    Returns:
        模板字典，或 None（无匹配模板）
    """
    return lib.load_template(task_type)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import tempfile
    
    tmpdir = tempfile.mkdtemp()
    lib = PatternLibrary(data_dir=tmpdir)
    extractor = PatternExtractor()
    
    # 提取CLI应用模式
    print("=== 提取CLI应用模式 ===")
    cli_pattern = extractor.extract_cli_app_pattern({})
    lib.save(cli_pattern)
    print(f"  已保存: {cli_pattern.name}")
    
    # 提取网页抓取模式
    print("\n=== 提取网页抓取模式 ===")
    web_pattern = extractor.extract_web_scraper_pattern({})
    lib.save(web_pattern)
    print(f"  已保存: {web_pattern.name}")
    
    # 查询
    print("\n=== 查询 cli_app 模板 ===")
    template = lib.load_template("cli_app")
    if template:
        print(f"  模板: {template['name']}")
        print(f"  团队: {len(template['team_roles'])} 个角色")
        print(f"  流程: {len(template['workflow'])} 步")
        print(f"  文件: {len(template['code_structure']['files'])} 个")
    
    # 泛化查询
    print("\n=== 泛化查询: crud_app ===")
    matches = lib.find(task_type="crud_app")
    for m in matches:
        print(f"  [{m.confidence}] {m.name} (泛化自: {m.task_type})")
    
    print(f"\n=== 统计 ===")
    print(f"  {lib.stats()}")
