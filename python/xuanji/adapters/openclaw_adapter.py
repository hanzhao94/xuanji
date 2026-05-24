# -*- coding: utf-8 -*-
"""
OpenClaw 兼容适配器

读取 OpenClaw 工作区，自动转为玄机统一格式。
支持：
- Skills (SKILL.md)
- Memory (MEMORY.md, memory/*.md)
- Agent Config (AGENTS.md, SOUL.md, USER.md, TOOLS.md)
- MCP 配置
"""
import os
import re
import json
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class OpenClawAdapter(BaseAdapter):
    """OpenClaw 工作区 → 玄机统一格式"""
    
    name = "openclaw"
    detect_patterns = [
        "skills/",
        "memory/",
        "MEMORY.md",
        "AGENTS.md",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        """执行迁移"""
        result = MigrationResult()
        
        # 1. Skills: 解析所有 SKILL.md
        result.skills = self._migrate_skills(path)
        
        # 2. Memory: 读取所有记忆文件
        result.memories = self._migrate_memory(path)
        
        # 3. Agent Config: 解析 AGENTS.md, SOUL.md, USER.md, TOOLS.md
        result.agent_config = self._migrate_agent_config(path)
        
        # 4. MCP 配置: 从 config 提取
        result.mcp_servers = self._migrate_mcp_config(path)
        
        # 5. Tools: 从 skills 中提取工具定义
        result.tools = self._extract_tools(result.skills)
        
        return result
    
    def _migrate_skills(self, path: str) -> List[Skill]:
        """读取 skills/ 目录下所有 SKILL.md"""
        skills = []
        skills_dir = os.path.join(path, "skills")
        if not os.path.isdir(skills_dir):
            return skills
        
        for skill_name in os.listdir(skills_dir):
            skill_md = os.path.join(skills_dir, skill_name, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # 解析 YAML frontmatter
                frontmatter = self._extract_frontmatter(content)
                # 提取正文（frontmatter 之后的部分）
                body = self._extract_body(content)
                
                skill = Skill(
                    name=frontmatter.get("name", skill_name),
                    description=frontmatter.get("description", ""),
                    instructions=body,
                    source="openclaw",
                    location=skill_md,
                    metadata=frontmatter,
                )
                skills.append(skill)
            except Exception as e:
                result.warnings.append(f"Failed to parse skill {skill_name}: {e}")
        
        return skills
    
    def _migrate_memory(self, path: str) -> List[Memory]:
        """读取记忆文件"""
        memories = []
        
        # MEMORY.md — 长期记忆
        memory_md = os.path.join(path, "MEMORY.md")
        if os.path.isfile(memory_md):
            try:
                with open(memory_md, "r", encoding="utf-8") as f:
                    content = f.read()
                memories.append(Memory(
                    content=content,
                    memory_type="semantic",
                    importance=1.0,
                    tags=["long-term", "core"],
                    source="openclaw",
                ))
            except Exception as e:
                pass
        
        # memory/*.md — 每日记忆/决策
        memory_dir = os.path.join(path, "memory")
        if os.path.isdir(memory_dir):
            for f_name in os.listdir(memory_dir):
                if not f_name.endswith(".md"):
                    continue
                f_path = os.path.join(memory_dir, f_name)
                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # 判断类型
                    if "decision" in f_name.lower() or "DECISIONS" in f_name:
                        mem_type = "decision"
                        importance = 0.9
                    elif f_name.startswith("SESSION_BRIDGE"):
                        mem_type = "episodic"
                        importance = 0.7
                    else:
                        mem_type = "episodic"
                        importance = 0.5
                    
                    memories.append(Memory(
                        content=content,
                        memory_type=mem_type,
                        importance=importance,
                        tags=[f_name.replace(".md", "").lower()],
                        source="openclaw",
                    ))
                except Exception as e:
                    pass
        
        return memories
    
    def _migrate_agent_config(self, path: str) -> AgentConfig:
        """解析 Agent 配置"""
        config = AgentConfig(source="openclaw")
        
        # AGENTS.md — persona + rules
        agents_md = os.path.join(path, "AGENTS.md")
        if os.path.isfile(agents_md):
            try:
                with open(agents_md, "r", encoding="utf-8") as f:
                    content = f.read()
                # 提取规则部分（Red Lines / 安全底线等）
                config.rules = self._extract_rules(content)
                config.persona = self._extract_persona(content)
            except Exception:
                pass
        
        # SOUL.md — persona
        soul_md = os.path.join(path, "SOUL.md")
        if os.path.isfile(soul_md):
            try:
                with open(soul_md, "r", encoding="utf-8") as f:
                    content = f.read()
                config.persona = content[:500]  # 取前500字符
            except Exception:
                pass
        
        # USER.md — 用户信息
        user_md = os.path.join(path, "USER.md")
        if os.path.isfile(user_md):
            try:
                with open(user_md, "r", encoding="utf-8") as f:
                    content = f.read()
                # 可以提取用户偏好
            except Exception:
                pass
        
        # TOOLS.md — 工具笔记
        tools_md = os.path.join(path, "TOOLS.md")
        if os.path.isfile(tools_md):
            try:
                with open(tools_md, "r", encoding="utf-8") as f:
                    content = f.read()
                # 可以提取工具配置
            except Exception:
                pass
        
        return config
    
    def _migrate_mcp_config(self, path: str) -> List[Dict]:
        """提取 MCP 配置"""
        servers = []
        
        # 检查常见的配置文件
        config_files = [
            "config.json",
            "config.yaml",
            "config.yml",
            ".openclaw/config.json",
            "openclaw.json",
        ]
        
        for cf in config_files:
            cf_path = os.path.join(path, cf)
            if not os.path.isfile(cf_path):
                continue
            try:
                with open(cf_path, "r", encoding="utf-8") as f:
                    if cf.endswith(".json"):
                        data = json.load(f)
                    else:
                        data = yaml.safe_load(f)
                
                # 提取 MCP server 配置
                mcp = data.get("mcp", {})
                if isinstance(mcp, dict):
                    servers = mcp.get("servers", [])
                elif isinstance(mcp, list):
                    servers = mcp
                break
            except Exception:
                continue
        
        return servers
    
    def _extract_tools(self, skills: List[Skill]) -> List[Tool]:
        """从 skills 中提取工具定义"""
        tools = []
        for skill in skills:
            # 每个 skill 的 instructions 可能定义工具
            # 这里简化处理：每个 skill 本身就是一个工具
            tools.append(Tool(
                name=skill.name,
                description=skill.description,
                parameters={"type": "object", "properties": {}},
                source="openclaw",
                location=skill.location,
            ))
        return tools
    
    # --- 辅助方法 ---
    
    def _extract_frontmatter(self, content: str) -> Dict[str, Any]:
        """提取 YAML frontmatter"""
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except Exception:
            return {}
    
    def _extract_body(self, content: str) -> str:
        """提取 frontmatter 之后的正文"""
        match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        if match:
            return content[match.end():].strip()
        return content.strip()
    
    def _extract_rules(self, content: str) -> List[str]:
        """提取规则列表"""
        rules = []
        # 找 "## Red Lines" 或 "## 安全底线" 等部分
        for section in ["Red Lines", "安全底线", "安全规则", "Rules"]:
            pattern = rf"## {section}\s*\n(.*?)(?=^## |\Z)"
            match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
            if match:
                section_content = match.group(1)
                for line in section_content.split("\n"):
                    line = line.strip()
                    if line.startswith("- ") or line.startswith("* "):
                        rules.append(line[2:])
                if rules:
                    break
        return rules
    
    def _extract_persona(self, content: str) -> str:
        """提取 persona 描述"""
        # 找 "## Session Startup" 或身份相关部分
        for section in ["SOUL.md", "IDENTITY.md", "Session Startup"]:
            pattern = rf"## {section}\s*\n(.*?)(?=^## |\Z)"
            match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
            if match:
                return match.group(1).strip()[:500]
        return ""
