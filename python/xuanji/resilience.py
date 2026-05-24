"""
xuanji 容错恢复系统

智能重试 + 故障自动恢复 + 模型选择:
  1. SmartRetry     — 智能重试（错误分类 + 不同故障不同策略）
  2. FaultRecovery  — 7种故障自动恢复配方
  3. ModelSelector  — 按任务类型选最优模型

# 核心逻辑提炼自开源工程实践
零外部依赖，纯Python标准库。
"""

import time
import random
from typing import Any, Callable, Dict, List, Optional, Tuple


# ===========================================================================
# 1. SmartRetry — 智能重试（比简单指数退避更强）
# ===========================================================================

class SmartRetry:
    """智能重试策略引擎
    
    核心优势（vs 简单指数退避）:
      - 错误分类：临时/输入/模型/系统四种类型
      - 不同故障不同策略：
        · 临时性错误 → 指数退避重试
        · 输入性错误 → 自动修正后重试1次
        · 模型性错误 → 先加约束，再换大模型
        · 系统性错误 → 不重试，立即报告
    
    用法::
    
        retry = SmartRetry(max_retries=3)
        
        # 简单用法
        result = retry.retry(my_api_call, prompt="hello")
        
        # 带回调的高级用法
        result = retry.retry(
            my_api_call, prompt="hello",
            _on_input_fix=lambda a, kw, e: (a, {**kw, "prompt": kw["prompt"][:1000]}),
            _on_upgrade_model=lambda a, kw: (a, {**kw, "model": "gpt-4"}),
        )
    """

    # 错误关键词 → 类型映射
    _TRANSIENT_KEYWORDS = [
        "timeout", "timed out", "429", "503", "502", "504",
        "rate limit", "throttl", "connection reset", "connection refused",
        "temporary", "retry", "too many requests", "service unavailable",
        "network", "unreachable", "ECONNRESET", "ETIMEDOUT",
    ]
    _INPUT_KEYWORDS = [
        "invalid", "parameter", "argument", "too long", "exceed",
        "maximum context", "max_tokens", "prompt is too long",
        "bad request", "400", "422", "validation",
    ]
    _MODEL_KEYWORDS = [
        "format", "json", "parse", "unexpected", "malformed",
        "hallucination", "off-topic", "irrelevant", "not valid json",
        "decode", "schema",
    ]
    _SYSTEM_KEYWORDS = [
        "api key", "apikey", "unauthorized", "401", "403", "forbidden",
        "expired", "revoked", "disabled", "service down", "deprecated",
        "quota exceeded", "billing",
    ]

    # Python异常类型 → 错误分类
    _EXCEPTION_TYPE_MAP = {
        "TimeoutError": "transient",
        "ConnectionError": "transient",
        "ConnectionResetError": "transient",
        "ConnectionRefusedError": "transient",
        "ConnectionAbortedError": "transient",
        "BrokenPipeError": "transient",
        "OSError": "transient",
        "IOError": "transient",
        "ValueError": "input",
        "TypeError": "input",
        "KeyError": "input",
        "IndexError": "input",
        "PermissionError": "system",
        "ImportError": "system",
        "ModuleNotFoundError": "system",
        "MemoryError": "system",
    }

    def __init__(self, max_retries: int = 3, base_wait: float = 1.0):
        """
        Args:
            max_retries: 临时性错误最大重试次数
            base_wait: 指数退避基础等待秒数
        """
        self.max_retries = max_retries
        self.base_wait = base_wait
        self.retry_log: List[dict] = []

    def classify_error(self, error: Exception) -> str:
        """分类错误类型
        
        分类优先级:
          1. 按Python异常类型精确匹配
          2. 按错误消息关键词匹配（更具体的优先）
          3. 按HTTP状态码属性
          4. 默认归为 transient（可重试比直接报错更安全）
        
        Returns:
            "transient" | "input" | "model" | "system"
        """
        error_type_name = type(error).__name__
        error_msg = str(error).lower()

        # 1. 按异常类型
        if error_type_name in self._EXCEPTION_TYPE_MAP:
            mapped = self._EXCEPTION_TYPE_MAP[error_type_name]
            # 关键词可能更具体
            keyword_type = self._classify_by_keywords(error_msg)
            if keyword_type and keyword_type != mapped:
                return keyword_type
            return mapped

        # 2. 按HTTP状态码属性
        status_code = getattr(error, "status_code", None) or \
                      getattr(error, "status", None) or \
                      getattr(error, "code", None)
        if status_code is not None:
            try:
                code = int(status_code)
                if code == 429 or code in (502, 503, 504):
                    return "transient"
                elif code in (400, 422):
                    return "input"
                elif code in (401, 403):
                    return "system"
            except (ValueError, TypeError):
                pass

        # 3. 按关键词
        keyword_type = self._classify_by_keywords(error_msg)
        if keyword_type:
            return keyword_type

        return "transient"

    def _classify_by_keywords(self, error_msg: str) -> Optional[str]:
        """按关键词分类，返回类型或None"""
        # 系统性错误优先（不可重试）
        for kw in self._SYSTEM_KEYWORDS:
            if kw in error_msg:
                return "system"
        for kw in self._INPUT_KEYWORDS:
            if kw in error_msg:
                return "input"
        for kw in self._MODEL_KEYWORDS:
            if kw in error_msg:
                return "model"
        for kw in self._TRANSIENT_KEYWORDS:
            if kw in error_msg:
                return "transient"
        return None

    def calculate_wait(self, attempt: int) -> float:
        """指数退避 + 随机抖动
        
        公式: wait = base * 2^attempt + random(0, 1)
        """
        wait = self.base_wait * (2 ** attempt) + random.random()
        return round(wait, 3)

    def should_retry(self, error_type: str, attempt: int) -> Tuple[bool, float, str]:
        """判断是否应该重试
        
        Returns:
            (是否重试, 等待秒数, 策略名)
            
        策略:
          - transient: 指数退避，最多 max_retries 次
          - input:     自动修正后重试1次（attempt=0时）
          - model:     最多2次，第1次加约束，第2次换大模型
          - system:    不重试
        """
        if error_type == "transient":
            if attempt < self.max_retries:
                return (True, self.calculate_wait(attempt), "exponential_backoff")
            return (False, 0, "max_retries_exceeded")

        elif error_type == "input":
            if attempt == 0:
                return (True, 0.5, "auto_fix_input")
            return (False, 0, "input_fix_failed")

        elif error_type == "model":
            if attempt == 0:
                return (True, 1.0, "add_constraints")
            elif attempt == 1:
                return (True, 2.0, "upgrade_model")
            return (False, 0, "model_retries_exhausted")

        elif error_type == "system":
            return (False, 0, "system_error_no_retry")

        return (False, 0, "unknown_error_type")

    def retry(self, func: Callable, *args, **kwargs) -> Any:
        """带智能重试的函数执行
        
        可选回调kwargs（会从kwargs中弹出，不传给func）:
          - _on_input_fix(args, kwargs, error) → (new_args, new_kwargs)
          - _on_add_constraints(args, kwargs) → (new_args, new_kwargs)
          - _on_upgrade_model(args, kwargs) → (new_args, new_kwargs)
        
        Returns:
            func的返回值
            
        Raises:
            最后一个异常（如果所有重试都失败）
        """
        on_input_fix = kwargs.pop("_on_input_fix", None)
        on_add_constraints = kwargs.pop("_on_add_constraints", None)
        on_upgrade_model = kwargs.pop("_on_upgrade_model", None)

        last_error = None
        attempt = 0

        while True:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                error_type = self.classify_error(e)
                should, wait, strategy = self.should_retry(error_type, attempt)

                self._log_retry(attempt, error_type, e, strategy)

                if not should:
                    raise

                if wait > 0:
                    time.sleep(wait)

                # 根据策略调整参数
                if strategy == "auto_fix_input" and on_input_fix:
                    try:
                        args, kwargs = on_input_fix(args, kwargs, e)
                    except Exception:
                        raise last_error
                elif strategy == "add_constraints" and on_add_constraints:
                    try:
                        args, kwargs = on_add_constraints(args, kwargs)
                    except Exception:
                        raise last_error
                elif strategy == "upgrade_model" and on_upgrade_model:
                    try:
                        args, kwargs = on_upgrade_model(args, kwargs)
                    except Exception:
                        raise last_error

                attempt += 1

    def _log_retry(self, attempt: int, error_type: str,
                   error: Optional[Exception], strategy: str):
        """记录重试日志（内存中，可查询）"""
        self.retry_log.append({
            "attempt": attempt,
            "error_type": error_type,
            "error_msg": str(error) if error else None,
            "error_class": type(error).__name__ if error else None,
            "strategy": strategy,
        })

    def get_retry_report(self) -> dict:
        """重试统计报告"""
        if not self.retry_log:
            return {"total_retries": 0, "by_type": {}, "by_strategy": {}}

        by_type: Dict[str, int] = {}
        by_strategy: Dict[str, int] = {}
        for entry in self.retry_log:
            et = entry.get("error_type", "unknown")
            st = entry.get("strategy", "unknown")
            by_type[et] = by_type.get(et, 0) + 1
            by_strategy[st] = by_strategy.get(st, 0) + 1

        return {
            "total_retries": len(self.retry_log),
            "by_type": by_type,
            "by_strategy": by_strategy,
            "last_error": self.retry_log[-1] if self.retry_log else None,
        }


