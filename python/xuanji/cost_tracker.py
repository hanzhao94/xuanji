"""
xuanji 成本看板

追踪LLM调用的Token消耗和费用。
内置各主流模型价格表，支持按Agent/模型/日期统计。
零外部依赖，仅使用标准库。
"""

import json
import time
import threading
import logging
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ─── 模型价格表（每百万Token，单位USD）──────────────────────

MODEL_PRICES: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},

    # Anthropic
    "claude-4-opus": {"input": 15.00, "output": 75.00},
    "claude-4-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3.7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3.5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},

    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},

    # DeepSeek
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-v3": {"input": 0.14, "output": 0.28},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
    "deepseek-coder": {"input": 0.14, "output": 0.28},

    # 通义千问
    "qwen-turbo": {"input": 0.30, "output": 0.60},
    "qwen-plus": {"input": 0.80, "output": 2.00},
    "qwen-max": {"input": 2.00, "output": 6.00},
    "qwen-long": {"input": 0.50, "output": 2.00},
    "qwen3-235b": {"input": 4.00, "output": 16.00},
    "qwen3.5-plus": {"input": 0.80, "output": 2.00},

    # Meta
    "llama-3.1-405b": {"input": 3.00, "output": 3.00},
    "llama-3.1-70b": {"input": 0.80, "output": 0.80},
    "llama-3.1-8b": {"input": 0.10, "output": 0.10},

    # Mistral
    "mistral-large": {"input": 2.00, "output": 6.00},
    "mistral-medium": {"input": 2.70, "output": 8.10},
    "mistral-small": {"input": 0.20, "output": 0.60},
}


# ─── 调用记录 ───────────────────────────────────────────────

class CallRecord:
    """一次LLM调用记录"""

    __slots__ = ("agent", "model", "input_tokens", "output_tokens",
                 "cost_usd", "timestamp", "metadata")

    def __init__(self, agent: str, model: str, input_tokens: int,
                 output_tokens: int, cost_usd: float, metadata: Optional[Dict] = None):
        self.agent = agent
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.timestamp = time.time()
        self.metadata = metadata or {}

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def date_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d")

    def to_dict(self) -> Dict:
        return {
            "agent": self.agent,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "timestamp": self.timestamp,
            "date": self.date_str,
            "metadata": self.metadata,
        }


# ─── CostTracker 主类 ──────────────────────────────────────

