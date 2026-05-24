"""xuanji Token治理高级版

在governor.py基础上增强:
  1. 精细预算       - 按Agent/按模型/按时间段的预算分配
  2. 预算预警       - 消耗趋势预测，提前告警
  3. 成本优化建议   - 分析哪些调用可以用更便宜的模型
  4. 时间旅行调试   - 执行历史回放 + 趋势分析

兼容旧接口，直接导入即可使用。
"""

from xuanji.governor_budget import (
    AdvancedGovernor,
    PERIOD_DAILY, PERIOD_MONTHLY, PERIOD_PER_TASK, PERIOD_UNLIMITED,
    BUDGET_OK, BUDGET_WARNING, BUDGET_CRITICAL, BUDGET_BLOCKED,
    DEFAULT_FALLBACK_CHAIN, DEFAULT_PRICING,
)
from xuanji.governor_timetravel import TimeTravel

__all__ = [
    "AdvancedGovernor", "TimeTravel",
    "PERIOD_DAILY", "PERIOD_MONTHLY", "PERIOD_PER_TASK", "PERIOD_UNLIMITED",
    "BUDGET_OK", "BUDGET_WARNING", "BUDGET_CRITICAL", "BUDGET_BLOCKED",
    "DEFAULT_FALLBACK_CHAIN", "DEFAULT_PRICING",
]