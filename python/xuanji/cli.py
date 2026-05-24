"""
xuanji 命令行工具

用法:
  xuanji init [项目名]     创建新项目
  xuanji run               启动运行时
  xuanji status            查看状态
  xuanji skill list         列出已安装Skill
  xuanji skill install <dir> 从目录安装Skill
  xuanji skill create <name> 生成Skill模板
  xuanji mcp list           列出MCP Server
  xuanji mcp test <name>    测试MCP Server连接
  xuanji secret set <name>  设置密钥
  xuanji secret list        列出密钥名
  xuanji audit list         查看审计日志
  xuanji create <type> <name>  脚手架生成
"""

import io
import json
import os
import shutil
import sys
from pathlib import Path

# Fix Windows GBK console encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def cmd_init(args):
    """创建新项目"""
    name = args[0] if args else "my-agent"
    project_dir = Path(name)
    
    if project_dir.exists():
        print(f"❌ 目录 {name} 已存在")
        return
    
    # 创建目录结构
    project_dir.mkdir()
    (project_dir / "plugins" / "agents").mkdir(parents=True)
    (project_dir / "plugins" / "tools").mkdir(parents=True)
    (project_dir / "skills").mkdir(parents=True)
    
    # 生成config.toml
    config = '''# xuanji 配置文件

[runtime]
name = "{name}"

# LLM — 一行配一个（给key就行）
[llm]
# deepseek = "sk-xxx"
# openai = "sk-xxx"
# ollama = "localhost"

# 通信渠道 — 一行接一个平台
[channels]
# telegram = "bot_token"
# qq = "app_id:app_secret"
# discord = "bot_token"

# MCP工具
[mcp]
# filesystem = "npx -y @modelcontextprotocol/server-filesystem ."

# Skill目录
[skills]
paths = ["./skills"]

# 插件目录
[plugins]
paths = ["./plugins"]

# 安全
[security]
mode = "standard"
'''.format(name=name)
    
    (project_dir / "config.toml").write_text(config, encoding="utf-8")
    
    # 生成示例Agent
    agent_dir = project_dir / "plugins" / "agents" / "hello"
    agent_dir.mkdir(parents=True, exist_ok=True)
    
    plugin_toml = '''[plugin]
name = "hello"
type = "agent"
version = "0.1.0"
entry = "agent.py:HelloAgent"
description = "Hello World Agent"
'''
    (agent_dir / "plugin.toml").write_text(plugin_toml, encoding="utf-8")
    
    agent_py = '''from xuanji import AgentPlugin


class HelloAgent(AgentPlugin):
    name = "Hello"
    description = "示例Agent"
    
    async def on_message(self, msg, ctx):
        """收到消息时回复"""
        reply = await ctx.llm.chat([
            {"role": "system", "content": "你是一个友好的助手"},
            {"role": "user", "content": msg.content}
        ])
        await ctx.channels.reply(msg, reply)
    
    async def on_task(self, task, ctx):
        """收到任务时执行"""
        return f"任务完成: {task}"
'''
    (agent_dir / "agent.py").write_text(agent_py, encoding="utf-8")
    
    print(f"✅ 项目 {name} 创建成功")
    print(f"")
    print(f"   cd {name}")
    print(f"   # 编辑 config.toml 配置LLM和通信渠道")
    print(f"   xuanji run")


def cmd_run(args):
    """启动运行时"""
    from xuanji.runtime import Runtime
    config = args[0] if args else "config.toml"
    runtime = Runtime(config=config)
    runtime.run()


def cmd_status(args):
    """查看状态"""
    import importlib.metadata
    try:
        version = importlib.metadata.version('xuanji')
    except Exception:
        version = 'dev'
    print("📊 xuanji Status")
    print(f"   版本: {version}")
    print(f"   状态: 开发中")


def cmd_version(args):
    """版本信息"""
    import importlib.metadata
    try:
        version = importlib.metadata.version('xuanji')
    except Exception:
        version = 'dev'
    print(f"xuanji v{version}")


# ============================================================
# Skill 命令
# ============================================================

