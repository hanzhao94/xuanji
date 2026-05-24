"""
xuanji Token治理

控制Token消耗，防止费用失控:
  - 日预算/单任务预算（可配置）
  - Token计数（按模型不同的计数方式）
  - 预算用完→降级到更便宜的模型→本地模型→拒绝
  - 上下文压缩: 消息历史超过阈值→旧消息摘要替换
  - 统计报告: 今日消耗/剩余预算/各Agent消耗

零外部依赖，纯标准库。
"""

import time
import json
import os
import threading
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict


# 模型tier定义（从贵到便宜）
MODEL_TIERS = {
    # Tier 1: 旗舰模型（最贵）
    "gpt-4o": 1,
    "gpt-4-turbo": 1,
    "claude-sonnet-4-20250514": 1,
    "claude-3-opus": 1,
    "gemini-2.0-flash": 1,
    "deepseek-chat": 1,
    "qwen-plus": 1,
    "glm-4-plus": 1,
    
    # Tier 2: 中档模型
    "gpt-4o-mini": 2,
    "claude-3-haiku": 2,
    "gemini-1.5-flash": 2,
    "moonshot-v1-8k": 2,
    "deepseek-chat-v2": 2,
    "qwen-turbo": 2,
    
    # Tier 3: 经济模型
    "gpt-3.5-turbo": 3,
    "llama-3.3-70b-versatile": 3,
    "mistral-large-latest": 3,
    
    # Tier 4: 本地模型（免费）
    "ollama:*": 4,
    "vllm:*": 4,
    "llamacpp:*": 4,
}

# 每1K token的估算成本（美元）— 用于预算计算
TOKEN_COSTS_PER_1K = {
    1: 0.015,   # Tier 1
    2: 0.005,   # Tier 2
    3: 0.001,   # Tier 3
    4: 0.0,     # Tier 4 本地
}

# 降级路径: 当前tier → 下一个可用tier
DOWNGRADE_PATH = {1: 2, 2: 3, 3: 4, 4: None}


def _default_db_path() -> str:
    """默认统计数据库路径"""
    home = Path.home() / ".xuanji" / "data"
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "token_stats.db")


class BudgetExhausted(Exception):
    """预算耗尽异常"""
    pass


