"""
xuanji 进化系统 —— 自动集成层

将失败学习、成功复用、跨任务泛化、安全自适配挂载到 HookManager，
在任务启动/完成/出错时自动触发，无需手动调用。

使用方式:
    from .hook import EvolutionHook
    from .hooks import HookManager
    
    # 方式1: 直接挂载到已有 HookManager
    hm = HookManager()
    EvolutionHook.attach(hm)
    
    # 方式2: 创建时自动挂载
    ev = EvolutionHook()
    ev.register_to(hm)
    
    # 之后所有通过 HookManager 执行的任务都会自动:
    # - 启动前: 加载预防策略 + 成功模板 + 自适应安全策略
    # - 完成后: 提取成功模式 + 记录跨任务经验
    # - 出错时: 记录失败教训 + 调整沙盒策略
"""

import os
import time
import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EvolutionHook:
    """进化系统钩子 —— 自动挂载到 HookManager
    
    挂载点:
    - before_task: 加载进化配置（预防策略 + 模板 + 安全策略）
    - after_task: 提取成功模式 + 记录跨任务经验
    - on_error: 记录失败教训 + 调整策略
    """
    
    def __init__(
        self,
        data_dir: str = None,
        enabled: bool = True,
        auto_extract: bool = True,
        auto_adjust: bool = True,
    ):
        """
        Args:
            data_dir: 数据目录（pitfalls/patterns/cross_index 的父目录）
            enabled: 是否启用进化系统
            auto_extract: 是否自动提取成功模式
            auto_adjust: 是否自动调整沙盒策略
        """
        self.enabled = enabled
        self.auto_extract = auto_extract
        self.auto_adjust = auto_adjust
        
        # 延迟导入（避免循环依赖）
        self._learner = None
        self._pattern_lib = None
        self._cross_index = None
        self._security_engine = None
        self._extractor = None
        
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "workspace"
        )
        
        # 统计
        self._stats = {
            "tasks_hooked": 0,
            "strategies_loaded": 0,
            "patterns_extracted": 0,
            "pitfalls_recorded": 0,
            "errors_caught": 0,
        }
    
    # ============================================================
    # 延迟加载（避免循环依赖）
    # ============================================================
    
    @property
    def learner(self):
        if self._learner is None:
            from .failure_learning import FailureLearner
            self._learner = FailureLearner(
                data_dir=os.path.join(self.data_dir, "pitfalls")
            )
        return self._learner
    
    @property
    def pattern_lib(self):
        if self._pattern_lib is None:
            from .pattern_reuse import PatternLibrary
            self._pattern_lib = PatternLibrary(
                data_dir=os.path.join(self.data_dir, "patterns")
            )
        return self._pattern_lib
    
    @property
    def cross_index(self):
        if self._cross_index is None:
            from xuanji.cross_task_index import CrossTaskIndex
            self._cross_index = CrossTaskIndex(
                data_dir=os.path.join(self.data_dir, "cross_index")
            )
        return self._cross_index
    
    @property
    def security_engine(self):
        if self._security_engine is None:
            from xuanji.adaptive_security import AdaptiveSecurityEngine
            self._security_engine = AdaptiveSecurityEngine(
                config_path=os.path.join(self.data_dir, "security_policy.json")
            )
        return self._security_engine
    
    @property
    def extractor(self):
        if self._extractor is None:
            from .pattern_reuse import PatternExtractor
            self._extractor = PatternExtractor()
        return self._extractor
    
    # ============================================================
    # 挂载到 HookManager
    # ============================================================
    
    def register_to(self, hook_manager) -> None:
        """注册到 HookManager
        
        Args:
            hook_manager: HookManager 实例
        """
        if not self.enabled:
            return
        
        # before_task: 加载进化配置
        hook_manager.before_task(
            callback=self._before_task,
            priority=100,  # 最高优先级，最先执行
            name="evolution_before_task",
        )
        
        # after_task: 提取成功模式
        hook_manager.after_task(
            callback=self._after_task,
            priority=-100,  # 最低优先级，最后执行
            name="evolution_after_task",
        )
        
        # on_error: 记录失败教训
        hook_manager.on_error(
            callback=self._on_error,
            priority=100,  # 最高优先级
            name="evolution_on_error",
        )
        
        logger.info("EvolutionHook registered to HookManager")
    
    @staticmethod
    def attach(hook_manager) -> 'EvolutionHook':
        """静态方法: 创建并挂载
        
        Args:
            hook_manager: HookManager 实例
        
        Returns:
            EvolutionHook 实例
        """
        ev = EvolutionHook()
        ev.register_to(hook_manager)
        return ev
    
    # ============================================================
    # 钩子回调
    # ============================================================
    
    def _before_task(self, context: Dict) -> Dict:
        """任务启动前: 加载进化配置
        
        注入到 context:
        - _prevention_strategies: 预防策略列表
        - _template: 成功模板
        - _sandbox_policy: 自适应沙盒策略
        """
        self._stats["tasks_hooked"] += 1
        
        task_type = context.get("task_type", "unknown")
        task_features = context.get("task_features", {})
        
        # 1. 加载预防策略
        strategies = self.learner.get_prevention_strategies(task_type)
        if strategies:
            context["_prevention_strategies"] = strategies
            self._stats["strategies_loaded"] += len(strategies)
            logger.info(f"Evolution: loaded {len(strategies)} prevention strategies for {task_type}")
        
        # 2. 加载成功模板
        if self.auto_extract:
            template = self.pattern_lib.load_template(task_type)
            if template:
                context["_template"] = template
                logger.info(f"Evolution: loaded template '{template['name']}' for {task_type}")
        
        # 3. 加载自适应安全策略
        if self.auto_adjust and task_features:
            policy = self.security_engine.get_policy(task_features)
            context["_sandbox_policy"] = policy
            logger.info(f"Evolution: security policy={policy.risk_level} for {task_type}")
        
        # 4. 注入沙盒调整建议
        if self.auto_adjust:
            sandbox_adj = self.learner.should_adjust_sandbox(task_type)
            if sandbox_adj.get("adjust"):
                context["_sandbox_adjustment"] = sandbox_adj
                logger.info(f"Evolution: sandbox adjustment needed for {task_type}")
        
        return context
    
    def _after_task(self, context: Dict, result: Any) -> Any:
        """任务完成后: 提取成功模式 + 记录跨任务经验"""
        task_type = context.get("task_type", "unknown")
        domain = context.get("domain", "development")
        elapsed = context.get("elapsed_time", 0)
        tokens = context.get("tokens_used", 0)
        
        # 1. 提取成功模式（如果是代码类任务且成功）
        if self.auto_extract and result and self._is_code_task(task_type):
            try:
                # 检查代码是否通过沙盒验证
                if context.get("sandbox_verified", False):
                    pattern = self.extractor.extract_cli_app_pattern({
                        "subtasks": context.get("subtasks", []),
                        "llm_calls": context.get("llm_calls", 0),
                        "memory_retrieved": context.get("memory_retrieved", 0),
                        "iterations": context.get("iterations", 1),
                        "template_used": context.get("_template") is not None,
                        "sandbox_verified": True,
                    })
                    self.pattern_lib.save(pattern)
                    self._stats["patterns_extracted"] += 1
                    logger.info(f"Evolution: extracted pattern '{pattern.name}'")
            except Exception as e:
                logger.warning(f"Evolution: pattern extraction failed: {e}")
        
        # 2. 记录跨任务经验
        try:
            # 提取策略标签
            from xuanji.cross_task_index import StrategyExtractor
            se = StrategyExtractor()
            task_result = {
                "subtasks": context.get("subtasks", []),
                "llm_calls": context.get("llm_calls", 0),
                "memory_retrieved": context.get("memory_retrieved", 0),
                "iterations": context.get("iterations", 1),
                "template_used": context.get("_template") is not None,
                "sandbox_verified": context.get("sandbox_verified", False),
            }
            tags = se.extract(task_type, task_result)
            abstract = se.get_abstract_strategy(tags)
            
            self.cross_index.record(
                task_type=task_type,
                domain=domain,
                strategy=abstract,
                strategy_tags=tags,
                success=True,
                metrics={
                    "time": elapsed,
                    "tokens": tokens,
                    "quality": context.get("quality_score", 0.8),
                },
                generalizable_to=self._get_generalizable(task_type),
            )
            logger.info(f"Evolution: recorded experience for {task_type} (tags={tags})")
        except Exception as e:
            logger.warning(f"Evolution: cross-index recording failed: {e}")
        
        # 3. 反馈安全策略
        if self.auto_adjust:
            try:
                task_features = context.get("task_features", {})
                policy = context.get("_sandbox_policy")
                if policy:
                    self.security_engine.feedback(
                        task_features=task_features,
                        policy=policy,
                        success=True,
                        error=None,
                    )
            except Exception as e:
                logger.warning(f"Evolution: security feedback failed: {e}")
        
        return result
    
    def _on_error(self, context: Dict, error: Exception) -> None:
        """任务出错: 记录失败教训"""
        self._stats["errors_caught"] += 1
        
        task_type = context.get("task_type", "unknown")
        error_msg = str(error)
        
        # 1. 分类错误
        category = self._classify_error(error_msg)
        
        # 2. 记录坑点
        try:
            workaround = self._suggest_workaround(category, error_msg)
            prevention = self._suggest_prevention(category, task_type)
            
            self.learner.record(
                category=category,
                task_type=task_type,
                error=error_msg,
                root_cause=self._analyze_root_cause(category, error_msg),
                workaround=workaround,
                prevention=prevention,
                severity=self._estimate_severity(category),
                confidence=0.7,  # 自动记录的置信度较低，人工确认后可提升
            )
            self._stats["pitfalls_recorded"] += 1
            logger.info(f"Evolution: recorded pitfall [{category}] for {task_type}")
        except Exception as e:
            logger.warning(f"Evolution: pitfall recording failed: {e}")
        
        # 3. 反馈安全策略（如果是沙盒相关错误）
        if self.auto_adjust and "sandbox" in category:
            try:
                task_features = context.get("task_features", {})
                policy = context.get("_sandbox_policy")
                if policy:
                    self.security_engine.feedback(
                        task_features=task_features,
                        policy=policy,
                        success=False,
                        error=error_msg,
                    )
            except Exception as e:
                logger.warning(f"Evolution: security feedback failed: {e}")
    
    # ============================================================
    # 辅助方法
    # ============================================================
    
    def _is_code_task(self, task_type: str) -> bool:
        """判断是否为代码类任务"""
        code_tasks = {"cli_app", "code_gen", "crud_app", "data_tool", 
                      "web_scraper", "file_processor", "api_client"}
        return task_type in code_tasks or "app" in task_type or "gen" in task_type
    
    def _get_generalizable(self, task_type: str) -> List[str]:
        """获取可泛化的任务类型"""
        mapping = {
            "cli_app": ["crud_app", "data_tool", "cli_tool"],
            "web_scraper": ["web_research", "content_analysis"],
            "code_gen": ["code_review", "code_refactor"],
        }
        return mapping.get(task_type, [])
    
    def _classify_error(self, error_msg: str) -> str:
        """自动分类错误"""
        from .failure_learning import ErrorCategory
        
        msg = error_msg.lower()
        
        if "sandbox" in msg or "禁止" in msg or "blocked" in msg:
            if "line" in msg or "syntax" in msg:
                return ErrorCategory.LLM_SYNTAX_ERROR
            return ErrorCategory.SANDBOX_TOO_STRICT
        
        if "syntax" in msg or "syntaxerror" in msg:
            return ErrorCategory.LLM_SYNTAX_ERROR
        
        if "import" in msg and ("error" in msg or "not found" in msg):
            return ErrorCategory.IMPORT_ERROR
        
        if "timeout" in msg or "超时" in msg:
            return ErrorCategory.SANDBOX_TIMEOUT
        
        if "memory" in msg or "内存" in msg:
            return ErrorCategory.MEMORY_ERROR
        
        if "network" in msg or "networkerror" in msg:
            return ErrorCategory.NETWORK_ERROR
        
        if "path" in msg or "no such file" in msg or "文件" in msg:
            return ErrorCategory.PATH_ERROR
        
        return ErrorCategory.UNKNOWN
    
    def _analyze_root_cause(self, category: str, error_msg: str) -> str:
        """分析根因"""
        causes = {
            "sandbox_too_strict": "沙盒策略过严，阻止了合法操作",
            "llm_syntax_error": "LLM生成的代码有语法错误",
            "import_error": "模块导入失败，可能是依赖缺失或路径错误",
            "sandbox_timeout": "沙盒执行超时，任务复杂度超出预期",
        }
        return causes.get(category, f"未知原因: {error_msg[:100]}")
    
    def _suggest_workaround(self, category: str, error_msg: str) -> str:
        """建议解决方案"""
        workarounds = {
            "sandbox_too_strict": "调宽沙盒白名单，允许被阻止的模块/函数",
            "llm_syntax_error": "用更强模型生成代码，或加语法检查后处理",
            "import_error": "检查依赖安装，确认模块路径正确",
            "sandbox_timeout": "增加超时时间，或拆分任务为更小的子任务",
        }
        return workarounds.get(category, "需要人工分析")
    
    def _suggest_prevention(self, category: str, task_type: str) -> str:
        """建议预防策略"""
        preventions = {
            "sandbox_too_strict": f"下次 {task_type} 任务自动调宽沙盒白名单",
            "llm_syntax_error": f"下次 {task_type} 任务代码生成后先AST验证",
            "import_error": f"下次 {task_type} 任务先检查依赖环境",
            "sandbox_timeout": f"下次 {task_type} 任务增加超时或拆分任务",
        }
        return preventions.get(category, "需要人工制定预防策略")
    
    def _estimate_severity(self, category: str) -> int:
        """评估严重度 (1-5)"""
        severities = {
            "sandbox_too_strict": 3,
            "sandbox_too_loose": 5,
            "llm_syntax_error": 2,
            "llm_logic_error": 3,
            "import_error": 2,
            "sandbox_timeout": 2,
            "runtime_error": 3,
            "memory_error": 4,
            "network_error": 2,
            "config_error": 2,
            "path_error": 1,
        }
        return severities.get(category, 2)
    
    # ============================================================
    # 统计
    # ============================================================
    
    def stats(self) -> Dict:
        """进化系统统计"""
        return {
            **self._stats,
            "pitfalls_total": self.learner.stats()["total"] if self._learner else 0,
            "patterns_total": self.pattern_lib.stats()["total"] if self._pattern_lib else 0,
            "experience_total": self.cross_index.stats()["total"] if self._cross_index else 0,
        }
    
    def summary(self) -> str:
        """人类可读的摘要"""
        s = self.stats()
        return (
            f"进化系统状态:\n"
            f"  任务挂钩: {s['tasks_hooked']} 次\n"
            f"  策略加载: {s['strategies_loaded']} 次\n"
            f"  模式提取: {s['patterns_extracted']} 个\n"
            f"  坑点记录: {s['pitfalls_recorded']} 个 (库中共 {s['pitfalls_total']} 条)\n"
            f"  错误捕获: {s['errors_caught']} 次\n"
            f"  经验索引: {s['experience_total']} 条"
        )