def _get_skills_dir() -> Path:
    """获取Skill安装目录: ~/.xuanji/skills/"""
    d = Path.home() / ".xuanji" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_skill(args):
    """Skill管理命令"""
    if not args:
        print("用法: xuanji skill <list|install|create>")
        return
    
    sub = args[0]
    sub_args = args[1:]
    
    if sub == "list":
        cmd_skill_list(sub_args)
    elif sub == "install":
        cmd_skill_install(sub_args)
    elif sub == "create":
        cmd_skill_create(sub_args)
    else:
        print(f"❌ 未知skill子命令: {sub}")
        print("用法: xuanji skill <list|install|create>")


def cmd_skill_list(args):
    """列出已安装Skill"""
    from xuanji.skill import SkillLoader
    
    skills_dir = _get_skills_dir()
    
    # 同时扫描当前项目的skills/和全局目录
    scan_paths = [str(skills_dir)]
    local_skills = Path("skills")
    if local_skills.is_dir():
        scan_paths.append(str(local_skills))
    
    loader = SkillLoader()
    found = loader.scan(scan_paths)
    all_skills = loader.list_skills()
    
    if not all_skills:
        print("📭 暂无已安装的Skill")
        print(f"   全局目录: {skills_dir}")
        print(f"   使用 xuanji skill create <name> 创建")
        return
    
    print(f"📦 已安装 {len(all_skills)} 个Skill:")
    print()
    for s in all_skills:
        kw_str = ", ".join(s.trigger_keywords[:5]) if s.trigger_keywords else "无"
        print(f"  📝 {s.name} (v{s.version})")
        if s.description:
            desc = s.description[:80]
            print(f"     {desc}")
        print(f"     关键词: {kw_str}")
        print(f"     目录: {s.directory}")
        print()


def cmd_skill_install(args):
    """从目录安装Skill（复制到~/.xuanji/skills/）"""
    if not args:
        print("用法: xuanji skill install <目录路径>")
        return
    
    src = Path(args[0]).resolve()
    skill_md = src / "SKILL.md"
    
    if not skill_md.is_file():
        print(f"❌ {src} 中找不到 SKILL.md")
        return
    
    # 目标目录
    name = src.name
    dst = _get_skills_dir() / name
    
    if dst.exists():
        print(f"⚠️  {name} 已存在，覆盖安装...")
        shutil.rmtree(dst)
    
    shutil.copytree(src, dst)
    print(f"✅ Skill '{name}' 安装成功")
    print(f"   目录: {dst}")


def cmd_skill_create(args):
    """生成Skill模板"""
    if not args:
        print("用法: xuanji skill create <名称>")
        return
    
    name = args[0]
    skill_dir = Path("skills") / name
    
    if skill_dir.exists():
        print(f"❌ 目录 {skill_dir} 已存在")
        return
    
    skill_dir.mkdir(parents=True)
    
    # SKILL.md
    skill_md = f"""# {name}

简短描述这个Skill做什么。

## 触发条件

- 用户要求{name}相关的任务
- 提到{name}关键词

## 执行步骤

1. 第一步
2. 第二步
3. 第三步

## 注意事项

- 注意事项1
- 注意事项2
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    
    # skill.toml
    skill_toml = f"""[skill]
name = "{name}"
version = "0.1.0"
description = "{name} Skill"

