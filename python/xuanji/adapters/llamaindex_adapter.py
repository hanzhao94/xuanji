# -*- coding: utf-8 -*-
"""
LlamaIndex 兼容适配器

读取 LlamaIndex 项目配置。
支持：
- Index 配置
- Query Engine 配置
- Tool 定义
- 文档/知识库
"""
import os
import json
import re
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class LlamaIndexAdapter(BaseAdapter):
    """LlamaIndex 生态 → 玄机统一格式"""
    
    name = "llamaindex"
    detect_patterns = [
        "indexes/",
        "query_engine.json",
        "llama_index.json",
        "documents/",
        "data/",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Query Engines: 解析为 skills
        result.skills = self._migrate_query_engines(path)
        
        # 2. Tools: 从配置提取
        result.tools = self._migrate_tools(path)
        
        # 3. Documents: 解析为 memories
        result.memories = self._migrate_documents(path)
        
        # 4. Agent config
        result.agent_config = self._migrate_agent_config(path)
        
        return result
    
    def _migrate_query_engines(self, path: str) -> List[Skill]:
        """解析 Query Engine 配置"""
        skills = []
        
        # 1. query_engine.json
        for config_file in ['query_engine.json', 'llama_index.json', 'config.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                engines = []
                if isinstance(data, list):
                    engines = data
                elif isinstance(data, dict):
                    engines = [data]
                    if "engines" in data:
                        engines = data["engines"]
                    elif "query_engines" in data:
                        engines = data["query_engines"]
                
                for engine in engines:
                    if isinstance(engine, dict):
                        name = engine.get("name", engine.get("index_name", "query_engine"))
                        desc = engine.get("description", engine.get("purpose", ""))
                        
                        # 构建指令
                        instructions = f"# LlamaIndex Query Engine\n\n"
                        if "index_type" in engine:
                            instructions += f"**Index Type**: {engine['index_type']}\n\n"
                        if "similarity_top_k" in engine:
                            instructions += f"**Top K**: {engine['similarity_top_k']}\n\n"
                        if "response_mode" in engine:
                            instructions += f"**Response Mode**: {engine['response_mode']}\n\n"
                        if "prompt_template" in engine:
                            instructions += f"**Prompt**: {engine['prompt_template'][:500]}\n"
                        
                        skills.append(Skill(
                            name=name,
                            description=desc,
                            instructions=instructions,
                            source="llamaindex",
                            location=config_path,
                        ))
            except Exception:
                continue
        
        # 2. 解析 Python 文件中的 QueryEngine 定义
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'venv')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    py_engines = self._parse_python_engines(fpath)
                    skills.extend(py_engines)
                except Exception:
                    continue
        
        return skills
    
    def _parse_python_engines(self, filepath: str) -> List[Skill]:
        """解析 Python 中的 QueryEngine 定义"""
        skills = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        patterns = [
            r'VectorStoreIndex\.as_query_engine\s*\(([^)]*)\)',
            r'QueryEngine\s*\(([^)]*)\)',
            r'RetrieverQueryEngine\.from_args\s*\(([^)]*)\)',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.DOTALL):
                params = match.group(1)
                # 提取关键参数
                top_k_match = re.search(r'similarity_top_k\s*=\s*(\d+)', params)
                response_mode_match = re.search(r'response_mode\s*=\s*["\']([^"\']+)["\']', params)
                
                instructions = f"# LlamaIndex Query Engine\n\n"
                if top_k_match:
                    instructions += f"**Top K**: {top_k_match.group(1)}\n\n"
                if response_mode_match:
                    instructions += f"**Response Mode**: {response_mode_match.group(1)}\n\n"
                
                skills.append(Skill(
                    name=f"llamaindex_engine_{len(skills)+1}",
                    description="LlamaIndex Query Engine",
                    instructions=instructions,
                    source="llamaindex",
                    location=filepath,
                ))
        
        return skills
    
    def _migrate_tools(self, path: str) -> List[Tool]:
        """提取 LlamaIndex tools"""
        tools = []
        
        # 1. tools/ 目录
        tools_dir = os.path.join(path, 'tools')
        if os.path.isdir(tools_dir):
            for f in os.listdir(tools_dir):
                if f.endswith(('.py', '.json')):
                    fpath = os.path.join(tools_dir, f)
                    if f.endswith('.json'):
                        try:
                            with open(fpath, 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            if isinstance(data, list):
                                for t in data:
                                    if isinstance(t, dict):
                                        tools.append(Tool(
                                            name=t.get("name", "unnamed"),
                                            description=t.get("description", ""),
                                            parameters=t.get("parameters", {}),
                                            source="llamaindex",
                                            location=fpath,
                                        ))
                        except Exception:
                            continue
                    else:
                        try:
                            file_tools = self._parse_python_tools(fpath)
                            tools.extend(file_tools)
                        except Exception:
                            continue
        
        # 2. 解析 Python 文件中的 QueryEngineTool / FunctionTool
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'venv')]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fpath = os.path.join(root, f)
                try:
                    file_tools = self._parse_python_tools(fpath)
                    tools.extend(file_tools)
                except Exception:
                    continue
        
        return tools
    
    def _parse_python_tools(self, filepath: str) -> List[Tool]:
        """解析 Python 中的工具定义"""
        tools = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        patterns = [
            r'QueryEngineTool\s*\(([^)]+)\)',
            r'FunctionTool\s*\(([^)]+)\)',
            r'@tool\s*\n\s*def\s+(\w+)',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.DOTALL):
                if match.group(1):
                    params = match.group(1)
                    name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', params)
                    desc_match = re.search(r'description\s*=\s*["\']([^"\']+)["\']', params)
                    name = name_match.group(1) if name_match else "unnamed"
                    desc = desc_match.group(1) if desc_match else ""
                else:
                    name = match.group(2) if len(match.groups()) > 1 else "unnamed"
                    desc = ""
                
                tools.append(Tool(
                    name=name,
                    description=desc,
                    parameters={"type": "object", "properties": {}},
                    source="llamaindex",
                    location=filepath,
                ))
        
        return tools
    
    def _migrate_documents(self, path: str) -> List[Memory]:
        """解析文档/知识库"""
        memories = []
        
        for subdir in ['documents', 'data', 'knowledge', 'docs']:
            subdir_path = os.path.join(path, subdir)
            if not os.path.isdir(subdir_path):
                continue
            for f in os.listdir(subdir_path):
                if f.endswith(('.md', '.txt', '.json', '.yaml')):
                    fpath = os.path.join(subdir_path, f)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as fh:
                            if f.endswith('.json'):
                                data = json.load(fh)
                                content = json.dumps(data, ensure_ascii=False)[:1000]
                            elif f.endswith(('.yaml', '.yml')):
                                data = yaml.safe_load(fh)
                                content = yaml.dump(data, allow_unicode=True)[:1000]
                            else:
                                content = fh.read()[:1000]
                        
                        memories.append(Memory(
                            content=content,
                            memory_type="semantic",
                            importance=0.6,
                            tags=["llamaindex", subdir, f],
                            source="llamaindex",
                        ))
                    except Exception:
                        continue
        
        return memories
    
    def _migrate_agent_config(self, path: str) -> Optional[AgentConfig]:
        """解析 Agent 配置"""
        for config_file in ['agent.json', 'llama_agent.json', 'config.json']:
            config_path = os.path.join(path, config_file)
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                agent = data.get("agent", data.get("llama_agent", data))
                if isinstance(agent, dict):
                    return AgentConfig(
                        name=agent.get("name", "llamaindex_agent"),
                        persona=agent.get("system_prompt", agent.get("instructions", ""))[:500],
                        source="llamaindex",
                    )
            except Exception:
                continue
        return None
