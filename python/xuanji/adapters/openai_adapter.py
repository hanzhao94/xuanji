# -*- coding: utf-8 -*-
"""
OpenAI 兼容适配器

读取 OpenAI Assistants / GPTs / Playground 配置。
支持：
- Assistants API 配置 (assistants.json)
- GPTs 配置
- Playground 对话历史
- Function calling 定义
"""
import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class OpenAIAdapter(BaseAdapter):
    """OpenAI 生态 → 玄机统一格式"""
    
    name = "openai"
    detect_patterns = [
        "assistants.json",
        "openai_config.json",
        "gpts/",
        "functions/",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Assistants: 解析为 AgentConfig
        result.agent_config = self._migrate_assistants(path)
        
        # 2. Functions/Tools: 解析为 Tool
        result.tools = self._migrate_functions(path)
        
        # 3. GPTs: 解析为 Skill
        result.skills = self._migrate_gpts(path)
        
        # 4. 对话历史: 解析为 Memory
        result.memories = self._migrate_conversations(path)
        
        return result
    
    def _migrate_assistants(self, path: str) -> Optional[AgentConfig]:
        """解析 OpenAI Assistants"""
        # 1. assistants.json
        assistants_file = os.path.join(path, 'assistants.json')
        if os.path.isfile(assistants_file):
            try:
                with open(assistants_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                assistants = []
                if isinstance(data, list):
                    assistants = data
                elif isinstance(data, dict) and "data" in data:
                    assistants = data["data"]
                elif isinstance(data, dict) and "assistants" in data:
                    assistants = data["assistants"]
                
                if assistants:
                    first = assistants[0]
                    return AgentConfig(
                        name=first.get("name", "openai_assistant"),
                        persona=first.get("instructions", first.get("description", ""))[:500],
                        rules=[],
                        source="openai",
                    )
            except Exception:
                pass
        
        # 2. openai_config.json
        config_file = os.path.join(path, 'openai_config.json')
        if os.path.isfile(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                assistant = data.get("assistant", data.get("agent", {}))
                if isinstance(assistant, dict):
                    return AgentConfig(
                        name=assistant.get("name", "openai_assistant"),
                        persona=assistant.get("instructions", assistant.get("system_prompt", ""))[:500],
                        source="openai",
                    )
            except Exception:
                pass
        
        return None
    
    def _migrate_functions(self, path: str) -> List[Tool]:
        """解析 OpenAI function calling 定义"""
        tools = []
        
        # 1. functions/ 目录
        functions_dir = os.path.join(path, 'functions')
        if os.path.isdir(functions_dir):
            for f in os.listdir(functions_dir):
                if f.endswith('.json'):
                    fpath = os.path.join(functions_dir, f)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as fh:
                            data = json.load(fh)
                        tools.append(Tool(
                            name=data.get("name", f.rsplit('.', 1)[0]),
                            description=data.get("description", ""),
                            parameters=data.get("parameters", {}),
                            source="openai",
                            location=fpath,
                        ))
                    except Exception:
                        continue
        
        # 2. 从 assistants 配置中提取 tools
        for config_file in ['assistants.json', 'openai_config.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 遍历所有 assistants
                assistants_data = []
                if isinstance(data, list):
                    assistants_data = data
                elif isinstance(data, dict):
                    if "data" in data:
                        assistants_data = data["data"]
                    elif "assistants" in data:
                        assistants_data = data["assistants"]
                    else:
                        assistants_data = [data]
                
                for assistant in assistants_data:
                    if not isinstance(assistant, dict):
                        continue
                    openai_tools = assistant.get("tools", [])
                    for tool in openai_tools:
                        if isinstance(tool, dict) and tool.get("type") == "function":
                            func = tool.get("function", {})
                            tools.append(Tool(
                                name=func.get("name", "unnamed"),
                                description=func.get("description", ""),
                                parameters=func.get("parameters", {}),
                                source="openai",
                                location=config_path,
                            ))
            except Exception:
                continue
        
        # 3. 解析 Python 文件中的 function 定义
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'venv')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    file_tools = self._parse_python_functions(fpath)
                    tools.extend(file_tools)
                except Exception:
                    continue
        
        return tools
    
    def _parse_python_functions(self, filepath: str) -> List[Tool]:
        """解析 Python 中的 function calling 定义"""
        tools = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 匹配 @openai_function 或 tools=[...] 格式
        patterns = [
            r'@openai_function\s*\n\s*def\s+(\w+)',
            r'"name"\s*:\s*"(\w+)"\s*,\s*"description"\s*:\s*"([^"]+)"',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content):
                if len(match.groups()) >= 2:
                    name, desc = match.group(1), match.group(2)
                else:
                    name = match.group(1)
                    desc = ""
                
                tools.append(Tool(
                    name=name,
                    description=desc,
                    parameters={"type": "object", "properties": {}},
                    source="openai",
                    location=filepath,
                ))
        
        return tools
    
    def _migrate_gpts(self, path: str) -> List[Skill]:
        """解析 GPTs 配置"""
        skills = []
        
        gpts_dir = os.path.join(path, 'gpts')
        if not os.path.isdir(gpts_dir):
            return skills
        
        for f in os.listdir(gpts_dir):
            if f.endswith('.json'):
                fpath = os.path.join(gpts_dir, f)
                try:
                    with open(fpath, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                    
                    name = data.get("name", f.rsplit('.', 1)[0])
                    description = data.get("description", "")
                    instructions = data.get("instructions", data.get("prompt", ""))
                    
                    skills.append(Skill(
                        name=name,
                        description=description,
                        instructions=instructions,
                        source="openai",
                        location=fpath,
                    ))
                except Exception:
                    continue
        
        return skills
    
    def _migrate_conversations(self, path: str) -> List[Memory]:
        """从对话历史提取记忆"""
        memories = []
        
        for conv_file in ['conversations.json', 'playground_history.json', 'chat_history.json']:
            conv_path = os.path.join(path, conv_file)
            if not os.path.isfile(conv_path):
                continue
            try:
                with open(conv_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                messages = []
                if isinstance(data, list):
                    messages = data
                elif isinstance(data, dict):
                    messages = data.get("messages", data.get("conversations", []))
                
                for msg in messages[:50]:
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        if content:
                            memories.append(Memory(
                                content=str(content)[:500],
                                memory_type="episodic",
                                importance=0.5,
                                tags=["openai", "conversation"],
                                source="openai",
                            ))
            except Exception:
                continue
        
        return memories
