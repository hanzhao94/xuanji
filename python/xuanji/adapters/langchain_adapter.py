# -*- coding: utf-8 -*-
"""
LangChain 兼容适配器

读取 LangChain projects，自动解析 chains/agents/tools 配置。
支持：
- LangChain Expression Language (LCEL) chains
- Agent configurations
- Tool definitions
- Memory configurations
- Prompt templates
"""
import os
import json
import re
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class LangChainAdapter(BaseAdapter):
    """LangChain 生态 → 玄机统一格式"""
    
    name = "langchain"
    detect_patterns = [
        "chains/",
        "agents/",
        "tools/",
        "langchain.json",
        "langgraph.json",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Tools: 从 Python 文件/配置中提取工具
        result.tools = self._migrate_tools(path)
        
        # 2. Chains: 解析为 skills
        result.skills = self._migrate_chains(path)
        
        # 3. Agents: 解析为 AgentConfig
        result.agent_config = self._migrate_agents(path)
        
        # 4. Memory: 从对话历史提取
        result.memories = self._migrate_memory(path)
        
        return result
    
    def _migrate_tools(self, path: str) -> List[Tool]:
        """
        从 LangChain 项目中提取工具。
        
        支持：
        - Python 文件中的 @tool 装饰器
        - tools/ 目录下的工具定义
        - langchain.json 中的工具配置
        """
        tools = []
        
        # 1. 解析 Python 文件中的 @tool 装饰器
        for root, dirs, files in os.walk(path):
            # 跳过虚拟环境和缓存
            dirs[:] = [d for d in dirs if d not in 
                       ('__pycache__', '.venv', 'venv', 'node_modules')]
            
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    file_tools = self._parse_python_tools(fpath)
                    tools.extend(file_tools)
                except Exception:
                    continue
        
        # 2. 解析 JSON 配置
        for config_file in ['langchain.json', 'tools.json', 'config.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                json_tools = self._parse_json_tools(data, config_path)
                tools.extend(json_tools)
            except Exception:
                continue
        
        # 3. 解析 tools/ 目录
        tools_dir = os.path.join(path, 'tools')
        if os.path.isdir(tools_dir):
            for f in os.listdir(tools_dir):
                if f.endswith('.py'):
                    fpath = os.path.join(tools_dir, f)
                    try:
                        file_tools = self._parse_python_tools(fpath)
                        tools.extend(file_tools)
                    except Exception:
                        continue
        
        return tools
    
    def _parse_python_tools(self, filepath: str) -> List[Tool]:
        """解析 Python 文件中的 @tool 装饰器"""
        tools = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 匹配 @tool 装饰器
        # @tool("name") 或 @tool 或 @tool(name="xxx")
        pattern = r'@tool(?:\s*\(\s*(?:"([^"]+)"|\'([^\')\']+)|name\s*=\s*["\']([^"\']+)["\'])?\s*\))?'
        
        matches = re.finditer(pattern, content)
        for match in matches:
            # 获取工具名
            name = match.group(1) or match.group(2) or match.group(3)
            if not name:
                # 尝试从后面的 def 语句提取
                start = match.end()
                def_match = re.search(r'def\s+(\w+)', content[start:start+200])
                if def_match:
                    name = def_match.group(1)
                else:
                    continue
            
            # 获取描述（docstring）
            def_start = match.end()
            def_match = re.search(r'def\s+\w+\s*\([^)]*\)\s*(?:->\s*\w+\s*)?:', content[def_start:])
            if def_match:
                func_body_start = def_start + def_match.end()
                docstring_match = re.search(r'"""(.*?)"""', content[func_body_start:func_body_start+500], re.DOTALL)
                description = ''
                if docstring_match:
                    description = docstring_match.group(1).strip().split('\n')[0][:200]
                
                tools.append(Tool(
                    name=name,
                    description=description,
                    parameters={"type": "object", "properties": {}},
                    source="langchain",
                    location=filepath,
                ))
        
        return tools
    
    def _parse_json_tools(self, data: Dict, source: str) -> List[Tool]:
        """从 JSON 配置解析工具"""
        tools = []
        
        # 常见的工具配置格式
        tool_configs = []
        
        if isinstance(data, dict):
            # {"tools": [...]}
            if "tools" in data:
                tool_configs = data["tools"]
            # {"tools": {"name": {...}}}
            elif "tool" in data:
                tool_configs = [data["tool"]]
        
        if isinstance(tool_configs, list):
            for tc in tool_configs:
                if isinstance(tc, dict):
                    tools.append(Tool(
                        name=tc.get("name", "unnamed"),
                        description=tc.get("description", ""),
                        parameters=tc.get("parameters", tc.get("args_schema", {})),
                        source="langchain",
                        location=source,
                    ))
                elif isinstance(tc, str):
                    # 工具名（需要从其他地方找实现）
                    tools.append(Tool(
                        name=tc,
                        description="",
                        parameters={"type": "object", "properties": {}},
                        source="langchain",
                        location=source,
                    ))
        
        return tools
    
    def _migrate_chains(self, path: str) -> List[Skill]:
        """
        解析 LangChain chains 为 skills。
        
        chains/ 目录下的每个 JSON/YAML 文件代表一个 chain，
        转为玄机 skill。
        """
        skills = []
        
        chains_dir = os.path.join(path, 'chains')
        if not os.path.isdir(chains_dir):
            return skills
        
        for f in os.listdir(chains_dir):
            if not f.endswith(('.json', '.yaml', '.yml')):
                continue
            
            fpath = os.path.join(chains_dir, f)
            try:
                skill = self._parse_chain_file(fpath, f)
                if skill:
                    skills.append(skill)
            except Exception:
                continue
        
        return skills
    
    def _parse_chain_file(self, filepath: str, filename: str) -> Optional[Skill]:
        """解析单个 chain 文件"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                if filepath.endswith('.json'):
                    data = json.load(f)
                else:
                    import yaml
                    data = yaml.safe_load(f)
            
            if not isinstance(data, dict):
                return None
            
            name = data.get("name", filename.rsplit('.', 1)[0])
            description = data.get("description", data.get("purpose", ""))
            
            # 将 chain 配置转为 skill 指令
            instructions = self._chain_to_instructions(data)
            
            return Skill(
                name=name,
                description=description,
                instructions=instructions,
                source="langchain",
                location=filepath,
            )
        except Exception:
            return None
    
    def _chain_to_instructions(self, data: Dict) -> str:
        """将 LangChain chain 配置转为 skill 指令文本"""
        lines = ["# LangChain Chain\n"]
        
        if "description" in data:
            lines.append(f"**Description**: {data['description']}\n")
        
        if "input_variables" in data:
            lines.append(f"**Input**: {', '.join(data['input_variables'])}\n")
        
        if "output_variables" in data:
            lines.append(f"**Output**: {', '.join(data['output_variables'])}\n")
        
        # 添加 chain 步骤
        if "steps" in data:
            lines.append("\n## Steps\n")
            for i, step in enumerate(data["steps"], 1):
                if isinstance(step, dict):
                    step_type = step.get("type", "unknown")
                    step_desc = step.get("description", "")
                    lines.append(f"{i}. [{step_type}] {step_desc}")
                else:
                    lines.append(f"{i}. {step}")
        
        return "\n".join(lines)
    
    def _migrate_agents(self, path: str) -> Optional[AgentConfig]:
        """解析 Agent 配置"""
        agents_dir = os.path.join(path, 'agents')
        if not os.path.isdir(agents_dir):
            # 尝试根目录
            for config_file in ['agent.json', 'config.json', 'langchain.json']:
                config_path = os.path.join(path, config_file)
                if os.path.isfile(config_path):
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if "agent" in data:
                            agent_data = data["agent"]
                            return AgentConfig(
                                name=agent_data.get("name", "langchain_agent"),
                                persona=agent_data.get("system_message", agent_data.get("prompt", ""))[:500],
                                rules=[],
                                source="langchain",
                            )
                    except Exception:
                        continue
            return None
        
        # 解析 agents/ 目录
        for f in os.listdir(agents_dir):
            if f.endswith('.json'):
                fpath = os.path.join(agents_dir, f)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    return AgentConfig(
                        name=data.get("name", f.rsplit('.', 1)[0]),
                        persona=data.get("system_message", data.get("prompt", ""))[:500],
                        source="langchain",
                    )
                except Exception:
                    continue
        
        return None
    
    def _migrate_memory(self, path: str) -> List[Memory]:
        """从对话历史提取记忆"""
        memories = []
        
        # 常见的对话历史位置
        for history_file in ['history.json', 'conversations.json', 'chat_history.json']:
            hpath = os.path.join(path, history_file)
            if not os.path.isfile(hpath):
                continue
            
            try:
                with open(hpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if isinstance(data, list):
                    for item in data[:50]:  # 最多50条
                        if isinstance(item, dict):
                            content = item.get("content", item.get("text", ""))
                            if content:
                                memories.append(Memory(
                                    content=str(content)[:500],
                                    memory_type="episodic",
                                    importance=item.get("importance", 0.5),
                                    tags=["langchain", "conversation"],
                                    source="langchain",
                                ))
                elif isinstance(data, dict):
                    messages = data.get("messages", data.get("history", []))
                    if isinstance(messages, list):
                        for msg in messages[:50]:
                            if isinstance(msg, dict):
                                content = msg.get("content", msg.get("text", ""))
                                if content:
                                    memories.append(Memory(
                                        content=str(content)[:500],
                                        memory_type="episodic",
                                        importance=0.5,
                                        tags=["langchain"],
                                        source="langchain",
                                    ))
            except Exception:
                continue
        
        return memories
