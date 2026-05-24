# -*- coding: utf-8 -*-
"""
OpenClaw Skills 执行器

把 OpenClaw skills 中的脚本/逻辑接入 Xuanji ToolRegistry，
让迁移后的工具真正可执行，不是 stub。

设计思路：
1. 有 scripts/ 的 skill → 直接 subprocess 调用
2. 纯 prompt 的 skill → 包装为 prompt template + LLM 调用
3. 外部 API 的 skill → 包装为 HTTP 调用
"""
import os
import sys
import json
import subprocess
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# OpenClaw workspace 路径
OPENCLAW_WORKSPACE = os.environ.get('OPENCLAW_WORKSPACE',
    os.path.expandvars(r'/.openclaw/workspace'))


# ────────────────────────────────────────────────────────────
# 工具注册表：把每个 skill 映射为可执行函数
# ────────────────────────────────────────────────────────────

def _run_script(skill_name: str, script_name: str, args: list, 
                cwd: str = None, timeout: int = 30) -> dict:
    """运行 skill 目录下的脚本"""
    script_path = os.path.join(OPENCLAW_WORKSPACE, 'skills', skill_name, 'scripts', script_name)
    if not os.path.exists(script_path):
        # 尝试 python/xuanji/adapters 下的副本
        alt_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 
                               'skills', skill_name, 'scripts', script_name)
        if os.path.exists(alt_path):
            script_path = alt_path
        else:
            return {"error": f"Script not found: {script_path}"}
    
    work_dir = cwd or os.path.dirname(script_path)
    
    # Windows: 用 python 执行 .py，用 bash 执行 .sh
    if script_name.endswith('.py'):
        cmd = [sys.executable, script_path] + args
    elif script_name.endswith('.sh'):
        cmd = ['bash', script_path] + args
    else:
        cmd = [script_path] + args
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=work_dir,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        # Decode with utf-8, fallback to gbk
        try:
            result_stdout = result.stdout.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            result_stdout = result.stdout.decode('gbk', errors='replace') if result.stdout else ''
        try:
            result_stderr = result.stderr.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            result_stderr = result.stderr.decode('gbk', errors='replace') if result.stderr else ''
        return {
            "returncode": result.returncode,
            "stdout": result_stdout[:2000],
            "stderr": result_stderr[:500],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Script timeout after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def _make_input_file(skill_name: str, data: dict) -> str:
    """创建临时输入文件"""
    import tempfile
    tmpdir = os.path.join(OPENCLAW_WORKSPACE, 'skills', skill_name, 'tmp')
    os.makedirs(tmpdir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix='.json', dir=tmpdir)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ────────────────────────────────────────────────────────────
# 具体工具的 callable 实现
# ────────────────────────────────────────────────────────────

def generate_workflow(workflow_name: str = "my-workflow", 
                      trigger: str = "manual", 
                      steps: list = None,
                      output_format: str = "json") -> dict:
    """agentic-workflow-automation: 生成工作流蓝图"""
    if steps is None:
        steps = [{"name": "step-1", "type": "task"}]
    
    payload = {
        "workflow_name": workflow_name,
        "trigger": trigger,
        "steps": steps,
    }
    
    input_path = _make_input_file('agentic-workflow-automation', payload)
    output_path = input_path.replace('.json', '_output.json')
    
    result = _run_script(
        'agentic-workflow-automation',
        'generate_workflow_blueprint.py',
        ['--input', input_path, '--output', output_path, '--format', output_format],
    )
    
    # 清理临时文件
    try:
        os.remove(input_path)
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                result['output'] = json.load(f)
            os.remove(output_path)
    except Exception:
        pass
    
    return result


def recycle_content(input_text: str, 
                    platforms: list = None,
                    max_tweets: int = 10,
                    tone: str = "conversational") -> dict:
    """content-recycler: 将内容转换为多平台格式"""
    if platforms is None:
        platforms = ["twitter", "linkedin"]
    
    # 创建输入文件
    input_path = _make_input_file('content-recycler', {
        "content": input_text,
        "platforms": platforms,
    })
    
    results = {}
    
    # 生成 Twitter thread
    if "twitter" in platforms:
        output_dir = os.path.join(OPENCLAW_WORKSPACE, 'skills', 'content-recycler', 'tmp')
        os.makedirs(output_dir, exist_ok=True)
        
        r = _run_script(
            'content-recycler',
            'to_twitter_thread.py',
            ['--input', input_path, '--max-tweets', str(max_tweets), 
             '--tone', tone, '--output-dir', output_dir],
        )
        results['twitter'] = r
    
    # 生成 LinkedIn post
    if "linkedin" in platforms:
        output_dir = os.path.join(OPENCLAW_WORKSPACE, 'skills', 'content-recycler', 'tmp')
        r = _run_script(
            'content-recycler',
            'to_linkedin_post.py',
            ['--input', input_path, '--tone', tone, '--output-dir', output_dir],
        )
        results['linkedin'] = r
    
    # 清理
    try:
        os.remove(input_path)
    except Exception:
        pass
    
    return results


def generate_content_calendar(days: int = 7, 
                              content: str = "",
                              themes: list = None) -> dict:
    """content-recycler: 生成内容日历"""
    if themes is None:
        themes = ["teaser", "announcement", "follow-up", "tips", "engagement"]
    
    input_path = _make_input_file('content-recycler', {
        "content": content,
        "days": days,
        "themes": themes,
    })
    
    output_dir = os.path.join(OPENCLAW_WORKSPACE, 'skills', 'content-recycler', 'tmp')
    os.makedirs(output_dir, exist_ok=True)
    
    result = _run_script(
        'content-recycler',
        'generate_calendar.py',
        ['--input', input_path, '--days', str(days), '--output-dir', output_dir],
    )
    
    try:
        os.remove(input_path)
    except Exception:
        pass
    
    return result


def optimize_hashtags(topics: list = None) -> dict:
    """content-recycler: 优化标签"""
    if topics is None:
        topics = ["tech"]
    
    result = _run_script(
        'content-recycler',
        'optimize_hashtags.py',
        ['--topics'] + topics,
    )
    return result


def seo_audit(site: str = "boll-koll.se") -> dict:
    """seo-autopilot: SEO 审计"""
    allowed_sites = ["boll-koll.se", "hyresbyte.se"]
    if site not in allowed_sites:
        return {"error": f"Site not allowed. Allowed: {allowed_sites}"}
    
    result = _run_script(
        'seo-autopilot',
        'run.sh',
        [site],
    )
    return result


# ────────────────────────────────────────────────────────────
# 注册函数：把 skill 注册到 ToolRegistry
# ────────────────────────────────────────────────────────────

SKILL_TOOLS = {
    "agentic-workflow-automation": {
        "func": generate_workflow,
        "description": "Generate workflow blueprint with ordered steps",
        "params": {
            "type": "object",
            "properties": {
                "workflow_name": {"type": "string", "default": "my-workflow"},
                "trigger": {"type": "string", "default": "manual"},
                "steps": {"type": "array", "items": {"type": "object"}},
                "output_format": {"type": "string", "enum": ["json", "md", "csv"], "default": "json"},
            },
        },
    },
    "content-recycler": {
        "func": recycle_content,
        "description": "Transform content for multiple platforms (Twitter, LinkedIn, etc.)",
        "params": {
            "type": "object",
            "properties": {
                "input_text": {"type": "string"},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "max_tweets": {"type": "integer", "default": 10},
                "tone": {"type": "string", "enum": ["professional", "conversational", "playful"], "default": "conversational"},
            },
        },
    },
    "content-calendar": {
        "func": generate_content_calendar,
        "description": "Generate multi-day content calendar",
        "params": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 7},
                "content": {"type": "string"},
                "themes": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "optimize-hashtags": {
        "func": optimize_hashtags,
        "description": "Generate optimized hashtags for topics",
        "params": {
            "type": "object",
            "properties": {
                "topics": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "seo-autopilot": {
        "func": seo_audit,
        "description": "Run SEO audit for allowed sites",
        "params": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "enum": ["boll-koll.se", "hyresbyte.se"]},
            },
        },
    },
}


def register_skill_tools(tool_registry) -> int:
    """把所有可执行的 skill 工具注册到 ToolRegistry"""
    count = 0
    for name, tool_info in SKILL_TOOLS.items():
        try:
            tool_registry.register(
                name=name,
                description=tool_info["description"],
                params=tool_info["params"],
                func=tool_info["func"],
                category="openclaw_skill",
            )
            count += 1
            logger.info(f"Registered skill tool: {name}")
        except Exception as e:
            logger.warning(f"Failed to register {name}: {e}")
    return count


def list_available_skill_tools() -> list:
    """列出所有可执行的 skill 工具"""
    return list(SKILL_TOOLS.keys())
