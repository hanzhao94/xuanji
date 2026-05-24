# -*- coding: utf-8 -*-
"""
xuanji/adapters/migrate.py — 迁移集成模块

把 Universal Adapter Layer 接入玄机核心：
- SkillLoader 扫描外部 skills
- ToolRegistry 注册外部 tools
- MemoryManager 导入外部记忆
"""
import os
import sys
import logging
from typing import List, Dict, Optional, Any

from .base import (
    BaseAdapter, AdapterRegistry, Skill, Memory, Tool, MigrationResult
)

logger = logging.getLogger(__name__)


class MigrationEngine:
    """迁移引擎 — 连接 UAL 和玄机核心"""
    
    def __init__(self, skill_loader=None, tool_registry=None, memory_manager=None):
        self.skill_loader = skill_loader
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager
    
    def migrate_from(self, framework: str, path: str) -> MigrationResult:
        """从指定框架迁移到玄机"""
        adapter = AdapterRegistry.get(framework)
        if not adapter:
            raise ValueError(f"Unknown framework: {framework}")
        
        if not adapter.detect(path):
            raise ValueError(f"Not a {framework} workspace: {path}")
        
        return adapter.migrate(path)
    
    def auto_migrate(self, path: str) -> MigrationResult:
        """自动检测并迁移"""
        return AdapterRegistry.auto_migrate(path)
    
    def apply_skills(self, result: MigrationResult) -> int:
        """把迁移到的 skills 注入 SkillLoader"""
        if not self.skill_loader or not result.skills:
            return 0
        
        count = 0
        for skill in result.skills:
            # 转为 SkillInfo 格式
            info = self._make_skill_info(skill)
            self.skill_loader._skills[skill.name] = info
            count += 1
            logger.info(f"Loaded skill: {skill.name}")
        
        return count
    
    def apply_tools(self, result: MigrationResult) -> int:
        """把迁移到的 tools 注入 ToolRegistry"""
        if not self.tool_registry or not result.tools:
            return 0
        
        # 尝试导入 Skill Executor（真实可执行工具）
        skill_tools = self._load_skill_tools()
        
        count = 0
        for tool in result.tools:
            try:
                # 优先使用 Skill Executor 的真实实现
                if tool.name in skill_tools:
                    tool_info = skill_tools[tool.name]
                    self.tool_registry.register(
                        name=tool.name,
                        description=tool_info.get("description", tool.description),
                        params=tool_info.get("params", tool.parameters),
                        func=tool_info["func"],
                        category="openclaw_skill",
                    )
                    logger.info(f"Registered tool (real): {tool.name}")
                else:
                    # 降级为 stub
                    stub = self._make_tool_stub(tool)
                    self.tool_registry.register(
                        name=tool.name,
                        description=tool.description,
                        params=tool.parameters,
                        func=stub,
                        category="external",
                    )
                    logger.info(f"Registered tool (stub): {tool.name}")
                count += 1
            except Exception as e:
                logger.warning(f"Failed to register tool {tool.name}: {e}")
        
        return count
    
    def _load_skill_tools(self) -> dict:
        """尝试加载 Skill Executor 的真实工具实现"""
        try:
            from .skill_executor import SKILL_TOOLS
            return SKILL_TOOLS
        except ImportError:
            return {}
    
    def apply_memories(self, result: MigrationResult) -> int:
        """把迁移到的 memories 导入 MemoryManager"""
        if not self.memory_manager or not result.memories:
            return 0
        
        count = 0
        importance_map = {0.5: 5, 0.7: 7, 0.9: 9, 1.0: 10}
        for mem in result.memories:
            try:
                imp = importance_map.get(mem.importance, 5)
                # MemoryManager.remember is async, try sync fallback
                if hasattr(self.memory_manager, 'store'):
                    self.memory_manager.store(
                        content=mem.content,
                        importance=imp,
                        tags=mem.tags,
                    )
                elif hasattr(self.memory_manager, 'remember'):
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(
                                self.memory_manager.remember(
                                    mem.content, importance=imp, tags=mem.tags
                                )
                            )
                        else:
                            loop.run_until_complete(
                                self.memory_manager.remember(
                                    mem.content, importance=imp, tags=mem.tags
                                )
                            )
                    except RuntimeError:
                        pass
                count += 1
            except Exception as e:
                logger.warning(f"Failed to store memory: {e}")
        
        return count
    
    def apply_all(self, result: MigrationResult) -> Dict[str, int]:
        """一键应用所有迁移结果"""
        return {
            "skills": self.apply_skills(result),
            "tools": self.apply_tools(result),
            "memories": self.apply_memories(result),
        }
    
    # --- 辅助方法 ---
    
    def _make_skill_info(self, skill: Skill) -> Any:
        """转为 SkillInfo 格式"""
        # 尝试导入 SkillInfo
        try:
            from xuanji.skill import SkillInfo
            info = SkillInfo()
            info.name = skill.name
            info.description = skill.description
            info.content = skill.instructions
            info.path = skill.location
            info.directory = os.path.dirname(skill.location)
            info.trigger_keywords = skill.metadata.get("trigger_keywords", [])
            info.version = skill.metadata.get("version", "0.1.0")
            info.author = skill.metadata.get("author", "")
            return info
        except ImportError:
            # 降级为 dict
            return {
                "name": skill.name,
                "description": skill.description,
                "content": skill.instructions,
                "path": skill.location,
            }
    
    def _make_tool_stub(self, tool: Tool):
        """创建工具占位函数"""
        def stub(**kwargs):
            return f"[External tool '{tool.name}' from {tool.source}. Actual implementation needed.]"
        stub.__name__ = tool.name
        stub.__doc__ = tool.description
        return stub


# --- 便捷函数 ---

def quick_preview(path: str) -> str:
    """预览路径中的可迁移内容"""
    adapters = AdapterRegistry.detect(path)
    if not adapters:
        return f"No known framework detected at: {path}"
    
    lines = []
    for adapter in adapters:
        lines.append(f"Detected: {adapter.name}")
        result = adapter.migrate(path)
        lines.append(f"  {result.summary}")
    
    return "\n".join(lines)


def quick_migrate(path: str, framework: str = None) -> MigrationResult:
    """快速迁移（自动检测或指定框架）"""
    if framework:
        engine = MigrationEngine()
        return engine.migrate_from(framework, path)
    else:
        return AdapterRegistry.auto_migrate(path)
