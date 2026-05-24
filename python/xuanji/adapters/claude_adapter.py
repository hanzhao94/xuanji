# -*- coding: utf-8 -*-
"""
Claude 兼容适配器

读取 Claude Code / Claude Projects / Claude Desktop 数据，转为玄机统一格式。
支持：
- Claude Code skills (.claude/ 目录)
- Claude Projects 对话记录 (JSON导出)
- Claude Desktop 记忆 (本地SQLite)
- CLAUDE.md 配置文件
"""
import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class ClaudeAdapter(BaseAdapter):
    """Claude 生态 → 玄机统一格式"""
    
    name = "claude"
    detect_patterns = [
        ".claude/",
        "CLAUDE.md",
        "claude/",
        "projects/",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Skills: .claude/ 目录
        result.skills = self._migrate_claude_skills(path)
        
        # 2. CLAUDE.md — 类似 AGENTS.md
        result.agent_config = self._migrate_claude_config(path)
        
        # 3. Projects: 对话记录
        result.memories = self._migrate_projects(path)
        
        # 4. 从对话记录中提取工具使用模式
        result.tools = self._extract_tools(result.skills)
        
        return result
    
    def _migrate_claude_skills(self, path: str) -> List[Skill]:
        """读取 .claude/ 或 skills/ 目录"""
        skills = []
        
        # Claude Code 的 skills 通常在 .claude/ 或 skills/
        for subdir in [".claude", "skills", "CLAUDE/skills"]:
            skills_dir = os.path.join(path, subdir)
            if not os.path.isdir(skills_dir):
                continue
            
            for skill_name in os.listdir(skills_dir):
                # Claude skills 可能是 .md 文件或目录
                md_file = os.path.join(skills_dir, skill_name)
                if os.path.isfile(md_file) and md_file.endswith(".md"):
                    skill = self._parse_skill_file(md_file, skill_name)
                    if skill:
                        skills.append(skill)
                elif os.path.isdir(md_file):
                    # 目录形式
                    for f in os.listdir(md_file):
                        if f.endswith(".md"):
                            skill = self._parse_skill_file(
                                os.path.join(md_file, f),
                                os.path.join(skill_name, f)
                            )
                            if skill:
                                skills.append(skill)
        
        return skills
    
    def _parse_skill_file(self, path: str, name: str) -> Optional[Skill]:
        """解析 Claude skill 文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 提取 frontmatter（如果有的话）
            frontmatter = self._extract_frontmatter(content)
            body = self._extract_body(content)
            
            return Skill(
                name=frontmatter.get("name", name.replace(".md", "")),
                description=frontmatter.get("description", ""),
                instructions=body,
                source="claude",
                location=path,
                metadata=frontmatter,
            )
        except Exception as e:
            return None
    
    def _migrate_claude_config(self, path: str) -> Optional[AgentConfig]:
        """解析 CLAUDE.md"""
        claude_md = os.path.join(path, "CLAUDE.md")
        if not os.path.isfile(claude_md):
            return None
        
        try:
            with open(claude_md, "r", encoding="utf-8") as f:
                content = f.read()
            
            config = AgentConfig(source="claude")
            config.persona = content[:500]
            
            # 提取规则
            config.rules = self._extract_rules(content)
            
            return config
        except Exception:
            return None
    
    def _migrate_projects(self, path: str) -> List[Memory]:
        """读取 Projects 对话记录"""
        memories = []
        
        # 常见的对话记录位置
        project_dirs = ["projects", "conversations", ".claude/conversations"]
        
        for pd in project_dirs:
            proj_path = os.path.join(path, pd)
            if not os.path.isdir(proj_path):
                continue
            
            for f_name in os.listdir(proj_path):
                if not f_name.endswith(".json"):
                    continue
                
                f_path = os.path.join(proj_path, f_name)
                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # 解析对话
                    conv_memories = self._parse_conversation(data, f_name)
                    memories.extend(conv_memories)
                except Exception:
                    continue
        
        return memories
    
    def _parse_conversation(self, data: Any, filename: str) -> List[Memory]:
        """解析对话 JSON 为记忆"""
        memories = []
        
        if isinstance(data, dict):
            # 提取消息
            messages = data.get("messages", data.get("conversation", []))
            if isinstance(messages, list):
                # 汇总为一条记忆
                content_parts = []
                for msg in messages[:20]:  # 最多取20条
                    role = msg.get("role", "unknown")
                    text = msg.get("content", msg.get("text", ""))
                    if isinstance(text, list):
                        text = " ".join(
                            t.get("text", "") for t in text if isinstance(t, dict)
                        )
                    if text:
                        content_parts.append(f"[{role}] {text[:200]}")
                
                if content_parts:
                    memories.append(Memory(
                        content="\n".join(content_parts),
                        memory_type="episodic",
                        importance=data.get("importance", 0.5),
                        tags=["claude", "conversation", filename.replace(".json", "")],
                        source="claude",
                    ))
        
        elif isinstance(data, list):
            # 直接是消息列表
            for item in data:
                if isinstance(item, dict) and "content" in item:
                    memories.append(Memory(
                        content=str(item.get("content", ""))[:500],
                        memory_type="episodic",
                        importance=item.get("importance", 0.5),
                        tags=["claude"],
                        source="claude",
                    ))
        
        return memories
    
    def _extract_tools(self, skills: List[Skill]) -> List[Tool]:
        """从 skills 提取工具定义"""
        tools = []
        for skill in skills:
            tools.append(Tool(
                name=skill.name,
                description=skill.description,
                parameters={"type": "object", "properties": {}},
                source="claude",
                location=skill.location,
            ))
        return tools
    
    def _extract_frontmatter(self, content: str) -> Dict[str, Any]:
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return {}
        try:
            import yaml
            return yaml.safe_load(match.group(1)) or {}
        except Exception:
            return {}
    
    def _extract_body(self, content: str) -> str:
        match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        if match:
            return content[match.end():].strip()
        return content.strip()
    
    def _extract_rules(self, content: str) -> List[str]:
        rules = []
        for section in ["Rules", "Guidelines", "Constraints", "Behavior"]:
            pattern = rf"## {section}\s*\n(.*?)(?=^## |\Z)"
            match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
            if match:
                for line in match.group(1).split("\n"):
                    line = line.strip()
                    if line.startswith("- ") or line.startswith("* "):
                        rules.append(line[2:])
                if rules:
                    break
        return rules
