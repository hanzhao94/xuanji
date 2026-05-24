"""xuanji Token预算治理

精细预算管理，支持按Agent/按模型/按时间段的预算分配、
预算预警、自动模型降级、成本优化建议。

零外部依赖，纯Python标准库。
"""

import json
import os
import time
from collections import defaultdict
from typing import Optional, List, Dict, Any

# ============================================================
# 常量
# ============================================================

PERIOD_DAILY = "daily"
PERIOD_MONTHLY = "monthly"
PERIOD_PER_TASK = "per_task"
PERIOD_UNLIMITED = "unlimited"

BUDGET_OK = "ok"
BUDGET_WARNING = "warning"       # >80%
BUDGET_CRITICAL = "critical"     # >95%
BUDGET_BLOCKED = "blocked"       # >=100%

# 默认模型降级链
DEFAULT_FALLBACK_CHAIN = [
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen-turbo-mini",
]

# 默认定价（每1000 token，单位：元）
DEFAULT_PRICING = {
    "qwen-max":        {"input": 0.02,  "output": 0.06},
    "qwen-plus":       {"input": 0.008, "output": 0.02},
    "qwen-turbo":      {"input": 0.003, "output": 0.006},
    "qwen-turbo-mini": {"input": 0.001, "output": 0.002},
    "_default":        {"input": 0.01,  "output": 0.03},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _this_month() -> str:
    return time.strftime("%Y-%m")
class AdvancedGovernor:
    """Token预算治理高级版
    
    相比基础governor.py的增强:
      - 四级作用域: global / project:xxx / agent:xxx / task:xxx
      - 精细预算周期: daily / monthly / per_task / unlimited
      - 自动模型降级: 预算紧张时自动选更便宜的模型
      - 消耗趋势预测: 基于历史预测未来成本
      - 成本优化建议: 分析可降级的调用
    """
    
    def __init__(self, data_dir: str = ""):
        """
        Args:
            data_dir: 数据持久化目录，空字符串则纯内存模式
        """
        self.data_dir = data_dir
        self._budgets: Dict[str, dict] = {}
        # scope → list of usage records
        self._usage: Dict[str, List[dict]] = defaultdict(list)
        self._fallback_chain: List[str] = list(DEFAULT_FALLBACK_CHAIN)
        self._pricing: Dict[str, Dict[str, float]] = dict(DEFAULT_PRICING)
        
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self._load_data()
    
    # ============================
    # 持久化
    # ============================
    
    def _load_data(self):
        """从文件加载预算和使用记录"""
        budget_file = os.path.join(self.data_dir, "adv_budgets.jsonl")
        if os.path.exists(budget_file):
            try:
                with open(budget_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        scope = data.get("scope", "")
                        if scope:
                            self._budgets[scope] = data
            except (json.JSONDecodeError, IOError):
                pass
        
        usage_file = os.path.join(self.data_dir, "adv_usage.jsonl")
        if os.path.exists(usage_file):
            try:
                with open(usage_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        scope = data.get("scope", "")
                        if scope:
                            self._usage[scope].append(data)
            except (json.JSONDecodeError, IOError):
                pass
    
    def _append_jsonl(self, filename: str, record: dict):
        """追加一条记录"""
        if not self.data_dir:
            return
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    
    # ============================
    # 预算管理
    # ============================
    
    def set_budget(self, scope: str, budget: int,
                   period: str = PERIOD_DAILY,
                   warning_threshold: float = 0.80,
                   critical_threshold: float = 0.95,
                   hard_limit: bool = True):
        """设置预算
        
        Args:
            scope: 作用域
                "global"         — 全局
                "project:xxx"    — 项目级
                "agent:xxx"      — Agent级
                "task:xxx"       — 任务级
            budget: token数上限
            period: "daily" / "monthly" / "per_task" / "unlimited"
            warning_threshold: 警告阈值（0-1，默认0.80）
            critical_threshold: 严重阈值（0-1，默认0.95）
            hard_limit: True=硬停止，False=仅警告
        """
        if budget <= 0:
            raise ValueError("Budget must be positive")
        
        config = {
            "scope": scope,
            "budget": budget,
            "period": period,
            "warning_threshold": warning_threshold,
            "critical_threshold": critical_threshold,
            "hard_limit": hard_limit,
            "created_at": _now(),
        }
        self._budgets[scope] = config
        self._append_jsonl("adv_budgets.jsonl", config)
    
    def get_budget(self, scope: str) -> Optional[Dict]:
        """获取预算配置"""
        return self._budgets.get(scope)
    
    def list_budgets(self) -> List[Dict]:
        """列出所有预算"""
        return list(self._budgets.values())
    
    def check_budget(self, scope: str) -> Dict:
        """检查预算状态
        
        Returns:
            {
                "scope": str,
                "budget": int,
                "used": int,
                "remaining": int,
                "pct": float,         # 使用百分比
                "status": str,        # ok/warning/critical/blocked
                "model_override": str,# 推荐使用的模型（可能已降级）
                "hard_limit": bool,
            }
        """
        config = self._budgets.get(scope)
        if not config:
            return {
                "scope": scope, "budget": 0, "used": 0,
                "remaining": 0, "pct": 0.0,
                "status": "no_budget_set",
                "model_override": "", "hard_limit": False,
            }
        
        used = self._get_period_usage(scope, config["period"])
        remaining = max(0, config["budget"] - used)
        pct = used / config["budget"] if config["budget"] > 0 else 0.0
        
        if pct >= 1.0:
            status = BUDGET_BLOCKED
        elif pct >= config["critical_threshold"]:
            status = BUDGET_CRITICAL
        elif pct >= config["warning_threshold"]:
            status = BUDGET_WARNING
        else:
            status = BUDGET_OK
        
        model_override = self.select_model(scope, 0)
        
        return {
            "scope": scope,
            "budget": config["budget"],
            "used": used,
            "remaining": remaining,
            "pct": round(pct, 4),
            "status": status,
            "model_override": model_override,
            "hard_limit": config["hard_limit"],
        }
    
    def record_usage(self, scope: str, tokens_in: int, tokens_out: int,
                     model: str, agent_id: str = "",
                     metadata: Optional[Dict] = None):
        """记录token消耗"""
        cost = self._calculate_cost(model, tokens_in, tokens_out)
        
        record = {
            "scope": scope,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "cost": cost,
            "timestamp": _now(),
            "date": _today(),
            "agent_id": agent_id,
            "metadata": metadata or {},
        }
        
        self._usage[scope].append(record)
        self._append_jsonl("adv_usage.jsonl", record)
    
    def enforce(self, scope: str, estimated_tokens: int) -> Dict:
        """强制执行：能不能花这么多token？
        
        Args:
            scope: 作用域
            estimated_tokens: 预估需要的token数
            
        Returns:
            {
                "allowed": bool,
                "model_override": str,  # 空=用原模型，非空=降级到此模型
                "reason": str,
                "remaining_after": int,
                "status": str,
            }
        """
        config = self._budgets.get(scope)
        if not config:
            return {
                "allowed": True, "model_override": "",
                "reason": "no_budget_set",
                "remaining_after": -1, "status": BUDGET_OK,
            }
        
        used = self._get_period_usage(scope, config["period"])
        remaining = config["budget"] - used
        remaining_after = remaining - estimated_tokens
        pct_after = (used + estimated_tokens) / config["budget"] if config["budget"] > 0 else 0.0
        
        # 已经超了
        if remaining <= 0:
            return {
                "allowed": not config["hard_limit"],
                "model_override": "",
                "reason": f"budget_exhausted (used={used}, budget={config['budget']})",
                "remaining_after": remaining_after,
                "status": BUDGET_BLOCKED,
            }
        
        # 执行后会超
        if remaining_after < 0 and config["hard_limit"]:
            model_override = self.select_model(scope, estimated_tokens)
            if model_override and model_override != self._fallback_chain[0]:
                return {
                    "allowed": True,
                    "model_override": model_override,
                    "reason": f"model_downgraded (remaining={remaining})",
                    "remaining_after": remaining_after,
                    "status": BUDGET_CRITICAL,
                }
            return {
                "allowed": False, "model_override": "",
                "reason": f"would_exceed_budget (remaining={remaining})",
                "remaining_after": remaining_after,
                "status": BUDGET_BLOCKED,
            }
        
        # 接近上限
        if pct_after >= config["critical_threshold"]:
            return {
                "allowed": True,
                "model_override": self.select_model(scope, estimated_tokens),
                "reason": "approaching_limit",
                "remaining_after": remaining_after,
                "status": BUDGET_CRITICAL,
            }
        
        if pct_after >= config["warning_threshold"]:
            return {
                "allowed": True, "model_override": "",
                "reason": "warning_zone",
                "remaining_after": remaining_after,
                "status": BUDGET_WARNING,
            }
        
        return {
            "allowed": True, "model_override": "",
            "reason": "within_budget",
            "remaining_after": remaining_after,
            "status": BUDGET_OK,
        }
    
    # ============================
    # 模型降级链
    # ============================
    
    def set_fallback_chain(self, chain: List[str]):
        """设置模型降级链（从贵到便宜）"""
        if not chain:
            raise ValueError("Fallback chain cannot be empty")
        self._fallback_chain = list(chain)
    
    def get_fallback_chain(self) -> List[str]:
        """获取当前降级链"""
        return list(self._fallback_chain)
    
    def select_model(self, scope: str, estimated_tokens: int) -> str:
        """根据剩余预算自动选择模型
        
        策略:
          - 预算>60% → 用最好的
          - 40-60%   → 降一级
          - 20-40%   → 降两级
          - <20%     → 用最便宜的
        """
        if not self._fallback_chain:
            return ""
        
        config = self._budgets.get(scope)
        if not config:
            return self._fallback_chain[0]
        
        used = self._get_period_usage(scope, config["period"])
        remaining_pct = 1.0 - (used / config["budget"]) if config["budget"] > 0 else 1.0
        
        chain_len = len(self._fallback_chain)
        if remaining_pct > 0.6:
            idx = 0
        elif remaining_pct > 0.4:
            idx = min(1, chain_len - 1)
        elif remaining_pct > 0.2:
            idx = min(2, chain_len - 1)
        else:
            idx = chain_len - 1
        
        return self._fallback_chain[idx]
    
    # ============================
    # 定价
    # ============================
    
    def set_pricing(self, model: str, input_per_1k: float, output_per_1k: float):
        """设置模型定价（每1000 token，元）"""
        self._pricing[model] = {"input": input_per_1k, "output": output_per_1k}
    
    def get_pricing(self, model: str) -> Dict[str, float]:
        """获取模型定价"""
        return self._pricing.get(model,
                                 self._pricing.get("_default",
                                                   {"input": 0.01, "output": 0.03}))
    
    # ============================
    # 报告 & 预测
    # ============================
    
    def daily_report(self, date: Optional[str] = None) -> str:
        """每日token消耗报告"""
        target_date = date or _today()
        parts = [f"# Token消耗日报 — {target_date}\n"]
        
        scope_stats: Dict[str, Dict] = {}
        for scope, records in self._usage.items():
            day_records = [r for r in records if r.get("date") == target_date]
            if not day_records:
                continue
            total_in = sum(r.get("tokens_in", 0) for r in day_records)
            total_out = sum(r.get("tokens_out", 0) for r in day_records)
            total_cost = sum(r.get("cost", 0) for r in day_records)
            models_used = set(r.get("model", "?") for r in day_records)
            
            scope_stats[scope] = {
                "calls": len(day_records),
                "tokens_in": total_in,
                "tokens_out": total_out,
                "total_tokens": total_in + total_out,
                "cost": total_cost,
                "models": list(models_used),
            }
        
        if not scope_stats:
            parts.append("今日无token消耗记录。")
            return "\n".join(parts)
        
        grand_total = sum(s["total_tokens"] for s in scope_stats.values())
        grand_cost = sum(s["cost"] for s in scope_stats.values())
        grand_calls = sum(s["calls"] for s in scope_stats.values())
        
        parts.append(f"## 总计")
        parts.append(f"- 总调用: {grand_calls}")
        parts.append(f"- 总Token: {grand_total:,}")
        parts.append(f"- 总成本: ￥{grand_cost:.4f}\n")
        
        parts.append(f"## 分项明细")
        for scope, stats in sorted(scope_stats.items()):
            budget_info = ""
            config = self._budgets.get(scope)
            if config:
                pct = stats["total_tokens"] / config["budget"] * 100 if config["budget"] > 0 else 0
                budget_info = f" | 预算: {stats['total_tokens']:,}/{config['budget']:,} ({pct:.1f}%)"
            
            parts.append(f"### {scope}{budget_info}")
            parts.append(f"- 调用: {stats['calls']}")
            parts.append(f"- Input: {stats['tokens_in']:,} / Output: {stats['tokens_out']:,}")
            parts.append(f"- 成本: ￥{stats['cost']:.4f}")
            parts.append(f"- 模型: {', '.join(stats['models'])}\n")
        
        return "\n".join(parts)
    
    def cost_forecast(self, days: int = 30) -> Dict:
        """成本预测（基于最近7天平均消耗）
        
        Returns:
            {
                "daily_avg_tokens": int,
                "daily_avg_cost": float,
                "forecast_days": int,
                "forecast_total_tokens": int,
                "forecast_total_cost": float,
                "trend": "increasing" | "decreasing" | "stable",
            }
        """
        daily_stats: Dict[str, Dict] = {}
        for scope, records in self._usage.items():
            for r in records:
                d = r.get("date", "")
                if d not in daily_stats:
                    daily_stats[d] = {"tokens": 0, "cost": 0.0}
                daily_stats[d]["tokens"] += r.get("tokens_in", 0) + r.get("tokens_out", 0)
                daily_stats[d]["cost"] += r.get("cost", 0)
        
        if not daily_stats:
            return {
                "daily_avg_tokens": 0, "daily_avg_cost": 0.0,
                "forecast_days": days,
                "forecast_total_tokens": 0, "forecast_total_cost": 0.0,
                "trend": "stable",
            }
        
        sorted_days = sorted(daily_stats.keys(), reverse=True)[:7]
        n = len(sorted_days)
        
        total_tokens = sum(daily_stats[d]["tokens"] for d in sorted_days)
        total_cost = sum(daily_stats[d]["cost"] for d in sorted_days)
        
        avg_tokens = total_tokens // n
        avg_cost = total_cost / n
        
        # 趋势判断
        trend = "stable"
        if n >= 4:
            mid = n // 2
            first_half = sum(daily_stats[d]["tokens"] for d in sorted_days[:mid]) / mid
            second_half = sum(daily_stats[d]["tokens"] for d in sorted_days[mid:]) / (n - mid)
            if first_half > second_half * 1.2:
                trend = "increasing"
            elif second_half > first_half * 1.2:
                trend = "decreasing"
        
        return {
            "daily_avg_tokens": avg_tokens,
            "daily_avg_cost": round(avg_cost, 4),
            "forecast_days": days,
            "forecast_total_tokens": avg_tokens * days,
            "forecast_total_cost": round(avg_cost * days, 4),
            "trend": trend,
        }
    
    def top_consumers(self, n: int = 5) -> List[Dict]:
        """最大token消耗者"""
        scope_totals: Dict[str, Dict] = {}
        for scope, records in self._usage.items():
            total_tokens = sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in records)
            total_cost = sum(r.get("cost", 0) for r in records)
            scope_totals[scope] = {
                "scope": scope,
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "calls": len(records),
            }
        
        sorted_scopes = sorted(scope_totals.values(),
                               key=lambda x: x["total_tokens"], reverse=True)
        return sorted_scopes[:n]
    
    def optimization_suggestions(self) -> List[Dict]:
        """成本优化建议
        
        分析哪些调用可以用更便宜的模型。
        
        Returns:
            [{"scope": ..., "current_model": ..., "suggested_model": ...,
              "potential_saving_pct": ..., "reason": ...}]
        """
        suggestions = []
        
        # 分析每个scope的模型使用
        for scope, records in self._usage.items():
            if not records:
                continue
            
            # 按模型分组
            model_usage: Dict[str, Dict] = {}
            for r in records:
                model = r.get("model", "unknown")
                if model not in model_usage:
                    model_usage[model] = {"calls": 0, "tokens": 0, "cost": 0.0}
                model_usage[model]["calls"] += 1
                model_usage[model]["tokens"] += r.get("tokens_in", 0) + r.get("tokens_out", 0)
                model_usage[model]["cost"] += r.get("cost", 0)
            
            # 找用贵模型做简单事的情况
            for model, stats in model_usage.items():
                pricing = self.get_pricing(model)
                # 如果平均每次调用token很少（<500），可能是简单任务
                avg_tokens = stats["tokens"] / stats["calls"] if stats["calls"] > 0 else 0
                
                if avg_tokens < 500 and pricing["input"] > 0.005:
                    # 简单任务用了贵模型
                    cheap = self._fallback_chain[-1] if self._fallback_chain else "qwen-turbo"
                    cheap_pricing = self.get_pricing(cheap)
                    saving = 1.0 - (cheap_pricing["input"] / pricing["input"]) if pricing["input"] > 0 else 0
                    
                    if saving > 0.3:  # 能省30%以上才建议
                        suggestions.append({
                            "scope": scope,
                            "current_model": model,
                            "suggested_model": cheap,
                            "potential_saving_pct": round(saving * 100, 1),
                            "reason": f"平均{avg_tokens:.0f} tokens/次，可用更便宜的模型",
                            "affected_calls": stats["calls"],
                        })
        
        suggestions.sort(key=lambda x: x.get("potential_saving_pct", 0), reverse=True)
        return suggestions
    
    # ============================
    # 内部方法
    # ============================
    
    def _get_period_usage(self, scope: str, period: str) -> int:
        """获取指定周期内的token使用量"""
        records = self._usage.get(scope, [])
        if not records:
            return 0
        
        if period == PERIOD_UNLIMITED or period == PERIOD_PER_TASK:
            return sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in records)
        
        if period == PERIOD_DAILY:
            today = _today()
            return sum(r.get("tokens_in", 0) + r.get("tokens_out", 0)
                       for r in records if r.get("date") == today)
        
        if period == PERIOD_MONTHLY:
            month = _this_month()
            return sum(r.get("tokens_in", 0) + r.get("tokens_out", 0)
                       for r in records
                       if r.get("date", "").startswith(month))
        
        return 0
    
    def _calculate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """计算成本（input/output分开计价）"""
        pricing = self.get_pricing(model)
        input_cost = tokens_in * pricing["input"] / 1000
        output_cost = tokens_out * pricing["output"] / 1000
        return round(input_cost + output_cost, 6)


# ============================================================
# TimeTravel — 时间旅行调试
# ============================================================