class CostTracker:
    """成本追踪器
    
    用法::
    
        tracker = CostTracker()
        
        # 记录调用
        tracker.record("assistant", "gpt-4o", 1000, 500)
        tracker.record("coder", "claude-3.5-sonnet", 2000, 1000)
        
        # 日报
        report = tracker.daily_report()
        
        # 按Agent统计
        by_agent = tracker.by_agent()
        
        # 按模型统计
        by_model = tracker.by_model()
        
        # 最贵的调用
        expensive = tracker.top_expensive(5)
    """

    def __init__(self, max_records: int = 100000,
                 custom_prices: Optional[Dict[str, Dict[str, float]]] = None):
        """
        Args:
            max_records: 最大记录数
            custom_prices: 自定义模型价格表（覆盖内置）
        """
        self._records: List[CallRecord] = []
        self._max_records = max_records
        self._lock = threading.Lock()

        # 合并价格表
        self._prices = dict(MODEL_PRICES)
        if custom_prices:
            self._prices.update(custom_prices)

    def _calc_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """计算费用（USD）"""
        # 尝试精确匹配
        prices = self._prices.get(model)

        # 尝试模糊匹配（去掉版本号后缀等）
        if prices is None:
            model_lower = model.lower()
            for key, val in self._prices.items():
                if key.lower() in model_lower or model_lower in key.lower():
                    prices = val
                    break

        if prices is None:
            logger.warning(f"未知模型 '{model}'，使用默认价格 $1.00/1M tokens")
            prices = {"input": 1.00, "output": 1.00}

        input_cost = (input_tokens / 1_000_000) * prices["input"]
        output_cost = (output_tokens / 1_000_000) * prices["output"]
        return input_cost + output_cost

    def add_model_price(self, model: str, input_per_m: float, output_per_m: float) -> None:
        """添加/更新模型价格
        
        Args:
            model: 模型名
            input_per_m: 输入价格（每百万Token，USD）
            output_per_m: 输出价格（每百万Token，USD）
        """
        self._prices[model] = {"input": input_per_m, "output": output_per_m}

    def record(self, agent: str, model: str, input_tokens: int,
               output_tokens: int, metadata: Optional[Dict] = None) -> CallRecord:
        """记录一次LLM调用
        
        Args:
            agent: Agent名称
            model: 模型名称
            input_tokens: 输入Token数
            output_tokens: 输出Token数
            metadata: 额外元数据
        
        Returns:
            CallRecord
        """
        cost = self._calc_cost(model, input_tokens, output_tokens)
        record = CallRecord(agent, model, input_tokens, output_tokens, cost, metadata)

        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records:]

        return record

    def _filter_records(self, start: Optional[float] = None,
                        end: Optional[float] = None,
                        agent: Optional[str] = None,
                        model: Optional[str] = None) -> List[CallRecord]:
        """过滤记录"""
        with self._lock:
            records = list(self._records)

        if start:
            records = [r for r in records if r.timestamp >= start]
        if end:
            records = [r for r in records if r.timestamp <= end]
        if agent:
            records = [r for r in records if r.agent == agent]
        if model:
            records = [r for r in records if r.model == model]
        return records

    def _aggregate(self, records: List[CallRecord]) -> Dict:
        """汇总统计"""
        if not records:
            return {
                "total_calls": 0, "total_input_tokens": 0,
                "total_output_tokens": 0, "total_tokens": 0,
                "total_cost_usd": 0, "avg_cost_usd": 0,
            }
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)
        total_cost = sum(r.cost_usd for r in records)
        return {
            "total_calls": len(records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_usd": round(total_cost, 4),
            "avg_cost_usd": round(total_cost / len(records), 6),
        }

    def daily_report(self, date: Optional[str] = None) -> Dict:
        """日报
        
        Args:
            date: 日期字符串 (YYYY-MM-DD)，默认今天
        
        Returns:
            日报数据
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"error": f"日期格式错误: {date}"}

        start = dt.timestamp()
        end = (dt + timedelta(days=1)).timestamp()
        records = self._filter_records(start=start, end=end)

        report = self._aggregate(records)
        report["date"] = date
        report["by_agent"] = {}
        report["by_model"] = {}

        # 按Agent分组
        agent_groups: Dict[str, List[CallRecord]] = defaultdict(list)
        for r in records:
            agent_groups[r.agent].append(r)
        for ag, recs in agent_groups.items():
            report["by_agent"][ag] = self._aggregate(recs)

        # 按Model分组
        model_groups: Dict[str, List[CallRecord]] = defaultdict(list)
        for r in records:
            model_groups[r.model].append(r)
        for md, recs in model_groups.items():
            report["by_model"][md] = self._aggregate(recs)

        return report

    def weekly_report(self) -> Dict:
        """周报（最近7天）"""
        now = datetime.now()
        start_dt = now - timedelta(days=7)
        records = self._filter_records(start=start_dt.timestamp())

        report = self._aggregate(records)
        report["period"] = f"{start_dt.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}"

        # 每日趋势
        daily = {}
        for r in records:
            d = r.date_str
            if d not in daily:
                daily[d] = {"calls": 0, "tokens": 0, "cost_usd": 0}
            daily[d]["calls"] += 1
            daily[d]["tokens"] += r.total_tokens
            daily[d]["cost_usd"] = round(daily[d]["cost_usd"] + r.cost_usd, 4)

        report["daily_trend"] = dict(sorted(daily.items()))
        return report

    def by_agent(self) -> Dict[str, Dict]:
        """按Agent统计"""
        with self._lock:
            records = list(self._records)

        groups: Dict[str, List[CallRecord]] = defaultdict(list)
        for r in records:
            groups[r.agent].append(r)

        result = {}
        for agent, recs in sorted(groups.items()):
            agg = self._aggregate(recs)
            agg["models_used"] = list(set(r.model for r in recs))
            result[agent] = agg
        return result

    def by_model(self) -> Dict[str, Dict]:
        """按模型统计"""
        with self._lock:
            records = list(self._records)

        groups: Dict[str, List[CallRecord]] = defaultdict(list)
        for r in records:
            groups[r.model].append(r)

        result = {}
        for model, recs in sorted(groups.items(), key=lambda x: -sum(r.cost_usd for r in x[1])):
            agg = self._aggregate(recs)
            prices = self._prices.get(model, {})
            agg["price_input_per_m"] = prices.get("input", "unknown")
            agg["price_output_per_m"] = prices.get("output", "unknown")
            result[model] = agg
        return result

    def top_expensive(self, limit: int = 10) -> List[Dict]:
        """最贵的调用
        
        Args:
            limit: 返回数量
        
        Returns:
            按费用倒序排列的调用列表
        """
        with self._lock:
            records = list(self._records)

        records.sort(key=lambda r: r.cost_usd, reverse=True)
        return [r.to_dict() for r in records[:limit]]

    def total_cost(self) -> float:
        """总费用（USD）"""
        with self._lock:
            return round(sum(r.cost_usd for r in self._records), 4)

    def summary(self) -> Dict:
        """总览摘要"""
        with self._lock:
            records = list(self._records)

        agg = self._aggregate(records)
        agg["record_count"] = len(records)
        agg["unique_agents"] = len(set(r.agent for r in records))
        agg["unique_models"] = len(set(r.model for r in records))

        if records:
            agg["first_record"] = records[0].date_str
            agg["last_record"] = records[-1].date_str
        return agg

    def export(self) -> List[Dict]:
        """导出所有记录"""
        with self._lock:
            return [r.to_dict() for r in self._records]

    def export_json(self, indent: int = 2) -> str:
        """导出为JSON字符串"""
        return json.dumps(self.export(), ensure_ascii=False, indent=indent, default=str)

    def list_prices(self) -> Dict[str, Dict[str, float]]:
        """列出所有模型价格"""
        return dict(self._prices)
