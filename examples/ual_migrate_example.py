# UAL 示例项目

演示如何从 OpenClaw 迁移 skills/tools/memories 到玄机。

## 快速开始

```bash
# 进入玄机项目
cd D:\openagent

# 运行迁移
python -c "
import sys; sys.path.insert(0, 'python')
from xuanji.adapters import AdapterRegistry
from xuanji.adapters.migrate import MigrationEngine
from xuanji.skill import SkillLoader
from xuanji.agent_runner import ToolRegistry

# 1. 迁移
result = AdapterRegistry.auto_migrate(r"/path/to/workspace")
print(f'迁移: {len(result.skills)} skills, {len(result.tools)} tools, {len(result.memories)} memories')

# 2. 注入
skill_loader = SkillLoader()
tool_registry = ToolRegistry()
engine = MigrationEngine(skill_loader=skill_loader, tool_registry=tool_registry)
counts = engine.apply_all(result)
print(f'注入: {counts}')

# 3. 验证
matched = skill_loader.match('workflow')
if matched:
    print(f'match: {matched.name}')

# 4. 执行
if 'agentic-workflow-automation' in tool_registry._tools:
    func = tool_registry._tools['agentic-workflow-automation'].get('func')
    if func:
        r = func(workflow_name='demo', steps=[{'name': 'step1', 'type': 'task'}])
        print(f'执行: {r.get(\"output\", {}).get(\"summary\", \"OK\")}')
"
```

## 输出示例

```
迁移: 43 skills, 43 tools, 70 memories
注入: {'skills': 43, 'tools': 43, 'memories': 70}
match: agentic-workflow-automation
执行: Generated workflow blueprint with 1 steps
```

## 可执行技能

| 技能 | 功能 |
|------|------|
| agentic-workflow-automation | 生成工作流蓝图 |
| content-recycler | 内容多平台转换 |
| content-calendar | 内容日历生成 |
| optimize-hashtags | 标签优化 |
| seo-autopilot | SEO 审计 |

## 扩展你自己的适配器

```python
from xuanji.adapters import BaseAdapter, AdapterRegistry, MigrationResult

class MyAdapter(BaseAdapter):
    name = "my_framework"
    detect_patterns = ["my_config.json"]
    
    def migrate(self, path: str) -> MigrationResult:
        result = MigrationResult()
        # 你的迁移逻辑
        return result

AdapterRegistry.register(MyAdapter())
```