[requires]
python = []
tools = []
"""
    (skill_dir / "skill.toml").write_text(skill_toml, encoding="utf-8")
    
    print(f"✅ Skill模板 '{name}' 已创建")
    print(f"   目录: {skill_dir}")
    print(f"   编辑 {skill_dir / 'SKILL.md'} 添加指导内容")


# ============================================================
# MCP 命令
# ============================================================

def _load_config() -> dict:
    """加载项目config.toml"""
    config_path = Path("config.toml")
    if not config_path.is_file():
        return {}
    
    # 复用loader的TOML解析
    from xuanji.loader import PluginLoader
    loader = PluginLoader()
    try:
        return loader._read_toml(str(config_path))
    except Exception:
        return {}


def cmd_mcp(args):
    """MCP管理命令"""
    if not args:
        print("用法: xuanji mcp <list|test>")
        return
    
    sub = args[0]
    sub_args = args[1:]
    
    if sub == "list":
        cmd_mcp_list(sub_args)
    elif sub == "test":
        cmd_mcp_test(sub_args)
    else:
        print(f"❌ 未知mcp子命令: {sub}")
        print("用法: xuanji mcp <list|test>")


def cmd_mcp_list(args):
    """列出config.toml中配置的MCP Server"""
    config = _load_config()
    mcp_config = config.get("mcp", {})
    
    if not mcp_config:
        print("📭 未配置MCP Server")
        print("   在 config.toml 的 [mcp] 段添加:")
        print('   filesystem = "npx -y @modelcontextprotocol/server-filesystem ."')
        return
    
    print(f"🔧 已配置 {len(mcp_config)} 个MCP Server:")
    print()
    for name, value in mcp_config.items():
        if isinstance(value, str):
            print(f"  🔌 {name}")
            print(f"     命令: {value}")
        elif isinstance(value, dict):
            cmd = value.get("command", "")
            cmd_args = value.get("args", [])
            full_cmd = f"{cmd} {' '.join(cmd_args)}" if cmd_args else cmd
            print(f"  🔌 {name}")
            print(f"     命令: {full_cmd}")
        print()


def cmd_mcp_test(args):
    """测试MCP Server连接"""
    if not args:
        print("用法: xuanji mcp test <server名>")
        return
    
    name = args[0]
    config = _load_config()
    mcp_config = config.get("mcp", {})
    
    if name not in mcp_config:
        print(f"❌ 未找到MCP Server: {name}")
        print(f"   已配置: {', '.join(mcp_config.keys()) if mcp_config else '无'}")
        return
    
    value = mcp_config[name]
    
    if isinstance(value, str):
        parts = value.split()
        command = parts[0]
        cmd_args = parts[1:]
    elif isinstance(value, dict):
        command = value.get("command", "")
        cmd_args = value.get("args", [])
    else:
        print(f"❌ 配置格式错误")
        return
    
    print(f"🔄 测试连接 {name}...")
    print(f"   命令: {command} {' '.join(cmd_args)}")
    
    from xuanji.mcp_client import MCPClient, MCPError
    
    client = MCPClient(name)
    try:
        info = client.connect_stdio(command, cmd_args, timeout=15)
        print(f"✅ 连接成功!")
        
        server_info = info.get("serverInfo", {})
        print(f"   Server: {server_info.get('name', 'unknown')} v{server_info.get('version', '?')}")
        
        tools = client.list_tools()
        print(f"   工具数: {len(tools)}")
        for t in tools:
            print(f"     - {t.name}: {t.description[:60] if t.description else ''}")
        
        client.disconnect()
    except MCPError as e:
        print(f"❌ 连接失败: {e.message}")
    except Exception as e:
        print(f"❌ 错误: {e}")


# ============================================================
# Secret 命令
# ============================================================

def _get_secrets_path() -> Path:
    """密钥文件: ~/.xuanji/secrets.json"""
    return Path.home() / ".xuanji" / "secrets.json"


def _load_secrets() -> dict:
    p = _get_secrets_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_secrets(data: dict):
    p = _get_secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_secret(args):
    """密钥管理命令"""
    if not args:
        print("用法: xuanji secret <set|list>")
        return
    
    sub = args[0]
    sub_args = args[1:]
    
    if sub == "set":
        cmd_secret_set(sub_args)
    elif sub == "list":
        cmd_secret_list(sub_args)
    else:
        print(f"❌ 未知secret子命令: {sub}")


def cmd_secret_set(args):
    """设置密钥"""
    if not args:
        print("用法: xuanji secret set <名称> [值]")
        return
    
    name = args[0]
    
    if len(args) >= 2:
        value = args[1]
    else:
        # 交互式输入
        import getpass
        value = getpass.getpass(f"输入 {name} 的值: ")
    
    secrets = _load_secrets()
    secrets[name] = value
    _save_secrets(secrets)
    print(f"✅ 密钥 '{name}' 已保存")


def cmd_secret_list(args):
    """列出密钥名（不显示值）"""
    secrets = _load_secrets()
    if not secrets:
        print("📭 暂无密钥")
        print("   使用 xuanji secret set <名称> 添加")
        return
    
    print(f"🔑 已保存 {len(secrets)} 个密钥:")
    for name in secrets:
        # 只显示前4位
        val = secrets[name]
        masked = val[:4] + "*" * (len(val) - 4) if len(val) > 4 else "****"
        print(f"  - {name}: {masked}")


# ============================================================
# Audit 命令
# ============================================================

def _get_audit_path() -> Path:
    """审计日志: ~/.xuanji/audit.log"""
    return Path.home() / ".xuanji" / "audit.log"


def cmd_audit(args):
    """审计日志命令"""
    if not args:
        print("用法: xuanji audit <list>")
        return
    
    sub = args[0]
    sub_args = args[1:]
    
    if sub == "list":
        cmd_audit_list(sub_args)
    else:
        print(f"❌ 未知audit子命令: {sub}")


def cmd_audit_list(args):
    """查看审计日志"""
    audit_path = _get_audit_path()
    
    if not audit_path.is_file():
        print("📭 暂无审计日志")
        return
    
    # 显示最近N行
    n = 20
    if args:
        try:
            n = int(args[0])
        except ValueError:
            pass
    
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    recent = lines[-n:] if len(lines) > n else lines
    
    print(f"📋 审计日志（最近{len(recent)}条，共{len(lines)}条）:")
    print()
    for line in recent:
        print(f"  {line}")


# ============================================================
# Create 脚手架命令
# ============================================================

def cmd_create(args):
    """脚手架 — 快速生成模板
    
    xuanji create skill <name>
    xuanji create tool <name>
    xuanji create agent <name>
    xuanji create mcp <name>
    """
    if len(args) < 2:
        print("用法: xuanji create <skill|tool|agent|mcp> <名称>")
        return
    
    kind = args[0]
    name = args[1]
    
    creators = {
        "skill": _create_skill,
        "tool": _create_tool,
        "agent": _create_agent,
        "mcp": _create_mcp,
    }
    
    creator = creators.get(kind)
    if not creator:
        print(f"❌ 未知类型: {kind}")
        print(f"   支持: {', '.join(creators.keys())}")
        return
    
    creator(name)


def _create_skill(name: str):
    """生成Skill模板"""
    cmd_skill_create([name])


def _create_tool(name: str):
    """生成Tool Plugin模板"""
    d = Path("plugins") / "tools" / name
    if d.exists():
        print(f"❌ 目录 {d} 已存在")
        return
    d.mkdir(parents=True)
    
    (d / "plugin.toml").write_text(f"""[plugin]
