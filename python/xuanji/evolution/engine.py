"""
xuanji 进化引擎

Agent从每次任务中进化——不只是记忆，是变强。

核心流程：
  1. start_round()  — 开始一轮进化实验（记录问题+基线）
  2. record_result() — 记录实验结果，自动计算delta
  3. decide()       — 锁定（采纳）或否决（回滚）
  4. best_params()  — 查询当前最优参数
  5. history()      — 查看进化历史
  6. summary()      — 项目进化摘要

数据格式: JSONL (append-only)
零外部依赖，纯Python标准库。

# 核心逻辑提炼自开源工程实践
"""

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

_CST = timezone(timedelta(hours=8))


# ============================================================
# 数据类
# ============================================================

@dataclass
class EvolutionRound:
    """一轮进化实验的完整记录"""
    id: str                 # uuid
    project: str            # 项目名
    round_num: int          # 第几轮
    problem: str            # 发现的问题
    experiment: str         # 做了什么实验
    variable_changed: str   # 只改了哪个变量
    baseline: dict          # 基线数据 {metric: value}
    result: dict            # 实验数据 {metric: value}
    delta: dict             # 变化 {metric: {abs: x, pct: y}}
    decision: str           # "locked" / "rejected" / "pending"
    reason: str             # 决策原因
    timestamp: str          # ISO时间
    duration_min: float     # 本轮耗时（分钟）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionRound":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# 进化引擎
# ============================================================

