"""
xuanji 内置工具集

把玄玑已有的能力注册为Agent可调用的工具。
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def register_builtin_tools(registry) -> None:
    """注册所有内置工具到Agent的ToolRegistry
    
    Args:
        registry: ToolRegistry实例
    """
    
    # ── Web搜索 ──
    def _web_search(query: str, max_results: int = 5) -> str:
        """网络搜索"""
        try:
            from xuanji.web_search import search
            results = search(query, engine='bing', limit=max_results)
            if isinstance(results, list):
                out = []
                for i, r in enumerate(results[:max_results]):
                    title = r.get("title", "") if isinstance(r, dict) else getattr(r, 'title', '')
                    snippet = r.get("snippet", r.get("summary", "")) if isinstance(r, dict) else getattr(r, 'snippet', getattr(r, 'summary', ''))
                    url = r.get("url", "") if isinstance(r, dict) else getattr(r, 'url', '')
                    out.append(f"[{i+1}] {title}\n    {snippet}\n    {url}")
                return "\n\n".join(out)
            return str(results)
        except ImportError:
            return "错误：web_search模块未安装"
        except Exception as e:
            return f"搜索失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="web_search",
        description="搜索互联网，获取实时信息",
        params={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "返回结果数量（默认5）"},
            },
            "required": ["query"],
        },
        func=_web_search,
        category="web",
    )
    
    # ── 网页内容读取 ──
    def _fetch_webpage(url: str, max_chars: int = 3000) -> str:
        """抓取网页内容，提取正文"""
        try:
            from xuanji.web_reader import WebReader
            reader = WebReader()
            content = reader.read(url)
            if isinstance(content, str):
                return content[:max_chars]
            return str(content)
        except ImportError:
            return "错误：web_reader模块未安装"
        except Exception as e:
            return f"抓取失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="fetch_webpage",
        description="抓取指定URL的网页内容，提取正文文本",
        params={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页URL"},
                "max_chars": {"type": "integer", "description": "最大返回字符数（默认3000）"},
            },
            "required": ["url"],
        },
        func=_fetch_webpage,
        category="web",
    )
    
    # ── 文件读取 ──
    def _read_file(path: str) -> str:
        """读取文件内容"""
        try:
            import os
            if not os.path.exists(path):
                return f"错误：文件不存在 - {path}"
            if os.path.isdir(path):
                return f"错误：{path} 是目录"
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if len(content) > 5000:
                return content[:5000] + f"\n...（文件共{len(content)}字，已截断）"
            return content
        except PermissionError:
            return f"错误：无权限读取 - {path}"
        except Exception as e:
            return f"读取失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="read_file",
        description="读取本地文件内容（文本文件）",
        params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径或相对路径"},
            },
            "required": ["path"],
        },
        func=_read_file,
        category="file",
    )
    
    # ── 文件写入 ──
    def _write_file(path: str, content: str) -> str:
        """写入文件内容"""
        try:
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"文件已写入：{path} ({len(content)}字)"
        except Exception as e:
            return f"写入失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="write_file",
        description="写入内容到本地文件",
        params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
        func=_write_file,
        category="file",
    )
    
    # ── 目录列表 ──
    def _list_directory(path: str = ".") -> str:
        """列出目录内容"""
        try:
            import os
            entries = os.listdir(path)
            lines = []
            for name in sorted(entries):
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    lines.append(f"[DIR]  {name}/")
                else:
                    size = os.path.getsize(full)
                    lines.append(f"[FILE] {name} ({size}B)")
            return "\n".join(lines[:50]) + (f"\n...（共{len(entries)}项）" if len(entries) > 50 else "")
        except Exception as e:
            return f"列出失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="list_directory",
        description="列出目录下的文件和子目录",
        params={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（默认当前目录）"},
            },
            "required": [],
        },
        func=_list_directory,
        category="file",
    )
    
    # ── 执行Shell命令 ──
    def _run_shell(command: str, timeout: int = 30) -> str:
        """执行Shell命令"""
        try:
            import subprocess
            result = subprocess.run(
                command, capture_output=True,
                text=True, timeout=timeout, encoding='utf-8', errors='replace'
            )
            output = result.stdout
            if result.stderr:
                output += "\n[STDERR]\n" + result.stderr
            if len(output) > 5000:
                output = output[:5000] + f"\n...（输出共{len(output)}字，已截断）"
            return output
        except subprocess.TimeoutExpired:
            return f"命令超时（{timeout}秒）"
        except Exception as e:
            return f"执行失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="run_shell",
        description="执行Shell命令，返回stdout+stderr",
        params={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {"type": "integer", "description": "超时秒数（默认30）"},
            },
            "required": ["command"],
        },
        func=_run_shell,
        category="system",
    )
    
    # ── Python代码执行 ──
    def _run_python(code: str, timeout: int = 30) -> str:
        """执行Python代码"""
        try:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(code)
                f.flush()
                result = subprocess.run(
                    ['python', f.name], capture_output=True,
                    text=True, timeout=timeout, encoding='utf-8', errors='replace'
                )
                output = result.stdout
                if result.stderr:
                    output += "\n[STDERR]\n" + result.stderr
                if len(output) > 5000:
                    output = output[:5000] + f"\n...（输出已截断）"
                return output
        except subprocess.TimeoutExpired:
            return f"代码执行超时（{timeout}秒）"
        except Exception as e:
            return f"执行失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="run_python",
        description="执行Python代码片段，返回输出结果",
        params={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的Python代码"},
                "timeout": {"type": "integer", "description": "超时秒数（默认30）"},
            },
            "required": ["code"],
        },
        func=_run_python,
        category="system",
    )
    
    # ── 天气查询 ──
    def _get_weather(city: str) -> str:
        """查询天气"""
        try:
            from xuanji import web_search  # use internal web_search
            import urllib.request, json
            
            # 用wttr.in免费API
            city_encoded = urllib.parse.quote(city)
            url = f"https://wttr.in/{city_encoded}?format=j1"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            
            current = data.get("current_condition", [{}])[0]
            temp_c = current.get("temp_C", "?")
            feels = current.get("FeelsLikeC", "?")
            desc = current.get("weatherDesc", [{}])[0].get("value", "?")
            humidity = current.get("humidity", "?")
            wind = current.get("windspeedKmph", "?")
            
            return f"城市：{city}\n温度：{temp_c}°C（体感{feels}°C）\n天气：{desc}\n湿度：{humidity}%\n风速：{wind}km/h"
        except Exception as e:
            return f"天气查询失败：{type(e).__name__}: {e}"
    
    registry.register(
        name="get_weather",
        description="查询指定城市的实时天气",
        params={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名（英文或拼音，如Beijing/Shanghai）"},
            },
            "required": ["city"],
        },
        func=_get_weather,
        category="utility",
    )
    
    # ── 计算器 ──
    def _calculator(expression: str) -> str:
        """计算数学表达式"""
        try:
            # 安全计算：只允许数字和运算符
            import ast, operator
            ops = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
                ast.Pow: operator.pow,
                ast.USub: operator.neg,
            }
            tree = ast.parse(expression, mode='eval')
            def eval_node(node):
                if isinstance(node, ast.Constant):
                    return node.value
                if isinstance(node, ast.BinOp):
                    return ops[type(node.op)](eval_node(node.left), eval_node(node.right))
                if isinstance(node, ast.UnaryOp):
                    return ops[type(node.op)](eval_node(node.operand))
                raise ValueError("不支持的操作")
            result = eval_node(tree.body)
            return f"{expression} = {result}"
        except Exception as e:
            return f"计算失败：{e}"
    
    registry.register(
        name="calculator",
        description="计算数学表达式（支持加减乘除幂运算）",
        params={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式，如 3.14*2+10"},
            },
            "required": ["expression"],
        },
        func=_calculator,
        category="utility",
    )
    
    logger.info(f"Built-in tools registered: {len(registry.list_all())} tools")


# ── 便捷创建 ──
def create_agent(llm_router, model: Optional[str] = None, **kwargs):
    """创建配置好内置工具的Agent
    
    Args:
        llm_router: LLMRouter实例
        model: 使用的模型
        **kwargs: 传给AgentRunner的其他参数
    
    Returns:
        配置好的AgentRunner
    """
    from xuanji.agent_runner import AgentRunner
    
    runner = AgentRunner(llm_router, model=model, **kwargs)
    register_builtin_tools(runner.registry)
    runner._update_system_prompt()  # 更新prompt以注入工具列表
    return runner