name = "{name}"
type = "tool"
version = "0.1.0"
entry = "tool.py:{_to_class_name(name)}Tool"
description = "{name} 工具"

[requires]
python = []

[permissions]
network = []
""", encoding="utf-8")
    
    (d / "tool.py").write_text(f"""from xuanji import ToolPlugin


class {_to_class_name(name)}Tool(ToolPlugin):
    name = "{name}"
    description = "{name} 工具"
    
    def schema(self):
        return {{
            "input": {{"type": "string", "description": "输入参数"}},
        }}
    
    async def execute(self, params, ctx):
        # TODO: 实现工具逻辑
        return {{"result": f"执行 {name}: {{params.get('input', '')}}"}}
""", encoding="utf-8")
    
    print(f"✅ Tool Plugin '{name}' 已创建")
    print(f"   目录: {d}")
    print(f"   编辑 {d / 'tool.py'} 实现逻辑")


def _create_agent(name: str):
    """生成Agent Plugin模板"""
    d = Path("plugins") / "agents" / name
    if d.exists():
        print(f"❌ 目录 {d} 已存在")
        return
    d.mkdir(parents=True)
    
    (d / "plugin.toml").write_text(f"""[plugin]
name = "{name}"
type = "agent"
version = "0.1.0"
entry = "agent.py:{_to_class_name(name)}Agent"
description = "{name} Agent"
""", encoding="utf-8")
    
    (d / "agent.py").write_text(f"""from xuanji import AgentPlugin


class {_to_class_name(name)}Agent(AgentPlugin):
    name = "{name}"
    description = "{name} Agent"
    tools = []
    
    async def on_message(self, msg, ctx):
        \"\"\"收到消息时回复\"\"\"
        reply = await ctx.llm.chat([
            {{"role": "system", "content": "你是{name}助手"}},
            {{"role": "user", "content": msg.content}}
        ])
        await ctx.channels.reply(msg, reply)
    
    async def on_task(self, task, ctx):
        \"\"\"收到任务时执行\"\"\"
        return f"任务完成: {{task}}"
