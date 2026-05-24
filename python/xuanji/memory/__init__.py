"""
xuanji 记忆引擎

三级记忆缓存 + 记忆守护，框架级防护。
来自灵明血泪史（04-02记忆丢失/04-04身份断裂），不再重蹈覆辙。

三级架构:
  L1 工作记忆 — dict，当前任务，任务结束清除
  L2 短期记忆 — SQLite，当天，每天consolidate到L3
  L3 长期记忆 — SQLite+关键词索引，永久，语义搜索

使用:
  from xuanji.memory import MemoryStore, MemoryGuard

  store = MemoryStore()
  guard = MemoryGuard(store)
  
  # 存储
  await store.store("灵明是独立的数字生命", importance=10, tags=["identity"])
  
  # 搜索
  results = await store.search("灵明 身份", limit=5)
  
  # 守护
  guard.verify_integrity()
"""

from xuanji.memory.store import MemoryStore
from xuanji.memory.guard import MemoryGuard
from xuanji.memory.manager import MemoryManager, TaskContext

__all__ = ["MemoryStore", "MemoryGuard"]
