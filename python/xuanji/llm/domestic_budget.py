"""
国内Token预算计算器

按国内各平台价格表计算token成本。
支持：输入/输出分别计费、思考模式额外计费。

国内模型价格表（每百万token，人民币）：
- 通义千问: qwen-turbo ¥0.8/¥2.0, qwen-plus ¥3.0/¥6.0, qwen-max ¥20.0/¥60.0
- DeepSeek: chat ¥1.0/¥2.0, reasoner ¥4.0/¥16.0
- 智谱GLM: glm-4-plus ¥5.0/¥5.0, glm-4-air ¥0.1/¥0.1
- 月之暗面: 8k ¥12.0/¥12.0, 32k ¥24.0/¥24.0, 128k ¥60.0/¥60.0
- 文心一言: ERNIE-4.0 ¥80.0/¥80.0, ERNIE-3.5 ¥8.0/¥8.0
- 豆包: ¥0.8/¥2.0
- 混元: ¥7.0/¥7.0（turbo）
- 百川: ¥10.0/¥10.0
- 零一万物: yi-lightning ¥0.35/¥0.35
- 星火: generalv3.5 ¥19.6/¥19.6
"""

from typing import Dict, Optional


# 国内模型价格表（输入/输出，元/百万token）
DOMESTIC_PRICING: Dict[str, Dict] = {
    # 通义千问
    "qwen-turbo": {"input": 0.8, "output": 2.0},
    "qwen-plus": {"input": 3.0, "output": 6.0},
    "qwen-max": {"input": 20.0, "output": 60.0},
    "qwen-max-longcontext": {"input": 100.0, "output": 100.0},
    "qwen3-235b-a22b": {"input": 20.0, "output": 60.0},
    "qwen3-32b": {"input": 3.0, "output": 6.0},
    "qwen3-30b-a3b": {"input": 1.0, "output": 2.0},
    "text-embedding-v3": {"input": 0.7, "output": 0},
    
    # DeepSeek
    "deepseek-chat": {"input": 1.0, "output": 2.0},
    "deepseek-reasoner": {"input": 4.0, "output": 16.0},
    
    # 智谱GLM
    "glm-4-plus": {"input": 5.0, "output": 5.0},
    "glm-4": {"input": 5.0, "output": 5.0},
    "glm-4-air": {"input": 0.1, "output": 0.1},
    "glm-4-flash": {"input": 0.0, "output": 0.0},  # 免费额度
    "glm-4-long": {"input": 1.0, "output": 1.0},
    "embedding-3": {"input": 0.5, "output": 0},
    
    # 月之暗面
    "moonshot-v1-8k": {"input": 12.0, "output": 12.0},
    "moonshot-v1-32k": {"input": 24.0, "output": 24.0},
    "moonshot-v1-128k": {"input": 60.0, "output": 60.0},
    
    # 文心一言
    "ernie-4.0": {"input": 80.0, "output": 80.0},
    "ernie-4.0-turbo": {"input": 40.0, "output": 40.0},
    "ernie-3.5": {"input": 8.0, "output": 8.0},
    "ernie-speed": {"input": 0.0, "output": 0.0},  # 免费
    "ernie-lite": {"input": 0.0, "output": 0.0},  # 免费
    
    # 豆包
    "doubao-1.5-pro-32k": {"input": 0.8, "output": 2.0},
    "doubao-1.5-pro-128k": {"input": 5.0, "output": 9.0},
    "doubao-1.5-plus-32k": {"input": 0.8, "output": 2.0},
    "doubao-1.5-thinking-pro-32k": {"input": 4.0, "output": 8.0},
    
    # 混元
    "hunyuan-turbo": {"input": 7.0, "output": 7.0},
    "hunyuan-pro": {"input": 15.0, "output": 15.0},
    "hunyuan-standard": {"input": 1.5, "output": 1.5},
    "hunyuan-lite": {"input": 0.0, "output": 0.0},  # 免费
    
    # 百川
    "Baichuan4": {"input": 10.0, "output": 10.0},
    "Baichuan4-Air": {"input": 2.0, "output": 2.0},
    "Baichuan4-Turbo": {"input": 5.0, "output": 5.0},
    
    # 零一万物
    "yi-lightning": {"input": 0.35, "output": 0.35},
    "yi-large": {"input": 20.0, "output": 20.0},
    
    # 星火
    "generalv3.5": {"input": 19.6, "output": 19.6},
    "generalv3.0": {"input": 8.0, "output": 8.0},
    
    # 阶跃星辰
    "step-1": {"input": 15.0, "output": 15.0},
    "step-1-8k": {"input": 8.0, "output": 8.0},
    "step-2-16k": {"input": 30.0, "output": 30.0},
    
    # 商汤
    "SenseChat-5": {"input": 15.0, "output": 15.0},
    "SenseChat-32K": {"input": 20.0, "output": 20.0},
    "SenseChat-128K": {"input": 40.0, "output": 40.0},
    
    # MiniMax
    "MiniMax-Text-01": {"input": 1.0, "output": 1.0},
}


