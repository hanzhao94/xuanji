# -*- coding: utf-8 -*-
"""
AutoGen 兼容适配器

读取 AutoGen (autogen) 项目配置。
支持：
- AssistantAgent / UserProxyAgent 配置
- GroupChat 配置
- Function calling 定义
- 对话历史
"""
import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class AutoGenAdapter(BaseAdapter):
    """AutoGen 生态 → 玄机统一格式"""
    
    name = "autogen"
    detect_patterns = [
        "autogen_config.json",
        "agents/",
        "group_chat.json",
        "conversations/",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Agents
        result.agent_config = self._migrate_agents(path)
        
        # 2. Tools
        result.tools = self._migrate_tools(path)
        
        # 3. Skills (GroupChat)
        result.skills = self._migrate_group_chats(path)
        
        # 4. Memory
        result.memories = self._migrate_conversations(path)
        
        return result
    
    def _migrate_agents(self, path: str) -> Optional[AgentConfig]:
        """解析 AutoGen agents"""
        agents = []
        
        # JSON 配置
        for config_file in ['autogen_config.json', 'config.json', 'agents.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if "agents" in data:
                    for agent in data["agents"]:
                        if isinstance(agent, dict):
                            agents.append(agent)
            except Exception:
                continue
        
        # Python 文件
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'venv')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    py_agents = self._parse_python_agents(fpath)
                    agents.extend(py_agents)
                except Exception:
                    continue
        
        if not agents:
            return None
        
        primary = agents[0]
        return AgentConfig(
            name=primary.get("name", "autogen_agent"),
            persona=primary.get("system_message", primary.get("description", ""))[:500],
            rules=self._extract_rules(primary.get("system_message", "")),
            source="autogen",
        )
    
    def _parse_python_agents(self, filepath: str) -> List[Dict]:
        """解析 Python 文件中的 Agent 定义"""
        agents = []
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for match in re.finditer(r'(?:AssistantAgent|UserProxyAgent|ConversableAgent)\s*\(', content):
            start = match.start()
            paren_depth = 0
            end = start
            for i, ch in enumerate(content[start:]):
                if ch == '(': paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                    if paren_depth == 0:
                        end = start + i + 1
                        break
            
            agent_text = content[start:end]
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', agent_text)
            sys_match = re.search(r'system_message\s*=\s*["\']([^"\']+)["\']', agent_text)
            
            agents.append({
                "name": name_match.group(1) if name_match else "unnamed",
                "system_message": sys_match.group(1) if sys_match else "",
                "source": filepath,
            })
        
        return agents
    
    def _migrate_tools(self, path: str) -> List[Tool]:
        """提取工具定义"""
        tools = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'venv')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                    for match in re.finditer(r'(?:@register_tool|def\s+\w+_tool)\s*\n\s*def\s+(\w+)', content):
                        name = match.group(1)
                        tools.append(Tool(
                            name=name, description="",
                            parameters={"type": "object", "properties": {}},
                            source="autogen", location=fpath,
                        ))
                except Exception:
                    continue
        return tools
    
    def _migrate_group_chats(self, path: str) -> List[Skill]:
        """解析 GroupChat"""
        skills = []
        for config_file in ['group_chat.json', 'autogen_config.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if "group_chat" in data:
                    gc = data["group_chat"]
                    skills.append(Skill(
                        name=gc.get("name", "group_chat"),
                        description=gc.get("description", "AutoGen GroupChat"),
                        instructions=f"# AutoGen GroupChat\n\n**Method**: {gc.get('speaker_selection_method', 'auto')}",
                        source="autogen", location=config_path,
                    ))
            except Exception:
                continue
        return skills
    
    def _migrate_conversations(self, path: str) -> List[Memory]:
        """从对话历史提取记忆"""
        memories = []
        conv_dir = os.path.join(path, 'conversations')
        if not os.path.isdir(conv_dir):
            return memories
        for f in os.listdir(conv_dir):
            if not f.endswith('.json'):
                continue
            try:
                with open(os.path.join(conv_dir, f), 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    for msg in data[:50]:
                        if isinstance(msg, dict) and msg.get("content"):
                            memories.append(Memory(
                                content=str(msg["content"])[:500],
                                memory_type="episodic", importance=0.5,
                                tags=["autogen", "conversation"], source="autogen",
                            ))
            except Exception:
                continue
        return memories
    
    def _extract_rules(self, system_message: str) -> List[str]:
        rules = []
        for line in system_message.split('\n'):
            line = line.strip()
            if line.startswith('- ') or line.startswith('* '):
                rules.append(line[2:])
        return rules[:10]
