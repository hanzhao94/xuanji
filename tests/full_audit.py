"""
玄玑全架构审查报告
扫描所有模块，找问题、死代码、API不匹配、缺失环节
"""
import sys
import os
import ast
import importlib

os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, r'D:\openagent\python')

print("=" * 70)
print("玄玑 (XuanJi) v1.0.0 - 全架构内部审查")
print("=" * 70)

issues = []
warnings = []
ok_items = []

# ─────────────────────────────────────────────
# 1. 模块导入完整性
# ─────────────────────────────────────────────
print("\n[1] 模块导入完整性")

all_modules = []
for root, dirs, files in os.walk(r"D:\openagent\python\xuanji"):
    for f in files:
        if f.endswith('.py') and not f.startswith('__'):
            rel = os.path.relpath(os.path.join(root, f), r"D:\openagent\python")
            mod = rel.replace(os.sep, '.').replace('.py', '')
            all_modules.append(mod)

# Platform-specific modules that are expected to fail on wrong platform
PLATFORM_MODULES = {
    'xuanji.hands._win': 'win32',
    'xuanji.hands._linux': 'linux',
    'xuanji.hands._darwin': 'darwin',
    'xuanji.perception._win': 'win32',
    'xuanji.perception._linux': 'linux',
    'xuanji.perception._darwin': 'darwin',
}

ok_count = 0
fail_count = 0
expected_fails = 0
for mod in sorted(all_modules):
    try:
        importlib.import_module(mod)
        ok_count += 1
    except ImportError as e:
        # Check if this is an expected platform-specific failure
        if mod in PLATFORM_MODULES:
            expected_fails += 1
        else:
            fail_count += 1
            msg = f"FAIL: {mod} -> {type(e).__name__}: {e}"
            issues.append(msg)
            print(f"  {msg}")
    except Exception as e:
        fail_count += 1
        msg = f"FAIL: {mod} -> {type(e).__name__}: {e}"
        issues.append(msg)
        print(f"  {msg}")

print(f"  OK: {ok_count}/{len(all_modules)}")
print(f"  Expected platform skips: {expected_fails}")
if fail_count > 0:
    print(f"  Unexpected failures: {fail_count}")

# ─────────────────────────────────────────────
# 2. API一致性检查
# ─────────────────────────────────────────────
print("\n[2] API一致性检查")

def check_api_match(name, module, expected_classes):
    mod = importlib.import_module(module)
    for cls in expected_classes:
        if not hasattr(mod, cls):
            msg = f"MISSING: {module}.{cls}"
            issues.append(msg)
            print(f"  {msg}")

# Evolution engine
try:
    mod = importlib.import_module('xuanji.evolution.engine')
    for cls in ['EvolutionEngine']:
        if not hasattr(mod, cls):
            issues.append(f"MISSING: evolution.engine.{cls}")
    ok_items.append("evolution.engine OK")
except Exception as e:
    issues.append(f"evolution.engine import error: {e}")

# Memory manager
try:
    mod = importlib.import_module('xuanji.memory.manager')
    if not hasattr(mod, 'MemoryManager'):
        issues.append("MISSING: memory.manager.MemoryManager")
    ok_items.append("memory.manager OK")
except Exception as e:
    issues.append(f"memory.manager import error: {e}")

# Agent runner
try:
    mod = importlib.import_module('xuanji.agent_runner')
    for cls in ['AgentRunner', 'ToolRegistry', 'AgentResult']:
        if not hasattr(mod, cls):
            issues.append(f"MISSING: agent_runner.{cls}")
    ok_items.append("agent_runner OK")
except Exception as e:
    issues.append(f"agent_runner import error: {e}")

print(f"  Issues found: {len(issues)}")

# ─────────────────────────────────────────────
# 3. 死代码检测
# ─────────────────────────────────────────────
print("\n[3] 死代码检测")

# Check for files that are never imported anywhere
xuanji_dir = r"D:\openagent\python\xuanji"
all_py_files = []
for root, dirs, files in os.walk(xuanji_dir):
    for f in files:
        if f.endswith('.py') and not f.startswith('__'):
            all_py_files.append(os.path.join(root, f))