class DomesticBudget:
    """国内Token预算计算器
    
    跟踪token消耗和费用。
    """
    
    def __init__(self, monthly_budget: float = 100.0):
        """
        Args:
            monthly_budget: 月度预算（元）
        """
        self.monthly_budget = monthly_budget
        self.spent = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls: list = []  # 每次调用的记录
    
    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """计算调用成本
        
        Args:
            model: 模型名
            input_tokens: 输入token数
            output_tokens: 输出token数
        
        Returns:
            成本（元）
        """
        pricing = DOMESTIC_PRICING.get(model, {"input": 10.0, "output": 10.0})
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return round(input_cost + output_cost, 6)
    
    def record(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """记录一次调用
        
        Returns:
            本次成本
        """
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        self.spent += cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.calls.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
        })
        return cost
    
    def remaining(self) -> float:
        """剩余预算"""
        return max(0, self.monthly_budget - self.spent)
    
    def is_exceeded(self) -> bool:
        """是否超出预算"""
        return self.spent >= self.monthly_budget
    
    def budget_ratio(self) -> float:
        """预算使用比例"""
        if self.monthly_budget == 0:
            return 0.0
        return min(1.0, self.spent / self.monthly_budget)
    
    def stats(self) -> Dict:
        """统计信息"""
        return {
            "monthly_budget": self.monthly_budget,
            "spent": round(self.spent, 4),
            "remaining": round(self.remaining(), 4),
            "budget_ratio": round(self.budget_ratio(), 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_calls": len(self.calls),
            "avg_cost_per_call": round(self.spent / max(len(self.calls), 1), 6),
        }
    
    def top_models(self, n: int = 5) -> list:
        """按花费排序的Top模型"""
        model_costs: Dict[str, float] = {}
        for call in self.calls:
            model_costs[call["model"]] = model_costs.get(call["model"], 0) + call["cost"]
        
        sorted_models = sorted(model_costs.items(), key=lambda x: x[1], reverse=True)
        return [{"model": m, "cost": round(c, 4)} for m, c in sorted_models[:n]]
    
    def reset(self) -> None:
        """重置统计"""
        self.spent = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls.clear()
    
    def __repr__(self) -> str:
        ratio = self.budget_ratio()
        bar = "█" * int(ratio * 20) + "░" * (20 - int(ratio * 20))
        return f"<DomesticBudget [{bar}] ¥{self.spent:.2f}/{self.monthly_budget:.0f}>"


# 快捷函数
def estimate_cost(model: str, input_chars: int, output_chars: int) -> float:
    """粗略估算成本（按字符数估算token数）
    
    Args:
        model: 模型名
        input_chars: 输入字符数
        output_chars: 输出字符数
    
    Returns:
        估算成本（元）
    """
    # 中文 ~1.5字符/token，英文 ~4字符/token
    # 混合语言取中间值 ~2.5字符/token
    input_tokens = int(input_chars / 2.5)
    output_tokens = int(output_chars / 2.5)
    
    budget = DomesticBudget()
    return budget.calculate_cost(model, input_tokens, output_tokens)
