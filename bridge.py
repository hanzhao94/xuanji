"""
XuanJi <-> OpenClaw 桥接器

用法:
    python bridge.py "你想说的话"
    python bridge.py --chat          交互式对话模式
"""

import sys
import json
import os
import asyncio

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "python"))

from xuanji.agent_runner import AgentRunner, ToolRegistry


def register_basic_tools(reg: ToolRegistry):
    """注册基础工具"""
    import subprocess

    def _web_search(query: str, max_results: int = 5) -> str:
        try:
            from xuanji.web_search import search
            results = search(query, engine='bing', limit=max_results)
            if isinstance(results, list):
                out = []
                for i, r in enumerate(results[:max_results]):
                    title = r.get("title", "") if isinstance(r, dict) else ""
                    snippet = r.get("snippet", r.get("summary", "")) if isinstance(r, dict) else ""
                    url = r.get("url", "") if isinstance(r, dict) else ""
                    out.append(f"[{i+1}] {title}\n    {snippet}\n    {url}")
                return "\n\n".join(out)
            return str(results)
        except Exception as e:
            return f"搜索失败: {type(e).__name__}: {e}"

    reg.register("web_search", "网络搜索，获取实时信息",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "返回结果数量(默认5)"},
        }, "required": ["query"]},
        _web_search, "web")

    def _fetch_webpage(url: str, max_chars: int = 3000) -> str:
        try:
            from xuanji.web_reader import WebReader
            reader = WebReader()
            content = reader.read(url)
            if isinstance(content, str):
                return content[:max_chars]
            return str(content)
        except Exception as e:
            return f"抓取失败: {type(e).__name__}: {e}"

    reg.register("fetch_webpage", "抓取网页内容，提取正文",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "网页URL"},
            "max_chars": {"type": "integer", "description": "最大返回字符(默认3000)"},
        }, "required": ["url"]},
        _fetch_webpage, "web")

    def _read_file(path: str) -> str:
        try:
            if not os.path.exists(path):
                return f"错误: 文件不存在 - {path}"
            if os.path.isdir(path):
                items = os.listdir(path)
                return f"目录 {path} 内容:\n" + "\n".join(f"  {i}" for i in items[:30])
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            if len(content) > 5000:
                return content[:5000] + f"\n...(已截断，共{len(content)}字)"
            return content
        except Exception as e:
            return f"读取失败: {type(e).__name__}: {e}"

    reg.register("read_file", "读取文件或列出目录内容",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径或目录路径"},
        }, "required": ["path"]},
        _read_file, "file")

    def _write_file(path: str, content: str) -> str:
        try:
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"已写入 {path} ({len(content)} 字符)"
        except Exception as e:
            return f"写入失败: {type(e).__name__}: {e}"

    reg.register("write_file", "写入内容到文件",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的内容"},
        }, "required": ["path", "content"]},
        _write_file, "file")

    def _execute_command(command: str, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=os.getcwd()
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            if not output:
                output = "(无输出)"
            if len(output) > 4000:
                output = output[:4000] + "\n...(已截断)"
            return output
        except subprocess.TimeoutExpired:
            return f"命令超时 ({timeout}s)"
        except Exception as e:
            return f"执行失败: {type(e).__name__}: {e}"

    reg.register("execute_command", "执行Shell命令",
        {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数(默认30)"},
        }, "required": ["command"]},
        _execute_command, "system")

    def _list_directory(path: str = ".") -> str:
        try:
            items = os.listdir(path)
            dirs = [i for i in items if os.path.isdir(os.path.join(path, i))]
            files = [i for i in items if os.path.isfile(os.path.join(path, i))]
            out = f"目录: {path}\n"
            if dirs:
                out += f"📁 子目录({len(dirs)}): {', '.join(dirs[:20])}" + ("..." if len(dirs) > 20 else "") + "\n"
            if files:
                out += f"📄 文件({len(files)}): {', '.join(files[:20])}" + ("..." if len(files) > 20 else "")
            return out
        except Exception as e:
            return f"列出失败: {type(e).__name__}: {e}"

    reg.register("list_directory", "列出目录内容",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径(默认当前目录)"},
        }},
        _list_directory, "file")


def create_tool_registry() -> ToolRegistry:
    """创建完整工具注册表"""
    reg = ToolRegistry()
    register_basic_tools(reg)

    # 记忆工具
    try:
        from xuanji.agent_tools import register_memory_tools
        register_memory_tools(reg)
    except Exception as e:
        pass

    # 浏览器工具
    try:
        from xuanji.agent_tools_v2 import register_browser_tools
        register_browser_tools(reg)
    except Exception as e:
        pass

    # 操控工具
    try:
        from xuanji.agent_tools_v2 import register_hands_tools
        register_hands_tools(reg)
    except Exception as e:
        pass

    # 感知工具
    try:
        from xuanji.agent_tools_v2 import register_perception_tools
        register_perception_tools(reg)
    except Exception as e:
        pass

    return reg


def get_llm_router():
    """获取LLM路由器"""
    import urllib.request

    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            if models:
                prefs = ["qwen3.6", "qwen3.5", "qwen3", "gemma"]
                best = models[0]
                for pref in prefs:
                    for m in models:
                        if pref in m.lower():
                            best = m
                            break

                from xuanji.llm.ollama import OllamaAdapter
                adapter = OllamaAdapter("ollama", {
                    "base_url": "http://localhost:11434",
                    "model": best,
                })
                adapter._models = models
                return adapter, best, models
    except Exception as e:
        print(f"[warn] Ollama not available: {e}", file=sys.stderr)

    return None, "", []


async def run_task(text: str):
    """运行任务"""
    llm, model, models = get_llm_router()
    if not llm:
        return {"error": "没有可用的LLM后端。请安装Ollama或配置API Key。"}

    reg = create_tool_registry()

    runner = AgentRunner(llm, tool_registry=reg, model=model, max_steps=20)
    result = await runner.run(text)

    return {
        "success": result.success,
        "answer": result.answer,
        "steps": result.total_steps,
        "elapsed": round(result.elapsed, 1),
        "tokens": result.tokens_used,
        "model": model,
        "tools_count": len(reg.tool_names),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python bridge.py \"你想说的话\"")
        print("      python bridge.py --chat    交互模式")
        sys.exit(1)

    if sys.argv[1] == "--chat":
        print("🔥 玄机交互模式 (Ctrl+C退出)")
        print(f"可用工具: 等待加载...")
        history = []
        while True:
            try:
                text = input("\n你> ")
            except (KeyboardInterrupt, EOFError):
                print("\n👋 再见")
                break
            if not text.strip():
                continue

            llm, model, models = get_llm_router()
            if not llm:
                print("❌ LLM不可用")
                continue

            reg = create_tool_registry()
            if not history:
                print(f"模型: {model} | 工具: {len(reg.tool_names)}个")

            runner = AgentRunner(llm, tool_registry=reg, model=model, max_steps=20)
            result = asyncio.run(runner.run(text, history=history))
            print(f"\n玄机> {result.answer}")

    else:
        text = " ".join(sys.argv[1:])
        result = asyncio.run(run_task(text))
        print(json.dumps(result, ensure_ascii=False, indent=2))
