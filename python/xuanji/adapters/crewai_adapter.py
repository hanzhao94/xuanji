# -*- coding: utf-8 -*-
"""
CrewAI 兼容适配器

读取 CrewAI 项目配置。
支持：
- Crew 配置
- Agent 角色定义
- Task 定义
- Tool 定义
- 知识库
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


class CrewAIAdapter(BaseAdapter):
    """CrewAI 生态 → 玄机统一格式"""
    
    name = "crewai"
    detect_patterns = [
        "crew.py",
        "agents/",
        "tasks/",
        "crew.json",
        "pyproject.toml",  # 可能有 crewai 依赖
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. Agents: 从 Python/JSON 提取
        agents = self._migrate_agents(path)
        if agents:
            result.agent_config = agents[0]  # 取第一个作为主要配置
            # 合并所有 agent persona
            if len(agents) > 1:
                personas = [a.persona for a in agents if a.persona]
                if personas:
                    result.agent_config.persona = "\n\n---\n\n".join(personas)[:500]
        
        # 2. Tasks: 转为 skills
        result.skills = self._migrate_tasks(path)
        
        # 3. Tools: 提取工具定义
        result.tools = self._migrate_tools(path)
        
        # 4. Memory: 从 output/knowledge 提取
        result.memories = self._migrate_knowledge(path)
        
        return result
    
    def _migrate_agents(self, path: str) -> List[AgentConfig]:
        """解析 CrewAI agents"""
        configs = []
        
        # 1. 解析 agents/ 目录
        agents_dir = os.path.join(path, 'agents')
        if os.path.isdir(agents_dir):
            for f in os.listdir(agents_dir):
                if f.endswith(('.py', '.json', '.yaml', '.yml')):
                    fpath = os.path.join(agents_dir, f)
                    agent = self._parse_agent_file(fpath, f)
                    if agent:
                        configs.append(agent)
        
        # 2. 解析 crew.py 中的 Agent 定义
        crew_py = os.path.join(path, 'crew.py')
        if os.path.isfile(crew_py):
            try:
                py_agents = self._parse_crew_py(crew_py)
                configs.extend(py_agents)
            except Exception:
                pass
        
        # 3. 解析 crew.json
        crew_json = os.path.join(path, 'crew.json')
        if os.path.isfile(crew_json):
            try:
                with open(crew_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if "agents" in data:
                    for agent_data in data["agents"]:
                        if isinstance(agent_data, dict):
                            configs.append(AgentConfig(
                                name=agent_data.get("role", agent_data.get("name", "crewai_agent")),
                                persona=agent_data.get("goal", agent_data.get("backstory", ""))[:500],
                                rules=[],
                                source="crewai",
                            ))
            except Exception:
                pass
        
        return configs
    
    def _parse_agent_file(self, filepath: str, filename: str) -> Optional[AgentConfig]:
        """解析单个 agent 文件"""
        try:
            if filepath.endswith('.json'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return AgentConfig(
                    name=data.get("role", data.get("name", filename.rsplit('.', 1)[0])),
                    persona=data.get("goal", data.get("backstory", ""))[:500],
                    source="crewai",
                )
            elif filepath.endswith(('.yaml', '.yml')):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                return AgentConfig(
                    name=data.get("role", data.get("name", filename.rsplit('.', 1)[0])),
                    persona=data.get("goal", data.get("backstory", ""))[:500],
                    source="crewai",
                )
            elif filepath.endswith('.py'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 匹配 Agent(...) 定义
                agent_match = re.search(r'Agent\s*\((.*?)\)', content, re.DOTALL)
                if agent_match:
                    params = agent_match.group(1)
                    role_match = re.search(r'role\s*=\s*["\']([^"\']+)["\']', params)
                    goal_match = re.search(r'goal\s*=\s*["\']([^"\']+)["\']', params)
                    backstory_match = re.search(r'backstory\s*=\s*["\']([^"\']+)["\']', params)
                    
                    return AgentConfig(
                        name=role_match.group(1) if role_match else filename.rsplit('.', 1)[0],
                        persona=(goal_match.group(1) if goal_match else "")[:500],
                        source="crewai",
                    )
        except Exception:
            pass
        return None
    
    def _parse_crew_py(self, filepath: str) -> List[AgentConfig]:
        """解析 crew.py 中的多个 Agent"""
        configs = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 匹配所有 Agent(...) 定义
        for match in re.finditer(r'Agent\s*\(([^)]+)\)', content, re.DOTALL):
            params = match.group(1)
            role_match = re.search(r'role\s*=\s*["\']([^"\']+)["\']', params)
            goal_match = re.search(r'goal\s*=\s*["\']([^"\']+)["\']', params)
            backstory_match = re.search(r'backstory\s*=\s*f?["\']([^"\']+)["\']', params)
            
            if role_match:
                configs.append(AgentConfig(
                    name=role_match.group(1),
                    persona=(goal_match.group(1) if goal_match else (backstory_match.group(1) if backstory_match else ""))[:500],
                    source="crewai",
                ))
        
        return configs
    
    def _migrate_tasks(self, path: str) -> List[Skill]:
        """解析 CrewAI tasks 为 skills"""
        skills = []
        
        tasks_dir = os.path.join(path, 'tasks')
        if os.path.isdir(tasks_dir):
            for f in os.listdir(tasks_dir):
                if f.endswith(('.json', '.yaml', '.yml', '.py')):
                    fpath = os.path.join(tasks_dir, f)
                    skill = self._parse_task_file(fpath, f)
                    if skill:
                        skills.append(skill)
        
        # 也解析 crew.py 中的 Task 定义
        crew_py = os.path.join(path, 'crew.py')
        if os.path.isfile(crew_py):
            try:
                with open(crew_py, 'r', encoding='utf-8') as f:
                    content = f.read()
                for match in re.finditer(r'Task\s*\(([^)]+)\)', content, re.DOTALL):
                    params = match.group(1)
                    desc_match = re.search(r'description\s*=\s*(?:f?["\']|"""|\'\'\')(.*?)(?:["\']|"""|\'\'\')', params, re.DOTALL)
                    if desc_match:
                        desc = desc_match.group(1).strip()[:200]
                        skills.append(Skill(
                            name=f"crewai_task_{len(skills)+1}",
                            description=desc,
                            instructions=f"# CrewAI Task\n\n{desc}",
                            source="crewai",
                            location=crew_py,
                        ))
            except Exception:
                pass
        
        return skills
    
    def _parse_task_file(self, filepath: str, filename: str) -> Optional[Skill]:
        """解析单个 task 文件"""
        try:
            if filepath.endswith('.json'):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return Skill(
                    name=data.get("name", filename.rsplit('.', 1)[0]),
                    description=data.get("description", data.get("expected_output", ""))[:200],
                    instructions=f"# CrewAI Task\n\n**Description**: {data.get('description', '')}\n\n**Expected Output**: {data.get('expected_output', '')}",
                    source="crewai",
                    location=filepath,
                )
            elif filepath.endswith(('.yaml', '.yml')):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                return Skill(
                    name=data.get("name", filename.rsplit('.', 1)[0]),
                    description=data.get("description", data.get("expected_output", ""))[:200],
                    instructions=f"# CrewAI Task\n\n**Description**: {data.get('description', '')}\n\n**Expected Output**: {data.get('expected_output', '')}",
                    source="crewai",
                    location=filepath,
                )
        except Exception:
            pass
        return None
    
    def _migrate_tools(self, path: str) -> List[Tool]:
        """提取 CrewAI tools"""
        tools = []
        
        # 解析 tools/ 目录
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
        
        # 解析 crew.py 中的 Tool 定义
        crew_py = os.path.join(path, 'crew.py')
        if os.path.isfile(crew_py):
            try:
                with open(crew_py, 'r', encoding='utf-8') as f:
                    content = f.read()
                for match in re.finditer(r'@tool\s*\n\s*def\s+(\w+)', content):
                    name = match.group(1)
                    tools.append(Tool(
                        name=name,
                        description="",
                        parameters={"type": "object", "properties": {}},
                        source="crewai",
                        location=crew_py,
                    ))
            except Exception:
                pass
        
        return tools
    
    def _parse_python_tools(self, filepath: str) -> List[Tool]:
        """解析 Python 文件中的工具"""
        tools = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        for match in re.finditer(r'(?:@tool|class\s+\w+Tool)\s*\n(?:\s*.*?\n)*?\s*def\s+(\w+)', content):
            name = match.group(1)
            doc_match = re.search(r'"""(.*?)"""', content[match.end():match.end()+500], re.DOTALL)
            desc = doc_match.group(1).strip().split('\n')[0][:200] if doc_match else ""
            
            tools.append(Tool(
                name=name,
                description=desc,
                parameters={"type": "object", "properties": {}},
                source="crewai",
                location=filepath,
            ))
        
        return tools
    
    def _migrate_knowledge(self, path: str) -> List[Memory]:
        """从知识库/输出提取记忆"""
        memories = []
        
        # 解析 knowledge/ 或 output/ 目录
        for subdir in ['knowledge', 'output', 'results']:
            subdir_path = os.path.join(path, subdir)
            if not os.path.isdir(subdir_path):
                continue
            for f in os.listdir(subdir_path):
                if f.endswith(('.md', '.txt', '.json')):
                    fpath = os.path.join(subdir_path, f)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as fh:
                            content = fh.read()
                        memories.append(Memory(
                            content=content[:1000],
                            memory_type="semantic",
                            importance=0.7,
                            tags=["crewai", subdir, f],
                            source="crewai",
                        ))
                    except Exception:
                        continue
        
        return memories
