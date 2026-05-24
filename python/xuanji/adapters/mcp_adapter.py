# -*- coding: utf-8 -*-
"""
MCP 通用客户端适配器

连接任意 MCP Server，自动发现并转为玄机工具。
MCP (Model Context Protocol) 是 Anthropic 提出的标准协议，
Claude Desktop、Cursor、Windsurf 等都支持。

支持：
- stdio MCP servers（本地进程）
- SSE MCP servers（远程HTTP）
- MCP 配置格式 (~/.config/mcp/*.json)
"""
import os
import json
import asyncio
from typing import Dict, Any, List, Optional

from .base import (
    BaseAdapter, Skill, Memory, Tool, AgentConfig, MigrationResult
)


class MCPAdapter(BaseAdapter):
    """任意 MCP Server → 玄机统一格式"""
    
    name = "mcp"
    detect_patterns = [
        ".mcp.json",
        "mcp.json",
        ".mcp/",
    ]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        
        # 1. 发现 MCP 配置
        result.mcp_servers = self._discover_mcp_configs(path)
        
        # 2. 连接每个 MCP Server 获取 tools
        for server in result.mcp_servers:
            try:
                tools = self._fetch_mcp_tools(server)
                result.tools.extend(tools)
            except Exception as e:
                result.warnings.append(
                    f"Failed to connect to MCP server '{server.get('name', 'unknown')}': {e}"
                )
        
        return result
    
    def _discover_mcp_configs(self, path: str) -> List[Dict]:
        """发现 MCP 配置"""
        servers = []
        
        # 常见 MCP 配置位置
        config_paths = [
            os.path.join(path, ".mcp.json"),
            os.path.join(path, "mcp.json"),
            os.path.join(path, ".mcp", "config.json"),
            os.path.expanduser("~/.config/mcp/servers.json"),
            os.path.expanduser("~/.mcp/servers.json"),
        ]
        
        for cp in config_paths:
            if not os.path.isfile(cp):
                continue
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # 解析配置格式
                parsed = self._parse_mcp_config(data, cp)
                servers.extend(parsed)
            except Exception:
                continue
        
        return servers
    
    def _parse_mcp_config(self, data: Dict, source_path: str) -> List[Dict]:
        """解析 MCP 配置为标准格式"""
        servers = []
        
        # 格式1: {"mcpServers": {"name": {"command": "...", "args": [...]}}}
        if "mcpServers" in data:
            for name, config in data["mcpServers"].items():
                servers.append({
                    "name": name,
                    "command": config.get("command", ""),
                    "args": config.get("args", []),
                    "env": config.get("env", {}),
                    "transport": "stdio",
                    "source": source_path,
                })
        
        # 格式2: 直接是 server 列表
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    servers.append({
                        "name": item.get("name", "unnamed"),
                        "command": item.get("command", ""),
                        "args": item.get("args", []),
                        "env": item.get("env", {}),
                        "transport": item.get("transport", "stdio"),
                        "url": item.get("url", ""),
                        "source": source_path,
                    })
        
        # 格式3: {"servers": [...]}
        elif "servers" in data:
            for item in data["servers"]:
                if isinstance(item, dict):
                    servers.append({
                        "name": item.get("name", "unnamed"),
                        "command": item.get("command", ""),
                        "args": item.get("args", []),
                        "transport": item.get("transport", "stdio"),
                        "url": item.get("url", ""),
                        "source": source_path,
                    })
        
        return servers
    
    def _fetch_mcp_tools(self, server: Dict) -> List[Tool]:
        """
        连接 MCP Server 获取 tools 列表。
        
        这里用 subprocess 启动 stdio MCP server，
        通过 JSON-RPC 调用 tools/list。
        """
        tools = []
        
        command = server.get("command", "")
        if not command:
            return tools
        
        args = server.get("args", [])
        env = server.get("env", {})
        
        # 启动 MCP server 进程
        import subprocess
        import time
        
        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **env},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            
            # 发送 JSON-RPC 请求
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
            
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
            
            # 读取响应（超时 5 秒）
            time.sleep(1)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"MCP server exited: {stderr[:200]}")
            
            # 非阻塞读取
            output = ""
            deadline = time.time() + 5
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line:
                    output += line.decode("utf-8", errors="replace")
                    if "tools" in output.lower() or "result" in output.lower():
                        break
                else:
                    time.sleep(0.2)
            
            # 解析响应
            if output:
                for line in output.strip().split("\n"):
                    try:
                        response = json.loads(line)
                        result = response.get("result", {})
                        mcp_tools = result.get("tools", [])
                        
                        for t in mcp_tools:
                            tools.append(Tool(
                                name=t.get("name", "unnamed"),
                                description=t.get("description", ""),
                                parameters=t.get("inputSchema", {}),
                                source=f"mcp:{server['name']}",
                                location=server.get("source", ""),
                            ))
                        break
                    except json.JSONDecodeError:
                        continue
            
            proc.terminate()
            proc.wait(timeout=2)
            
        except FileNotFoundError:
            raise RuntimeError(f"Command not found: {command}")
        except Exception as e:
            if "proc" in locals():
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            raise
        
        return tools


# --- 便捷函数 ---

def connect_mcp(command: str, args: List[str] = None, env: Dict = None) -> List[Tool]:
    """快速连接单个 MCP Server"""
    adapter = MCPAdapter()
    server = {
        "name": command.split("/")[-1],
        "command": command,
        "args": args or [],
        "env": env or {},
        "transport": "stdio",
    }
    return adapter._fetch_mcp_tools(server)