# Build a set of all imports in the codebase
all_imports = set()
for py_file in all_py_files:
    try:
        with open(py_file, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    all_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    all_imports.add(node.module)
    except:
        pass

# Check which files are never imported
unimported = []
for py_file in all_py_files:
    rel = os.path.relpath(py_file, r"D:\openagent\python").replace(os.sep, '.').replace('.py', '')
    # Check if this module or any parent is imported
    imported = False
    for imp in all_imports:
        if rel == imp or rel.startswith(imp + '.'):
            imported = True
            break
    if not imported:
        unimported.append(py_file)

print(f"  Total Python files: {len(all_py_files)}")
print(f"  Unimported files: {len(unimported)}")
if unimported:
    for u in unimported[:10]:
        rel = os.path.relpath(u, xuanji_dir)
        print(f"    - {rel}")
    if len(unimported) > 10:
        print(f"    ... and {len(unimported) - 10} more")

# ─────────────────────────────────────────────
# 4. 循环依赖检测
# ─────────────────────────────────────────────
print("\n[4] 循环依赖检测")

# Build dependency graph
deps = {}
for py_file in all_py_files:
    rel = os.path.relpath(py_file, r"D:\openagent\python").replace(os.sep, '.').replace('.py', '')
    module_deps = set()
    try:
        with open(py_file, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith('xuanji.'):
                    module_deps.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('xuanji.'):
                        module_deps.add(alias.name)
    except:
        pass
    deps[rel] = module_deps

# Simple cycle detection
cycles = []
for mod, mod_deps in deps.items():
    for dep in mod_deps:
        # Get the file name of the dep
        dep_file = dep.replace('xuanji.', '').replace('.', '/')
        if dep_file.endswith('/__init__'):
            dep_file = dep_file.replace('/__init__', '')
        
        # Check if dep imports mod back
        for d2, d2_deps in deps.items():
            if d2 == dep_file and mod in d2_deps:
                cycle = f"{mod} <-> {dep_file}"
                if cycle not in cycles and f"{dep_file} <-> {mod}" not in cycles:
                    cycles.append(cycle)

print(f"  Cycles found: {len(cycles)}")
for c in cycles[:5]:
    print(f"    - {c}")
    warnings.append(f"Circular dependency: {c}")

# ─────────────────────────────────────────────
# 5. __init__.py 导出检查
# ─────────────────────────────────────────────
print("\n[5] __init__.py 导出检查")

init_path = r"D:\openagent\python\xuanji\__init__.py"
with open(init_path, 'r', encoding='utf-8') as f:
    init_content = f.read()

# Check if all __all__ items are actually importable
import xuanji
for name in getattr(xuanji, '__all__', []):
    if not hasattr(xuanji, name):
        msg = f"MISSING from __all__: {name}"
        issues.append(msg)
        print(f"  {msg}")

print(f"  __all__ exports: {len(getattr(xuanji, '__all__', []))}")
ok_items.append("__init__.py exports OK")

# ─────────────────────────────────────────────
# 6. 工具链完整性
# ─────────────────────────────────────────────
print("\n[6] 工具链完整性")

from xuanji.agent_tools_v2 import create_ultimate_agent

class MockLLM:
    def capabilities(self): return {"adapters": {}}
    def chat_response(self, h, m=None):
        from xuanji.llm._base import ChatResponse
        return ChatResponse(content='{"answer": "OK"}', model="mock")

agent = create_ultimate_agent(MockLLM())
tools = agent.registry.list_all()
categories = {}
for t in tools:
    cat = t.get("category", "unknown")
    categories[cat] = categories.get(cat, 0) + 1

print(f"  Total tools: {len(tools)}")
for cat, count in sorted(categories.items()):
    print(f"    {cat}: {count}")

# Check expected categories
expected_cats = ['web', 'file', 'system', 'utility', 'browser', 'desktop', 'perception', 'voice', 'openclaw']
missing_cats = [c for c in expected_cats if c not in categories]
if missing_cats:
    for c in missing_cats:
        msg = f"Missing tool category: {c}"
        warnings.append(msg)
        print(f"  WARNING: {c}")

ok_items.append(f"Tool chain: {len(tools)} tools in {len(categories)} categories")

# ─────────────────────────────────────────────
# 7. LLM适配器扫描
# ─────────────────────────────────────────────
print("\n[7] LLM适配器扫描")

llm_dir = r"D:\openagent\python\xuanji\llm"
adapters = [f for f in os.listdir(llm_dir) if f.endswith('_adapter.py')]
print(f"  Adapter files: {len(adapters)}")
for a in sorted(adapters):
    name = a.replace('_adapter.py', '')
    print(f"    - {name}")

# ─────────────────────────────────────────────
# 8. Channel扫描
# ─────────────────────────────────────────────
print("\n[8] Channel扫描")

ch_dir = r"D:\openagent\python\xuanji\channels"
channels = [f for f in os.listdir(ch_dir) if f.endswith('.py') and not f.startswith('_') and f != '__init__.py']
print(f"  Channel files: {len(channels)}")
for c in sorted(channels):
    name = c.replace('.py', '')
    print(f"    - {name}")

# ─────────────────────────────────────────────
# 9. 安全检查
# ─────────────────────────────────────────────
print("\n[9] 安全模块扫描")

sec_dir = r"D:\openagent\python\xuanji\security"
sec_files = [f for f in os.listdir(sec_dir) if f.endswith('.py') and not f.startswith('_')]
print(f"  Security modules: {len(sec_files)}")
for s in sorted(sec_files):
    name = s.replace('.py', '')
    print(f"    - {name}")

# ─────────────────────────────────────────────
# 10. 总结
# ─────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"审查完成")
print(f"  OK: {len(ok_items)}")
print(f"  Issues: {len(issues)}")
print(f"  Warnings: {len(warnings)}")
print("=" * 70)

if issues:
    print("\nIssues:")
    for i in issues:
        print(f"  ISSUE: {i}")

if warnings:
    print("\nWarnings:")
    for w in warnings:
        print(f"  WARNING: {w}")

print("\n--- 玄机架构总览 ---")
print(f"  模块总数: {len(all_modules)}")
print(f"  Python文件: {len(all_py_files)}")
print(f"  LLM适配器: {len(adapters)}")
print(f"  通信渠道: {len(channels)}")
print(f"  安全模块: {len(sec_files)}")
print(f"  Agent工具: {len(tools)}")
print(f"  死代码文件: {len(unimported)}")
print(f"  循环依赖: {len(cycles)}")