# ============================================================
# 便捷函数
# ============================================================

def auto_evolve(hook_manager, task_type: str = None, **kwargs) -> Dict:
    """一键获取进化配置（不挂载钩子，手动调用）
    
    适用于不需要完整 HookManager 的场景。
    
    Args:
        hook_manager: HookManager 实例（用于创建 EvolutionHook）
        task_type: 任务类型
        **kwargs: 任务特征
    
    Returns:
        {
            "prevention_strategies": [...],
            "template": {...},
            "sandbox_policy": {...},
            "sandbox_adjustment": {...},
        }
    """
    ev = EvolutionHook()
    
    # 模拟 before_task 钩子
    context = {"task_type": task_type or "unknown", "task_features": kwargs}
    context = ev._before_task(context)
    
    return {
        "prevention_strategies": context.get("_prevention_strategies", []),
        "template": context.get("_template"),
        "sandbox_policy": context.get("_sandbox_policy"),
        "sandbox_adjustment": context.get("_sandbox_adjustment"),
    }


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import sys
    import tempfile
    
    # 创建临时数据目录
    tmpdir = tempfile.mkdtemp()
    
    print("=== 测试 EvolutionHook ===\n")
    
    # 1. 创建 HookManager 并挂载
    from .hooks import HookManager
    
    hm = HookManager()
    ev = EvolutionHook(data_dir=tmpdir)
    ev.register_to(hm)
    print("EvolutionHook 已挂载到 HookManager\n")
    
    # 2. 模拟任务启动
    print("--- 模拟任务启动 (cli_app) ---")
    context = {
        "task_type": "cli_app",
        "task_features": {
            "category": "file_write",
            "is_cli_app": True,
        },
    }
    context = ev._before_task(context)
    print(f"  预防策略: {len(context.get('_prevention_strategies', []))} 条")
    print(f"  模板: {context.get('_template') is not None}")
    print(f"  沙盒策略: {context.get('_sandbox_policy')}")
    
    # 3. 先记录一些坑点，再测试
    print("\n--- 记录测试坑点 ---")
    from .failure_learning import ErrorCategory
    
    ev.learner.record(
        category=ErrorCategory.SANDBOX_TOO_STRICT,
        task_type="cli_app",
        error="禁止导入模块: os",
        root_cause="沙盒黑名单包含os",
        workaround="移除os黑名单",
        prevention="下次自动调宽白名单",
        severity=3,
        confidence=0.95,
    )
    print("  坑点已记录\n")
    
    # 4. 再次模拟任务启动（应该加载到新坑点的策略）
    print("--- 再次模拟任务启动 (cli_app) ---")
    context = {
        "task_type": "cli_app",
        "task_features": {"category": "file_write", "is_cli_app": True},
    }
    context = ev._before_task(context)
    strategies = context.get("_prevention_strategies", [])
    print(f"  预防策略: {len(strategies)} 条")
    for s in strategies:
        print(f"    [{s['severity']}] {s['prevention']}")
    
    # 5. 模拟任务完成
    print("\n--- 模拟任务完成 ---")
    context = {
        "task_type": "cli_app",
        "domain": "development",
        "elapsed_time": 90,
        "tokens_used": 15000,
        "subtasks": ["设计模型", "实现持久化", "实现CLI"],
        "llm_calls": 3,
        "memory_retrieved": 2,
        "iterations": 1,
        "sandbox_verified": True,
        "quality_score": 0.85,
    }
    result = {"status": "success", "files": 5}
    result = ev._after_task(context, result)
    print(f"  结果: {result}")
    
    # 6. 模拟任务出错
    print("\n--- 模拟任务出错 ---")
    context = {
        "task_type": "web_scraper",
        "task_features": {"category": "network_read"},
    }
    error = Exception("禁止导入模块: urllib - 沙盒过严")
    ev._on_error(context, error)
    print("  错误已记录为坑点")
    
    # 7. 统计
    print(f"\n=== 统计 ===")
    print(ev.summary())
    
    print(f"\n=== 坑点库 ===")
    pitfalls = ev.learner.query(task_type="cli_app")
    for p in pitfalls:
        print(f"  [{p.category}] {p.prevention}")
    
    print(f"\n=== 模式库 ===")
    patterns = ev.pattern_lib.find()
    for p in patterns:
        print(f"  [{p.confidence}] {p.name}")
    
    print(f"\n=== 经验索引 ===")
    experiences = ev.cross_index.search(success_only=True)
    for e in experiences:
        print(f"  [{e.domain}/{e.task_type}] {e.abstract_strategy}")
