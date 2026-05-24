# -*- coding: utf-8 -*-
"""
Universal Adapter Layer — 基类

所有生态适配器的共同接口。
"""
import os
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path


@dataclass
class Skill:
    """统一技能格式"""
    name: str
    description: str
    instructions: str  # SKILL.md的正文
    source: str = ""   # 来源框架：openclaw/claude/langchain
    location: str = "" # 原始路径
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Memory:
    """统一记忆格式"""
    content: str
    memory_type: str = "episodic"  # episodic/semantic/decision
    importance: float = 0.5
    tags: List[str] = field(default_factory=list)
    source: str = ""
    created_at: str = ""  # ISO 8601


@dataclass
class Tool:
    """统一工具格式"""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema格式
    callable: Any = None  # 实际可调用对象
    source: str = ""
    location: str = ""


@dataclass
class AgentConfig:
    """统一Agent配置格式"""
    name: str = ""
    persona: str = ""  # persona描述
    rules: List[str] = field(default_factory=list)  # 行为规则
    skills: List[str] = field(default_factory=list)  # 技能列表
    tools: List[str] = field(default_factory=list)   # 工具列表
    source: str = ""


@dataclass
class MigrationResult:
    """迁移结果"""
    skills: List[Skill] = field(default_factory=list)
    memories: List[Memory] = field(default_factory=list)
    tools: List[Tool] = field(default_factory=list)
    agent_config: Optional[AgentConfig] = None
    mcp_servers: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    @property
    def summary(self) -> str:
        parts = []
        if self.skills:
            parts.append(f"{len(self.skills)} skills")
        if self.memories:
            parts.append(f"{len(self.memories)} memories")
        if self.tools:
            parts.append(f"{len(self.tools)} tools")
        if self.mcp_servers:
            parts.append(f"{len(self.mcp_servers)} MCP servers")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warnings")
        return ", ".join(parts) if parts else "nothing found"


class BaseAdapter(ABC):
    """所有生态适配器的基类"""
    
    name: str = ""  # 适配器名称，如 "openclaw"
    detect_patterns: List[str] = []  # 用于检测的文件/目录模式
    
    def detect(self, path: str) -> bool:
        """检测指定路径是否属于该框架的工作区"""
        if not os.path.isdir(path):
            return False
        return any(
            os.path.exists(os.path.join(path, pattern.rstrip("/")))
            for pattern in self.detect_patterns
        )
    
    @abstractmethod
    def migrate(self, path: str) -> MigrationResult:
        """执行迁移：读取外部格式，转为统一格式"""
        pass
    
    def preview(self, path: str) -> str:
        """预览：不实际迁移，只报告会发现什么"""
        if not self.detect(path):
            return f"Not a {self.name} workspace"
        result = self.migrate(path)
        return f"{self.name}: {result.summary}"


class AdapterRegistry:
    """适配器注册表 — 自动发现并注册所有适配器"""
    
    _adapters: Dict[str, BaseAdapter] = {}
    
    @classmethod
    def register(cls, adapter: BaseAdapter):
        """注册一个适配器实例"""
        cls._adapters[adapter.name] = adapter
    
    @classmethod
    def get(cls, name: str) -> Optional[BaseAdapter]:
        """按名称获取适配器"""
        return cls._adapters.get(name)
    
    @classmethod
    def detect(cls, path: str) -> List[BaseAdapter]:
        """检测路径匹配的所有适配器"""
        return [a for a in cls._adapters.values() if a.detect(path)]
    
    @classmethod
    def auto_migrate(cls, path: str) -> MigrationResult:
        """自动检测并迁移"""
        adapters = cls.detect(path)
        if not adapters:
            return MigrationResult(warnings=[f"No adapter found for: {path}"])
        # 用第一个匹配的适配器
        return adapters[0].migrate(path)
    
    @classmethod
    def list_all(cls) -> List[str]:
        return list(cls._adapters.keys())