""", encoding="utf-8")
    
    print(f"✅ Agent Plugin '{name}' 已创建")
    print(f"   目录: {d}")
    print(f"   编辑 {d / 'agent.py'} 实现逻辑")


def _create_mcp(name: str):
    """生成MCP Server模板"""
    filename = f"mcp_{name}.py"
    if Path(filename).exists():
        print(f"❌ 文件 {filename} 已存在")
        return
    
    Path(filename).write_text(f"""\"\"\"MCP Server: {name}

启动: python {filename}
接入: 在config.toml的[mcp]段添加 {name} = "python {filename}"
\"\"\"
from xuanji.mcp_server import MCPServer

server = MCPServer("{name}")


@server.tool("hello", description="示例工具")
def hello(name: str = "world") -> str:
    return f"Hello, {{name}}!"


# 添加更多工具...
# @server.tool("工具名", description="描述")
# def my_tool(param: str) -> str:
#     return "result"


if __name__ == "__main__":
    server.run()
""", encoding="utf-8")
    
    print(f"✅ MCP Server '{name}' 已创建")
    print(f"   文件: {filename}")
    print(f"   测试: python {filename}")
    print(f"   接入: 在config.toml添加 {name} = \"python {filename}\"")


def _to_class_name(name: str) -> str:
    """转为PascalCase类名: my-tool → MyTool"""
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))


# ============================================================
# Tutorial 命令
# ============================================================

def cmd_tutorial(args):
    """交互式教程命令"""
    from xuanji.tutorial import TutorialEngine
    engine = TutorialEngine()

    if not args:
        print("用法: xuanji tutorial <list|start|next|status|reset>")
        return

    sub = args[0]
    sub_args = args[1:]

    if sub == "list":
        engine.list_tutorials()
    elif sub == "start":
        if not sub_args:
            print("用法: xuanji tutorial start <教程名>")
            return
        engine.start(sub_args[0])
    elif sub == "next":
        # 自动检测当前教程
        progress_dir = Path.home() / ".xuanji" / "tutorials"
        if not progress_dir.is_dir():
            print("❌ 请先开始一个教程")
            return
        latest = sorted(progress_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not latest:
            print("❌ 找不到进行中的教程")
            return
        tutorial_id = latest[0].stem
        engine.next_step(tutorial_id)
    elif sub == "status":
        engine.status()
    elif sub == "reset":
        if not sub_args:
            print("用法: xuanji tutorial reset <教程名>")
            return
        engine.reset(sub_args[0])
    else:
        print(f"❌ 未知tutorial子命令: {sub}")


# ============================================================
# Debug 命令
# ============================================================

def cmd_debug(args):
    """调试器命令"""
    from xuanji.debugger import AgentDebugger, InteractiveDebugger
    debugger = AgentDebugger()

    if not args:
        print("用法: xuanji debug <start|step|continue|break|inspect|vars|history|stop>")
        return

    sub = args[0]
    sub_args = args[1:]

    if sub == "start":
        # 启动交互式调试器
        InteractiveDebugger(debugger).run()
    elif sub == "step":
        result = debugger.step()
        print(f"  状态: {result['status']}")
        if result.get('step'):
            print(f"  步骤: {result['step']}")
        if result.get('result'):
            print(f"  结果: {json.dumps(result['result'], ensure_ascii=False)[:200]}")
    elif sub == "continue":
        result = debugger.continue_to_next()
        print(f"  执行了 {result.get('steps_executed', 0)} 步，状态: {result.get('status')}")
    elif sub == "break":
        if not sub_args:
            print("用法: xuanji debug break <step_name>")
            return
        debugger.break_at(sub_args[0])
        print(f"🔖 断点已设置: {sub_args[0]}")
    elif sub == "inspect":
        info = debugger.inspect()
        print(f"📊 调试状态:")
        print(f"  会话: {info['session']}")
        print(f"  当前步骤: {info['current_step'] or '无'}")
        print(f"  进度: {info['step_index']}/{info['total_steps']}")
        print(f"  断点: {info['breakpoint_count']}")
        print(f"  历史: {info['history_length']}")
    elif sub == "vars":
        vars_dict = debugger.variables()
        if not vars_dict:
            print("📭 暂无变量")
        else:
            for name, value in vars_dict.items():
                print(f"  {name} = {value}")
    elif sub == "history":
        limit = int(sub_args[0]) if sub_args else 20
        records = debugger.history(limit)
        for r in records:
            print(f"  [{r['time']}] {r['step']} → {r['action']}")
    elif sub == "stop":
        debugger.stop()
        print("🛑 调试器已停止")
    else:
        print(f"❌ 未知debug子命令: {sub}")


# ============================================================
# Error 命令
# ============================================================

def cmd_error(args):
    """错误码查询命令"""
    from xuanji.error_codes import get_error, get_suggestion, list_all_codes, search_errors, format_error

    if not args:
        print("用法: xuanji error <query|list|search>")
        print("  xuanji error query <错误码>    查询错误详情")
        print("  xuanji error list               列出所有错误码")
        print("  xuanji error search <关键词>    搜索错误码")
        return

    sub = args[0]
    sub_args = args[1:]

    if sub == "query":
        if not sub_args:
            print("用法: xuanji error query <错误码>")
            return
        err = get_error(sub_args[0])
        if err:
            print(format_error(err, verbose=True))
        else:
            print(f"❌ 未知错误码: {sub_args[0]}")
            print(f"   使用 'xuanji error list' 查看所有错误码")
    elif sub == "list":
        codes = list_all_codes()
        print(f"📋 共 {len(codes)} 个错误码:")
        current_category = None
        for code in codes:
            err = get_error(code)
            if err.category != current_category:
                current_category = err.category
                print(f"\n  【{current_category}】")
            print(f"  {err.code:15s} {err.name}")
    elif sub == "search":
        if not sub_args:
            print("用法: xuanji error search <关键词>")
            return
        results = search_errors(sub_args[0])
        if results:
            print(f"🔍 找到 {len(results)} 个匹配:")
            for err in results:
                print(f"  {err.code:15s} {err.name} — {err.description}")
        else:
            print(f"📭 未找到匹配 '{sub_args[0]}' 的错误码")
    else:
        print(f"❌ 未知error子命令: {sub}")


# ============================================================
# Changelog 命令
# ============================================================

def cmd_changelog(args):
    """Changelog生成命令"""
    from xuanji.changelog import ChangelogGenerator, init_changelog

    if not args:
        print("用法: xuanji changelog <generate|diff|since|init>")
        return

    sub = args[0]
    sub_args = args[1:]

    gen = ChangelogGenerator()

    if sub == "generate":
        version = sub_args[0] if sub_args else None
        md = gen.generate(version)
        print(md)
    elif sub == "diff":
        if len(sub_args) < 2:
            print("用法: xuanji changelog diff <v1> <v2>")
            return
        md = gen.generate_between(sub_args[0], sub_args[1])
        print(md)
    elif sub == "since":
        if not sub_args:
            print("用法: xuanji changelog since <commit>")
            return
        md = gen.generate_since(sub_args[0])
        print(md)
    elif sub == "init":
        init_changelog()
    elif sub == "write":
        version = sub_args[0] if sub_args else None
        path = gen.write_changelog(version=version)
        print(f"✅ CHANGELOG.md 已写入: {path}")
    else:
        print(f"❌ 未知changelog子命令: {sub}")


# ============================================================
# Contributing 命令
# ============================================================

def cmd_contributing(args):
    """贡献者指南生成命令"""
    from xuanji.contributing import ContributingGuide

    if not args:
        print("用法: xuanji contributing <generate|preview>")
        return

    sub = args[0]
    guide = ContributingGuide()

    if sub == "generate":
        path = guide.write()
        print(f"✅ CONTRIBUTING.md 已生成: {path}")
    elif sub == "preview":
        print(guide.preview())
    else:
        print(f"❌ 未知contributing子命令: {sub}")


# ============================================================
# Plugin Registry 命令
# ============================================================

def cmd_plugin(args):
    """插件注册中心命令"""
    from xuanji.plugin_registry import PluginRegistry

    if not args:
        print("用法: xuanji plugin <submit|scan|bench|certify|list|info|revoke>")
        return

    sub = args[0]
    sub_args = args[1:]
    reg = PluginRegistry()

    if sub == "submit":
        if not sub_args:
            print("用法: xuanji plugin submit <路径>")
            return
        result = reg.submit(sub_args[0])
        if result.get("success"):
            print(f"✅ {result.get('message', '提交成功')}")
        else:
            print(f"❌ {result.get('error')}")
    elif sub == "scan":
        if not sub_args:
            print("用法: xuanji plugin scan <路径>")
            return
        result = reg.scan(sub_args[0])
        summary = result.summary()
        status = "✅ 通过" if result.passed else "❌ 未通过"
        print(f"🔒 安全扫描 {status} (评分: {summary['score']}/100)")
        print(f"   耗时: {summary['scan_time']}s")
        if result.critical:
            print(f"\n  ❌ 严重问题 ({len(result.critical)}个):")
            for c in result.critical:
                print(f"     - {c['message']}")
                if c.get('location'):
                    print(f"       位置: {c['location']}")
        if result.warnings:
            print(f"\n  ⚠️ 警告 ({len(result.warnings)}个):")
            for w in result.warnings:
                print(f"     - {w['message']}")
    elif sub == "bench":
        if not sub_args:
            print("用法: xuanji plugin bench <路径>")
            return
        result = reg.bench(sub_args[0])
        summary = result.summary()
        print(f"⚡ 性能测试:")
        print(f"   评级: {summary['grade']}")
        print(f"   加载时间: {summary['load_time_ms']}ms")
        print(f"   代码行数: {summary['code_lines']}")
        print(f"   复杂度: {summary['complexity']}")
    elif sub == "certify":
        if not sub_args:
            print("用法: xuanji plugin certify <名称>")
            return
        result = reg.certify(sub_args[0])
        if result.get("success"):
            print(f"✅ 插件 '{sub_args[0]}' 已认证")
            print(f"   安全评分: {result.get('security_score')}")
            print(f"   性能评级: {result.get('performance_grade')}")
        else:
            print(f"❌ {result.get('error')}")
    elif sub == "list":
        certified = reg.list_certified()
        if certified:
            print(f"🏅 认证插件 ({len(certified)}个):")
            for p in certified:
                print(f"  📦 {p['name']} v{p['version']}")
                print(f"     安全: {p['security_score']}/100 | 性能: {p['performance_grade']}")
                if p.get('description'):
                    print(f"     {p['description']}")
                print()
        else:
            print("📭 暂无认证插件")
    elif sub == "info":
        if not sub_args:
            print("用法: xuanji plugin info <名称>")
            return
        info = reg.get_info(sub_args[0])
        if info:
            print(f"📦 {info['name']} v{info['version']}")
            print(f"   作者: {info['author']}")
            print(f"   描述: {info['description']}")
            print(f"   认证: {'✅ 是' if info['certified'] else '❌ 否'}")
            print(f"   安全评分: {info['security_score']}/100")
            print(f"   性能评级: {info['performance_grade']}")
            print(f"   提交时间: {info['submitted_at']}")
            print(f"   兼容版本: {info['compat_version']}")
            if info.get('tags'):
                print(f"   标签: {', '.join(info['tags'])}")
        else:
            print(f"❌ 插件不存在: {sub_args[0]}")
    elif sub == "revoke":
        if not sub_args:
            print("用法: xuanji plugin revoke <名称>")
            return
        result = reg.revoke(sub_args[0])
        if result.get("success"):
            print(f"✅ {result.get('message')}")
        else:
            print(f"❌ {result.get('error')}")
    else:
        print(f"❌ 未知plugin子命令: {sub}")


def cmd_persona(args):
    """专家人格管理"""
    from xuanji.personas import PersonaLibrary
    
    if not args:
        print("用法: xuanji persona <子命令>")
        print("  list          列出所有人格")
        print("  list <领域>    按领域过滤")
        print("  get <ID>       查看人格详情")
        print("  search <关键词> 搜索人格")
        print("  suggest <任务>  推荐适合的人格")
        print("  team <类型>    查看团队组合")
        print("  match <角色>   角色匹配人格")
        print("  stats          统计信息")
        return
    
    lib = PersonaLibrary()
    sub = args[0].lower()
    sub_args = args[1:]
    
    if sub == "list":
        domain = sub_args[0] if sub_args else ""
        personas = lib.list_personas(domain)
        if not personas:
            print(f"❌ 未找到人格" + (f" (领域: {domain})" if domain else ""))
            return
        print(f"[LIST] 专家人格 ({len(personas)}个)" + (f" [领域: {domain}]" if domain else ""))
        print()
        for p in personas:
            print(f"  {p.id}")
            print(f"    中文名: {p.name_cn}")
            print(f"    领域: {p.domain}")
            print(f"    角色: {p.role}")
            print(f"    能力: {', '.join(p.expertise[:4])}{'...' if len(p.expertise) > 4 else ''}")
            print()
    
    elif sub == "get":
        if not sub_args:
            print("用法: xuanji persona get <ID>")
            return
        persona = lib.get(sub_args[0])
        if not persona:
            print(f"❌ 未找到人格: {sub_args[0]}")
            return
        print(f"[PERSONA] {persona.name_cn} ({persona.name})")
        print(f"  ID: {persona.id}")
        print(f"  领域: {persona.domain}")
        print(f"  角色: {persona.role}")
        print(f"  性格: {persona.personality}")
        print(f"  能力: {', '.join(persona.expertise)}")
        print(f"  工具: {', '.join(persona.tools_needed)}")
        print(f"  产出: {', '.join(persona.deliverables)}")
        print()
        print("## System Prompt")
        print(persona.system_prompt)
    
    elif sub == "search":
        if not sub_args:
            print("用法: xuanji persona search <关键词>")
            return
        results = lib.search(sub_args[0])
        if not results:
            print(f"❌ 未找到匹配 '{sub_args[0]}' 的人格")
            return
        print(f"[SEARCH] 搜索 '{sub_args[0]}' ({len(results)}个结果)")
        for p in results:
            print(f"  {p.id} | {p.name_cn} | {p.domain} | {p.role}")
    
    elif sub == "suggest":
        if not sub_args:
            print("用法: xuanji persona suggest <任务描述>")
            return
        results = lib.suggest(sub_args[0])
        if not results:
            print(f"❌ 未找到适合的人格")
            return
        print(f"[SUGGEST] 推荐人格 (任务: {sub_args[0][:50]}...)")
        for p in results[:5]:
            print(f"  {p.id} | {p.name_cn} | {p.role}")
    
    elif sub == "team":
        if not sub_args:
            print("可用团队模板: game, web_app, novel, anime, ai_system, full_stack")
            return
        team = lib.team_composition(sub_args[0])
        if not team:
            print(f"❌ 未找到团队模板: {sub_args[0]}")
            return
        print(f"[TEAM] 团队组合: {sub_args[0]} ({len(team)}人)")
        for p in team:
            print(f"  {p.id} | {p.name_cn} | {p.role}")
    
    elif sub == "match":
        if not sub_args:
            print("用法: xuanji persona match <角色>")
            return
        persona = lib.match_team_role(sub_args[0])
        if not persona:
            print(f"❌ 未找到角色匹配: {sub_args[0]}")
            return
        print(f"[MATCH] 角色 '{sub_args[0]}' → {persona.name_cn} ({persona.id})")
    
    elif sub == "stats":
        s = lib.stats()
        print("[STATS] 人格库统计")
        print(f"  总计: {s['total']}")
        print(f"  内置: {s['builtin']}")
        print(f"  额外: {s['extra']}")
        print(f"  自定义: {s['custom']}")
        print(f"  领域: {', '.join(f'{k}({v})' for k, v in s['domains'].items())}")
        print(f"  团队模板: {', '.join(s['team_templates'])}")
    
    else:
        print(f"❌ 未知persona子命令: {sub}")


# ============================================================
# 命令路由
# ============================================================

COMMANDS = {
    "init": cmd_init,
    "run": cmd_run,
    "status": cmd_status,
    "version": cmd_version,
    "--version": cmd_version,
    "-v": cmd_version,
    "skill": cmd_skill,
    "mcp": cmd_mcp,
    "secret": cmd_secret,
    "audit": cmd_audit,
    "create": cmd_create,
    "tutorial": cmd_tutorial,
    "debug": cmd_debug,
    "error": cmd_error,
    "changelog": cmd_changelog,
    "contributing": cmd_contributing,
    "plugin": cmd_plugin,
    "persona": cmd_persona,
}


def main():
    args = sys.argv[1:]
    
    if not args:
        print(__doc__)
        return
    
    cmd = args[0]
    cmd_args = args[1:]
    
    if cmd in COMMANDS:
        COMMANDS[cmd](cmd_args)
    else:
        print(f"❌ 未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