class TokenGovernor:
    """Token治理官
    
    Args:
        daily_budget: 日预算（美元），默认1.0
        task_budget: 单任务预算（美元），默认0.2
        context_threshold: 上下文压缩阈值（token数），默认8000
        db_path: 统计数据库路径
    """
    
    def __init__(
        self,
        daily_budget: float = 1.0,
        task_budget: float = 0.2,
        context_threshold: int = 8000,
        db_path: Optional[str] = None,
    ):
        self.daily_budget = daily_budget
        self.task_budget = task_budget
        self.context_threshold = context_threshold
        self.db_path = db_path or _default_db_path()
        
        # 当前任务Token消耗
        self._task_usage: Dict[str, int] = defaultdict(int)  # agent_name → tokens
        self._task_cost: Dict[str, float] = defaultdict(float)
        
        # 线程安全（RLock支持同线程重入）
        self._lock = threading.RLock()
        
        # SQLite统计
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_tables()
    
    def _init_tables(self):
        """初始化统计表"""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    agent_name TEXT DEFAULT 'default',
                    model TEXT NOT NULL,
                    tier INTEGER DEFAULT 1,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    estimated_cost REAL DEFAULT 0.0,
                    timestamp REAL NOT NULL
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_date ON token_usage(date)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_agent ON token_usage(agent_name, date)"
            )
            self._conn.commit()
    
    # ========== Token计数 ==========
    
    def count_tokens(self, text: str, model: str = "") -> int:
        """估算文本的token数
        
        简单估算策略（零依赖）:
          - 英文: ~4字符/token
          - 中文: ~1.5字符/token
          - 混合: 按字符类型分别计算
        
        Args:
            text: 要计数的文本
            model: 模型名（预留，不同模型可能有不同计数方式）
        
        Returns:
            估算token数
        """
        if not text:
            return 0
        
        import re
        
        # 分离中英文
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        ascii_chars = len(re.findall(r'[a-zA-Z0-9]', text))
        other_chars = len(text) - chinese_chars - ascii_chars
        
        # 估算
        chinese_tokens = chinese_chars / 1.5
        ascii_tokens = ascii_chars / 4.0
        other_tokens = other_chars / 3.0
        
        return max(1, int(chinese_tokens + ascii_tokens + other_tokens))
    
    def count_messages(self, messages: List[Dict], model: str = "") -> int:
        """估算消息列表的token数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count_tokens(content, model)
            elif isinstance(content, list):
                # 多模态消息
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count_tokens(part.get("text", ""), model)
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        total += 85  # 图片固定token估算
            # role/name等元信息
            total += 4
        total += 3  # 消息格式开销
        return total
    
    # ========== 预算检查 ==========
    
    def get_model_tier(self, model: str) -> int:
        """获取模型tier"""
        # 精确匹配
        if model in MODEL_TIERS:
            return MODEL_TIERS[model]
        
        # 通配符匹配
        for pattern, tier in MODEL_TIERS.items():
            if pattern.endswith(":*"):
                prefix = pattern[:-2]
                if model.startswith(prefix):
                    return tier
        
        # 默认Tier 2
        return 2
    
    def check_budget(
        self, model: str, estimated_tokens: int, agent_name: str = "default"
    ) -> Dict[str, Any]:
        """检查预算是否足够
        
        Returns:
            {
                "allowed": bool,
                "model": str (可能降级后的模型),
                "reason": str,
                "daily_remaining": float,
                "task_remaining": float,
            }
        """
        tier = self.get_model_tier(model)
        cost_per_1k = TOKEN_COSTS_PER_1K.get(tier, 0.005)
        estimated_cost = (estimated_tokens / 1000.0) * cost_per_1k
        
        # 日预算检查
        daily_used = self.get_daily_cost()
        daily_remaining = self.daily_budget - daily_used
        
        # 任务预算检查
        task_used = self._task_cost.get(agent_name, 0.0)
        task_remaining = self.task_budget - task_used
        
        if estimated_cost <= daily_remaining and estimated_cost <= task_remaining:
            return {
                "allowed": True,
                "model": model,
                "reason": "预算充足",
                "daily_remaining": daily_remaining,
                "task_remaining": task_remaining,
            }
        
        # 尝试降级
        downgraded = self._try_downgrade(tier, estimated_tokens, daily_remaining, task_remaining)
        if downgraded:
            return downgraded
        
        # 完全拒绝
        return {
            "allowed": False,
            "model": model,
            "reason": f"预算不足 (日剩余: ${daily_remaining:.4f}, 任务剩余: ${task_remaining:.4f})",
            "daily_remaining": daily_remaining,
            "task_remaining": task_remaining,
        }
    
    def _try_downgrade(
        self, current_tier: int, tokens: int,
        daily_remaining: float, task_remaining: float
    ) -> Optional[Dict]:
        """尝试降级到更便宜的模型"""
        tier = DOWNGRADE_PATH.get(current_tier)
        
        while tier is not None:
            cost_per_1k = TOKEN_COSTS_PER_1K.get(tier, 0.0)
            estimated_cost = (tokens / 1000.0) * cost_per_1k
            
            if estimated_cost <= daily_remaining and estimated_cost <= task_remaining:
                # 找到可用tier，返回该tier的推荐模型
                model = self._suggest_model_for_tier(tier)
                return {
                    "allowed": True,
                    "model": model,
                    "reason": f"预算不足，已降级到Tier {tier}: {model}",
                    "daily_remaining": daily_remaining,
                    "task_remaining": task_remaining,
                    "downgraded": True,
                }
            
            tier = DOWNGRADE_PATH.get(tier)
        
        return None
    
    def _suggest_model_for_tier(self, tier: int) -> str:
        """推荐该tier的默认模型"""
        tier_models = {
            1: "deepseek-chat",
            2: "gpt-4o-mini",
            3: "gpt-3.5-turbo",
            4: "ollama:auto",
        }
        return tier_models.get(tier, "ollama:auto")
    
    # ========== 记录消耗 ==========
    
    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        agent_name: str = "default",
    ):
        """记录Token消耗
        
        Args:
            model: 使用的模型
            input_tokens: 输入token数
            output_tokens: 输出token数
            agent_name: Agent名称
        """
        tier = self.get_model_tier(model)
        total = input_tokens + output_tokens
        cost_per_1k = TOKEN_COSTS_PER_1K.get(tier, 0.005)
        cost = (total / 1000.0) * cost_per_1k
        
        today = time.strftime("%Y-%m-%d")
        now = time.time()
        
        with self._lock:
            # 写入数据库
            self._conn.execute(
                """INSERT INTO token_usage 
                   (date, agent_name, model, tier, input_tokens, output_tokens, 
                    total_tokens, estimated_cost, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (today, agent_name, model, tier, input_tokens, output_tokens,
                 total, cost, now)
            )
            self._conn.commit()
            
            # 更新任务内存计数
            self._task_usage[agent_name] += total
            self._task_cost[agent_name] += cost
    
    # ========== 上下文压缩 ==========
    
    def compress_context(
        self, messages: List[Dict], max_tokens: Optional[int] = None
    ) -> Tuple[List[Dict], Dict[str, Any]]:
        """上下文压缩: 超过阈值的旧消息用摘要替换
        
        策略:
          1. 保留system消息（不压缩）
          2. 保留最近N条消息
          3. 中间的消息压缩成摘要
        
        Args:
            messages: 消息历史
            max_tokens: 压缩阈值（默认用self.context_threshold）
        
        Returns:
            (压缩后的消息, 压缩报告)
        """
        threshold = max_tokens or self.context_threshold
        current_tokens = self.count_messages(messages)
        
        if current_tokens <= threshold:
            return messages, {"compressed": False, "original_tokens": current_tokens}
        
        # 分离system消息和对话消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        dialog_msgs = [m for m in messages if m.get("role") != "system"]
        
        if len(dialog_msgs) <= 4:
            # 消息太少，不压缩
            return messages, {"compressed": False, "reason": "too_few_messages"}
        
        # 保留最近的消息
        keep_recent = max(2, len(dialog_msgs) // 3)  # 至少保留2条，最多1/3
        recent = dialog_msgs[-keep_recent:]
        to_compress = dialog_msgs[:-keep_recent]
        
        # 生成摘要
        summary = self._generate_summary(to_compress)
        summary_msg = {
            "role": "system",
            "content": f"[上下文摘要 — 以下是之前{len(to_compress)}条消息的要点]\n{summary}"
        }
        
        compressed = system_msgs + [summary_msg] + recent
        compressed_tokens = self.count_messages(compressed)
        
        return compressed, {
            "compressed": True,
            "original_tokens": current_tokens,
            "compressed_tokens": compressed_tokens,
            "messages_compressed": len(to_compress),
            "messages_kept": len(recent) + len(system_msgs),
            "saved_tokens": current_tokens - compressed_tokens,
        }
    
    def _generate_summary(self, messages: List[Dict]) -> str:
        """生成消息摘要（纯文本提取，不调用LLM）
        
        策略: 提取每条消息的前80字符，合并成摘要
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                preview = content[:80].replace("\n", " ")
                if len(content) > 80:
                    preview += "..."
                lines.append(f"- [{role}] {preview}")
        
        return "\n".join(lines)
    
    # ========== 统计报告 ==========
    
    def get_daily_cost(self, date: Optional[str] = None) -> float:
        """获取某天的总消耗"""
        date = date or time.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM token_usage WHERE date = ?",
                (date,)
            ).fetchone()
            return row[0] if row else 0.0
    
    def get_daily_tokens(self, date: Optional[str] = None) -> int:
        """获取某天的总token数"""
        date = date or time.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM token_usage WHERE date = ?",
                (date,)
            ).fetchone()
            return row[0] if row else 0
    
    def report(self, date: Optional[str] = None) -> Dict[str, Any]:
        """统计报告: 今日消耗/剩余预算/各Agent消耗
        
        Returns:
            {
                "date": "2026-05-15",
                "daily_budget": 1.0,
                "daily_used": 0.35,
                "daily_remaining": 0.65,
                "total_tokens": 12345,
                "by_agent": {"agent1": {"tokens": 5000, "cost": 0.2}, ...},
                "by_model": {"gpt-4o": {"tokens": 3000, "cost": 0.15}, ...},
                "task_usage": {"agent1": {"tokens": 2000, "cost": 0.1}, ...},
            }
        """
        date = date or time.strftime("%Y-%m-%d")
        
        with self._lock:
            # 总量
            daily_cost = self.get_daily_cost(date)
            daily_tokens = self.get_daily_tokens(date)
            
            # 按Agent
            by_agent = {}
            rows = self._conn.execute(
                """SELECT agent_name, SUM(total_tokens), SUM(estimated_cost)
                   FROM token_usage WHERE date = ? GROUP BY agent_name""",
                (date,)
            ).fetchall()
            for name, tokens, cost in rows:
                by_agent[name] = {"tokens": tokens, "cost": round(cost, 6)}
            
            # 按模型
            by_model = {}
            rows = self._conn.execute(
                """SELECT model, SUM(total_tokens), SUM(estimated_cost)
                   FROM token_usage WHERE date = ? GROUP BY model""",
                (date,)
            ).fetchall()
            for model, tokens, cost in rows:
                by_model[model] = {"tokens": tokens, "cost": round(cost, 6)}
        
        # 当前任务消耗
        task_usage = {}
        for agent, tokens in self._task_usage.items():
            task_usage[agent] = {
                "tokens": tokens,
                "cost": round(self._task_cost.get(agent, 0.0), 6),
            }
        
        return {
            "date": date,
            "daily_budget": self.daily_budget,
            "daily_used": round(daily_cost, 6),
            "daily_remaining": round(self.daily_budget - daily_cost, 6),
            "total_tokens": daily_tokens,
            "by_agent": by_agent,
            "by_model": by_model,
            "task_usage": task_usage,
        }
    
    # ========== 任务生命周期 ==========
    
    def reset_task(self, agent_name: str = "default"):
        """重置任务级消耗（任务结束时调用）"""
        with self._lock:
            self._task_usage.pop(agent_name, None)
            self._task_cost.pop(agent_name, None)
    
    def reset_all_tasks(self):
        """重置所有任务级消耗"""
        with self._lock:
            self._task_usage.clear()
            self._task_cost.clear()
    
    # ========== 生命周期 ==========
    
    def close(self):
        """关闭数据库连接"""
        with self._lock:
            self._conn.close()
    
    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