class EvolutionEngine:
    """
    进化循环引擎 — Agent持续改进的核心驱动。

    每轮进化只改一个变量，用数据说话，锁定或否决。
    所有记录持久化到JSONL，支持多项目隔离。
    """

    def __init__(self, project: str, data_dir: str = "data"):
        self.project = project
        self.data_dir = data_dir
        self._log_path = os.path.join(data_dir, "evolution_log.jsonl")
        self._pending: Dict[str, EvolutionRound] = {}
        os.makedirs(data_dir, exist_ok=True)
        self._history_cache: List[EvolutionRound] = []
        self._load_history()

    # ─── 内部方法 ───

    def _load_history(self):
        self._history_cache = []
        if not os.path.exists(self._log_path):
            return
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("project") == self.project:
                        self._history_cache.append(EvolutionRound.from_dict(d))
                except (json.JSONDecodeError, TypeError):
                    continue

    def _next_round_num(self) -> int:
        if not self._history_cache:
            return 1
        return max(r.round_num for r in self._history_cache) + 1

    def _append_log(self, round_obj: EvolutionRound):
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(round_obj.to_dict(), ensure_ascii=False) + "\n")

    def _update_log(self, round_obj: EvolutionRound):
        """更新指定记录（按id匹配，全文件重写）"""
        lines = []
        if os.path.exists(self._log_path):
            with open(self._log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines = []
        updated = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                d = json.loads(stripped)
                if d.get("id") == round_obj.id:
                    new_lines.append(json.dumps(round_obj.to_dict(), ensure_ascii=False) + "\n")
                    updated = True
                else:
                    new_lines.append(stripped + "\n")
            except (json.JSONDecodeError, TypeError):
                new_lines.append(stripped + "\n")
        if not updated:
            new_lines.append(json.dumps(round_obj.to_dict(), ensure_ascii=False) + "\n")
        with open(self._log_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    @staticmethod
    def _calc_delta(baseline: dict, result: dict) -> dict:
        """计算基线和实验结果之间的差异"""
        delta = {}
        for metric in set(list(baseline.keys()) + list(result.keys())):
            b = baseline.get(metric, 0)
            r = result.get(metric, 0)
            abs_change = r - b
            if b != 0:
                pct_change = round((abs_change / abs(b)) * 100, 2)
            elif r != 0:
                pct_change = float("inf") if r > 0 else float("-inf")
            else:
                pct_change = 0.0
            delta[metric] = {"abs": round(abs_change, 4), "pct": pct_change}
        return delta

    def _find_round(self, round_id: str) -> Optional[EvolutionRound]:
        """从pending或history中查找轮次"""
        r = self._pending.get(round_id)
        if r:
            return r
        for r in self._history_cache:
            if r.id == round_id:
                return r
        return None

    # ─── 核心方法 ───

    def start_round(self, problem: str, variable: str, baseline: dict) -> str:
        """
        开始新一轮进化实验。

        Args:
            problem: 发现的问题描述
            variable: 本轮只改变的变量名
            baseline: 基线数据 {metric: value}

        Returns:
            round_id: 本轮唯一标识符
        """
        round_id = str(uuid.uuid4())
        round_obj = EvolutionRound(
            id=round_id,
            project=self.project,
            round_num=self._next_round_num(),
            problem=problem,
            experiment="",
            variable_changed=variable,
            baseline=baseline,
            result={},
            delta={},
            decision="pending",
            reason="",
            timestamp=datetime.now(_CST).isoformat(),
            duration_min=0.0,
        )
        self._pending[round_id] = round_obj
        self._append_log(round_obj)
        self._history_cache.append(round_obj)
        return round_id

    def record_result(self, round_id: str, result: dict,
                      experiment: str = "") -> dict:
        """
        记录实验结果，自动计算delta，返回对比报告。

        Args:
            round_id: start_round返回的id
            result: 实验数据 {metric: value}
            experiment: 实验描述

        Returns:
            对比报告dict（含suggestion、improved/degraded指标）
        """
        round_obj = self._find_round(round_id)
        if round_obj is None:
            raise ValueError(f"Round {round_id} not found")

        start_time = datetime.fromisoformat(round_obj.timestamp)
        now = datetime.now(_CST)
        duration = (now - start_time).total_seconds() / 60.0
        delta = self._calc_delta(round_obj.baseline, result)

        round_obj.result = result
        round_obj.delta = delta
        round_obj.duration_min = round(duration, 2)
        if experiment:
            round_obj.experiment = experiment

        # 分析指标
        improved, degraded = [], []
        for metric, d in delta.items():
            pct = d["pct"]
            if isinstance(pct, float) and pct == float("inf"):
                improved.append(metric)
            elif isinstance(pct, float) and pct == float("-inf"):
                degraded.append(metric)
            elif pct > 1.0:
                improved.append(metric)
            elif pct < -1.0:
                degraded.append(metric)

        if degraded and not improved:
            suggestion = "建议否决：所有指标均下降"
        elif improved and not degraded:
            suggestion = "建议锁定：所有指标均改善"
        elif improved and degraded:
            suggestion = "效果有争议：部分指标改善，部分下降，需人工判断"
        else:
            suggestion = "效果不明显：变化在1%以内"

        self._update_log(round_obj)

        return {
            "round_id": round_id,
            "round_num": round_obj.round_num,
            "problem": round_obj.problem,
            "variable_changed": round_obj.variable_changed,
            "baseline": round_obj.baseline,
            "result": result,
            "delta": delta,
            "suggestion": suggestion,
            "improved_metrics": improved,
            "degraded_metrics": degraded,
            "duration_min": round_obj.duration_min,
        }

    def decide(self, round_id: str, decision: str, reason: str):
        """
        锁定或否决一轮实验。

        Args:
            decision: "locked" / "rejected"
            reason: 决策原因
        """
        if decision not in ("locked", "rejected"):
            raise ValueError(f"Invalid decision: {decision}")
        round_obj = self._find_round(round_id)
        if round_obj is None:
            raise ValueError(f"Round {round_id} not found")
        round_obj.decision = decision
        round_obj.reason = reason
        self._update_log(round_obj)
        self._pending.pop(round_id, None)

    def best_params(self, metric: str = None) -> dict:
        """
        返回当前最优参数组合（基于已锁定的轮次）。

        Args:
            metric: 按该指标排序（可选）

        Returns:
            包含locked_rounds、params、best_by_metric的字典
        """
        locked = [r for r in self._history_cache if r.decision == "locked"]
        params = {}
        for r in locked:
            var = r.variable_changed
            if var not in params or r.round_num > params[var]["value_from_round"]:
                params[var] = {
                    "value_from_round": r.round_num,
                    "best_result": r.result,
                }

        result = {
            "project": self.project,
            "locked_rounds": len(locked),
            "params": params,
        }
        if metric and locked:
            valid = [r for r in locked if metric in r.result]
            if valid:
                best = max(valid, key=lambda r: r.result[metric])
                result["best_by_metric"] = {
                    metric: {
                        "round_num": best.round_num,
                        "value": best.result[metric],
                        "variable_changed": best.variable_changed,
                    }
                }
        return result

    def history(self, last_n: int = 10) -> List[dict]:
        """返回最近N轮进化记录（按轮次倒序）"""
        sorted_h = sorted(self._history_cache, key=lambda r: r.round_num, reverse=True)
        return [r.to_dict() for r in sorted_h[:last_n]]

    def failure_patterns(self) -> List[dict]:
        """返回所有否决记录，用于避坑"""
        return [
            {
                "round_num": r.round_num,
                "problem": r.problem,
                "variable_changed": r.variable_changed,
                "experiment": r.experiment,
                "reason": r.reason,
                "delta": r.delta,
                "timestamp": r.timestamp,
            }
            for r in self._history_cache if r.decision == "rejected"
        ]

    def summary(self) -> str:
        """项目进化摘要（格式化字符串）"""
        total = len(self._history_cache)
        locked = sum(1 for r in self._history_cache if r.decision == "locked")
        rejected = sum(1 for r in self._history_cache if r.decision == "rejected")
        pending = sum(1 for r in self._history_cache if r.decision == "pending")

        durations = [r.duration_min for r in self._history_cache if r.duration_min > 0]
        avg_dur = round(sum(durations) / len(durations), 1) if durations else 0

        locked_rounds = [r for r in self._history_cache if r.decision == "locked"]
        all_metrics = set()
        for r in locked_rounds:
            all_metrics.update(r.result.keys())

        best_lines = []
        for m in sorted(all_metrics):
            valid = [r for r in locked_rounds if m in r.result]
            if valid:
                best = max(valid, key=lambda r: r.result[m])
                best_lines.append(f"  {m}: {best.result[m]} (第{best.round_num}轮)")

        decided = locked + rejected
        lock_rate = round(locked / decided * 100, 1) if decided > 0 else 0

        lines = [
            f"═══ 进化摘要：{self.project} ═══",
            f"总轮数：{total}  锁定：{locked}  否决：{rejected}  待决：{pending}",
            f"锁定率：{lock_rate}%  平均耗时：{avg_dur}分钟",
        ]
        if best_lines:
            lines.append("最优指标：")
            lines.extend(best_lines)
        if self._history_cache:
            latest = max(self._history_cache, key=lambda r: r.round_num)
            lines.append(f"最近：第{latest.round_num}轮 [{latest.decision}] — {latest.problem}")

        return "\n".join(lines)