# ===========================================================================
# 2. FaultRecovery — 7种故障自动恢复配方
# ===========================================================================

class FaultRecovery:
    """故障诊断与恢复
    
    7种故障配方:
      1. 崩溃 (crash)          — P0, 需人工重启
      2. 超时 (timeout)        — P2, 可自动重试
      3. OOM (oom)             — P0, 需减少并发或增大内存
      4. 限频 (rate_limit)     — P2, 等待后自动重试
      5. 认证失败 (auth_fail)  — P1, 检查API Key
      6. 网络断 (network)      — P1, 等待后自动重试
      7. 依赖缺失 (dep_missing)— P1, 安装提示
    
    用法::
    
        fr = FaultRecovery()
        
        # 自动诊断
        diag = fr.diagnose("CUDA out of memory: tried to allocate 2GB")
        print(diag["fault_type"])    # "oom"
        print(diag["recovery"])      # 恢复建议
        
        # 获取恢复建议
        print(fr.suggest_recovery("rate_limit"))
        
        # 尝试自动恢复（仅支持auto_fix=True的故障）
        success = fr.auto_recover("rate_limit", {"wait_seconds": 30})
    """

    FAULT_HANDBOOK: Dict[str, dict] = {
        "crash": {
            "symptoms": ["连接失败", "502", "gateway", "ECONNREFUSED",
                         "connection refused", "gateway error", "crash",
                         "segfault", "core dumped"],
            "severity": "P0",
            "recovery": "进程崩溃，需要人工重启服务。检查崩溃日志定位根因。",
            "auto_fix": False,
            "category": "infrastructure",
        },
        "timeout": {
            "symptoms": ["超时", "timeout", "timed out",
                         "idleTimeout", "task timed out",
                         "deadline exceeded", "504"],
            "severity": "P2",
            "recovery": "操作超时。简化任务拆分，增加超时时间（建议>=600s），重试。",
            "auto_fix": True,
            "category": "agent",
        },
        "oom": {
            "symptoms": ["内存溢出", "heap", "OOM", "killed", "MemoryError",
                         "out of memory", "Cannot allocate memory",
                         "memory allocation failed", "CUDA out of memory",
                         "显存", "torch.cuda", "CUBLAS_STATUS_ALLOC_FAILED"],
            "severity": "P0",
            "recovery": "内存/显存不足。检查进程泄漏，减少并发，换小模型，或增大内存。",
            "auto_fix": False,
            "category": "resource",
        },
        "rate_limit": {
            "symptoms": ["429", "rate limit", "too many requests",
                         "throttle", "quota exceeded", "请求频率",
                         "throttl"],
            "severity": "P2",
            "recovery": "API限频。等待限流窗口重置（通常1-5分钟），减少并发请求数。",
            "auto_fix": True,
            "category": "api",
        },
        "auth_fail": {
            "symptoms": ["api key", "apikey", "unauthorized", "401",
                         "invalid key", "authentication failed",
                         "access denied", "forbidden", "403",
                         "expired", "revoked"],
            "severity": "P1",
            "recovery": "认证失败。检查API Key是否过期或配额用完，更新环境变量或配置文件。",
            "auto_fix": False,
            "category": "auth",
        },
        "network": {
            "symptoms": ["connection reset", "ECONNRESET", "broken pipe",
                         "network unreachable", "DNS resolution",
                         "Name or service not known", "getaddrinfo",
                         "ETIMEDOUT", "EHOSTUNREACH"],
            "severity": "P1",
            "recovery": "网络不稳定。检查网络连接、DNS配置、代理设置，短暂等待后重试。",
            "auto_fix": True,
            "category": "network",
        },
        "dep_missing": {
            "symptoms": ["ModuleNotFoundError", "ImportError",
                         "No module named", "command not found",
                         "not recognized", "is not recognized",
                         "找不到命令", "依赖缺失"],
            "severity": "P1",
            "recovery": "依赖缺失。安装缺失的包或工具，检查PATH环境变量。",
            "auto_fix": False,
            "category": "environment",
        },
    }

    def __init__(self):
        self._fault_log: List[dict] = []

    def diagnose(self, error_text: str) -> dict:
        """根据错误文本自动诊断故障类型
        
        Returns:
            {
                "fault_type": str,
                "severity": str,          # P0/P1/P2
                "category": str,
                "matched_symptoms": [str],
                "recovery": str,
                "auto_fix": bool,
                "confidence": float,      # 0-1
            }
        """
        error_lower = error_text.lower()
        best_match = None
        best_score = 0

        for fault_type, info in self.FAULT_HANDBOOK.items():
            matched = []
            for symptom in info["symptoms"]:
                if symptom.lower() in error_lower:
                    matched.append(symptom)

            if matched:
                score = len(matched) / len(info["symptoms"])
                if info["severity"] == "P0":
                    score *= 1.2
                if score > best_score:
                    best_score = score
                    best_match = {
                        "fault_type": fault_type,
                        "severity": info["severity"],
                        "category": info.get("category", "unknown"),
                        "matched_symptoms": matched,
                        "recovery": info["recovery"],
                        "auto_fix": info["auto_fix"],
                        "confidence": min(1.0, round(score, 3)),
                    }

        if best_match:
            return best_match

        return {
            "fault_type": "unknown",
            "severity": "P2",
            "category": "unknown",
            "matched_symptoms": [],
            "recovery": "未知故障。请检查完整错误日志排查。",
            "auto_fix": False,
            "confidence": 0.0,
        }

    def suggest_recovery(self, fault_type: str) -> str:
        """返回人类可读的恢复建议"""
        info = self.FAULT_HANDBOOK.get(fault_type)
        if not info:
            return f"未知故障类型: {fault_type}。请检查错误日志。"

        lines = [
            f"🔧 故障恢复 [{fault_type}]",
            f"严重级别: {info['severity']}",
            f"分类: {info.get('category', 'unknown')}",
            f"恢复方案: {info['recovery']}",
            f"自动修复: {'✅ 支持' if info['auto_fix'] else '❌ 需人工'}",
        ]
        return "\n".join(lines)

    def auto_recover(self, fault_type: str,
                     context: Optional[dict] = None) -> bool:
        """尝试自动恢复（仅auto_fix=True的故障）
        
        支持的自动恢复:
          - timeout:    记录超时，标记可重试
          - rate_limit: 等待指定秒数后标记可重试
          - network:    等待5秒后标记可重试
        
        Returns:
            是否恢复成功
        """
        info = self.FAULT_HANDBOOK.get(fault_type)
        if not info or not info.get("auto_fix"):
            self.log_fault(fault_type,
                           f"Auto-fix not available for {fault_type}",
                           auto_recovered=False)
            return False

        context = context or {}

        try:
            if fault_type == "timeout":
                self.log_fault("timeout",
                               "Timeout recorded. Suggest: simplify task, increase timeout",
                               auto_recovered=True)
                return True

            elif fault_type == "rate_limit":
                wait_seconds = min(context.get("wait_seconds", 60), 120)
                time.sleep(wait_seconds)
                self.log_fault("rate_limit",
                               f"Waited {wait_seconds}s for rate limit reset",
                               auto_recovered=True)
                return True

            elif fault_type == "network":
                time.sleep(5)
                self.log_fault("network",
                               "Waited 5s for network recovery",
                               auto_recovered=True)
                return True

            else:
                self.log_fault(fault_type,
                               f"No auto-recovery handler for {fault_type}",
                               auto_recovered=False)
                return False

        except Exception as e:
            self.log_fault(fault_type,
                           f"Auto-recovery failed: {type(e).__name__}: {e}",
                           auto_recovered=False)
            return False

    def log_fault(self, fault_type: str, context: str,
                  auto_recovered: bool = False):
        """记录故障到内存日志"""
        self._fault_log.append({
            "fault_type": fault_type,
            "severity": self.FAULT_HANDBOOK.get(fault_type, {}).get("severity", "P2"),
            "context": context,
            "auto_recovered": auto_recovered,
        })

    def get_fault_history(self, limit: int = 20) -> List[dict]:
        """获取故障历史"""
        return self._fault_log[-limit:]

    def get_fault_summary(self) -> dict:
        """故障统计摘要"""
        if not self._fault_log:
            return {"total": 0, "by_type": {}, "by_severity": {},
                    "auto_recovered": 0, "manual_required": 0}

        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        auto_recovered = 0
        manual_required = 0

        for r in self._fault_log:
            ft = r.get("fault_type", "unknown")
            sv = r.get("severity", "P2")
            by_type[ft] = by_type.get(ft, 0) + 1
            by_severity[sv] = by_severity.get(sv, 0) + 1
            if r.get("auto_recovered"):
                auto_recovered += 1
            else:
                manual_required += 1

        return {
            "total": len(self._fault_log),
            "by_type": by_type,
            "by_severity": by_severity,
            "auto_recovered": auto_recovered,
            "manual_required": manual_required,
        }


# ===========================================================================
# 3. ModelSelector — 按任务类型选最优模型
# ===========================================================================

class ModelSelector:
    """模型智能选择器
    
    核心逻辑:
      - 简单任务 → 小模型（快、便宜、够用）
      - 复杂任务 → 大模型（准确、深度、创造性）
      - 代码任务 → 代码模型
    
    级联策略:
      1. 先用小模型尝试
      2. 校验失败 → 升级到大模型
      3. 大模型也不行 → 通知人类
    
    用法::
    
        selector = ModelSelector()
        
        # 按任务选模型
        model = selector.select("format")       # → "qwen-turbo" (小模型)
        model = selector.select("novel")         # → "qwen-max" (大模型)
        model = selector.select("qa", "high")    # → "qwen-max" (强制大模型)
        
        # 级联执行
        result = selector.cascade(
            task=lambda model: call_api(model, prompt),
            validator=lambda r: "error" not in r,
        )
    """

    # 默认模型配置
    DEFAULT_MODELS = {
        "small": {
            "name": "qwen-turbo",
            "cost_per_1k": 0.001,
            "speed": "fast",
            "max_tokens": 8000,
        },
        "medium": {
            "name": "qwen-plus",
            "cost_per_1k": 0.004,
            "speed": "medium",
            "max_tokens": 32000,
        },
        "large": {
            "name": "qwen-max",
            "cost_per_1k": 0.02,
            "speed": "slow",
            "max_tokens": 32000,
        },
    }

    # 任务类型 → 推荐模型大小
    TASK_MODEL_MAP = {
        # 小模型任务
        "format": "small",
        "classify": "small",
        "extract": "small",
        "summarize_short": "small",
        "translate_simple": "small",
        "single_function": "small",
        "qa_simple": "small",
        # 中模型任务
        "summarize": "medium",
        "translate": "medium",
        "write_short": "medium",
        "code_review": "medium",
        "qa": "medium",
        "edit": "medium",
        # 大模型任务
        "reason": "large",
        "write_long": "large",
        "create": "large",
        "architecture": "large",
        "multi_turn": "large",
        "decision": "large",
        "novel": "large",
        "complex_code": "large",
    }

    def __init__(self, models: Optional[Dict[str, dict]] = None):
        """
        Args:
            models: 自定义模型配置，覆盖DEFAULT_MODELS
        """
        self.models = models or dict(self.DEFAULT_MODELS)

    def select(self, task_type: str, complexity: str = "auto") -> str:
        """根据任务类型和复杂度选择模型
        
        Args:
            task_type: 任务类型（见 TASK_MODEL_MAP 的key）
            complexity: "low" / "medium" / "high" / "auto"
        
        Returns:
            模型名称（如 "qwen-turbo"）
        """
        size = self.TASK_MODEL_MAP.get(task_type, "medium")

        if complexity != "auto":
            complexity_map = {"low": "small", "medium": "medium", "high": "large"}
            complexity_size = complexity_map.get(complexity, "medium")
            size_order = ["small", "medium", "large"]
            size_idx = max(size_order.index(size),
                          size_order.index(complexity_size))
            size = size_order[size_idx]

        model_info = self.models.get(size, self.models.get("medium", {}))
        return model_info.get("name", "qwen-plus")

    def cascade(self, task: Callable, validator: Callable,
                models: Optional[List[str]] = None) -> dict:
        """级联策略：小 → 中 → 大 → 报告失败
        
        Args:
            task: Callable(model_name) → result
            validator: Callable(result) → bool
            models: 模型列表（默认从小到大）
        
        Returns:
            {
                "success": bool,
                "model_used": str or None,
                "result": Any,
                "attempts": [{model, success, error}],
            }
        """
        if models is None:
            models = [
                self.models["small"]["name"],
                self.models["medium"]["name"],
                self.models["large"]["name"],
            ]

        attempts = []
        for model_name in models:
            try:
                result = task(model_name)
                is_valid = validator(result)

                attempts.append({
                    "model": model_name,
                    "success": is_valid,
                    "error": None if is_valid else "validation_failed",
                })

                if is_valid:
                    return {
                        "success": True,
                        "model_used": model_name,
                        "result": result,
                        "attempts": attempts,
                    }

            except Exception as e:
                attempts.append({
                    "model": model_name,
                    "success": False,
                    "error": f"{type(e).__name__}: {e}",
                })

        return {
            "success": False,
            "model_used": None,
            "result": None,
            "attempts": attempts,
        }

    def estimate_cost(self, token_count: int, model: str) -> float:
        """估算调用成本（元）
        
        Args:
            token_count: token数量
            model: 模型名
        
        Returns:
            估算成本（元）
        """
        model_info = None
        for size_info in self.models.values():
            if size_info["name"] == model:
                model_info = size_info
                break
        if model_info is None:
            model_info = self.models.get("medium", {"cost_per_1k": 0.004})

        cost = (token_count / 1000) * model_info["cost_per_1k"]
        return round(cost, 6)

    def get_model_info(self, model_name: str) -> Optional[dict]:
        """获取模型详细信息"""
        for size, info in self.models.items():
            if info["name"] == model_name:
                return {"size": size, **info}
        return None

    def list_models(self) -> List[dict]:
        """列出所有可用模型"""
        return [{"size": size, **info} for size, info in self.models.items()]
