"""
xuanji 贡献者指南生成器

生成标准化的CONTRIBUTING.md文件。

用法 (CLI):
  xuanji contributing generate    生成CONTRIBUTING.md
  xuanji contributing preview     预览内容

用法 (API):
  from xuanji.contributing import ContributingGuide
  guide = ContributingGuide()
  md = guide.generate()
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class ContributingGuide:
    """贡献者指南生成器"""

    def __init__(self, project_name: str = "xuanji", repo_url: str = None,
                 python_version: str = "3.10+", custom_sections: Dict = None):
        self.project_name = project_name
        self.repo_url = repo_url or "https://github.com/xuanji/xuanji"
        self.python_version = python_version
        self.custom_sections = custom_sections or {}
        self.generated_at = datetime.now().strftime("%Y-%m-%d")

    def generate(self) -> str:
        """生成完整的CONTRIBUTING.md"""
        sections = []

        # 头部
        sections.append(self._header())

        # 行为准则
        sections.append(self._code_of_conduct())

        # 开发环境
        sections.append(self._dev_environment())

        # 代码规范
        sections.append(self._code_style())

        # 提交规范
        sections.append(self._commit_conventions())

        # PR流程
        sections.append(self._pr_workflow())

        # 测试要求
        sections.append(self._testing())

        # 代码审查
        sections.append(self._code_review())

        # 文档贡献
        sections.append(self._documentation())

        # 报告问题
        sections.append(self._reporting_issues())

        # 自定义章节
        for title, content in self.custom_sections.items():
            sections.append(f"## {title}\n\n{content}\n")

        return "\n".join(sections)

    def _header(self) -> str:
        return f"""# 贡献给 {self.project_name}

感谢你对 {self.project_name} 的贡献感兴趣！

本文档提供了贡献指南和最佳实践。请在提交代码之前阅读。

> ⚡ 快速开始:
> ```bash
> git clone {self.repo_url}.git
> cd xuanji
> pip install -e ".[dev]"
> pytest
> ```

---
"""

    def _code_of_conduct(self) -> str:
        return """## 行为准则

本项目采用[贡献者公约](https://www.contributor-covenant.org/)作为行为准则。

### 我们的承诺

- 使用友好和包容的语言
- 尊重不同的观点和经验
- 优雅地接受建设性批评
- 关注对社区最有利的事情
- 对其他社区成员表示同理心

### 不可接受的行为

- 使用性化的语言或图像
- 人身攻击或侮辱性评论
- 公开或私下骚扰
- 未经许可发布他人私人信息
- 其他不道德或不专业的行为

---
"""

    def _dev_environment(self) -> str:
        return f"""## 开发环境设置

### 前置要求

- Python {self.python_version}
- Git 2.30+
- 代码编辑器（推荐 VS Code 或 PyCharm）

### 步骤

1. **Fork 仓库**
   ```bash
   # 在GitHub上Fork本仓库到你的账户
   ```

2. **克隆到本地**
   ```bash
   git clone {self.repo_url}.git
   cd xuanji
   git remote add upstream {self.repo_url}.git
   ```

3. **创建虚拟环境**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # 或
   .venv\\Scripts\\activate   # Windows
   ```

4. **安装依赖**
   ```bash
   pip install -e ".[dev]"
   ```

5. **验证安装**
   ```bash
   pytest --tb=short
   # 所有测试通过 ✅
   ```

6. **创建功能分支**
   ```bash
   git checkout -b feature/my-feature
   ```

### 开发工具

推荐安装以下工具以获得最佳开发体验：

```bash
pip install black isort flake8 mypy pytest pytest-cov pre-commit

# 配置pre-commit hooks
pre-commit install
```

---
"""

    def _code_style(self) -> str:
        return """## 代码规范

### Python代码风格

- 遵循 [PEP 8](https://peps.python.org/pep-0008/) 规范
- 使用 [Black](https://black.readthedocs.io/) 格式化代码
- 使用 [isort](https://pycqa.github.io/isort/) 排序导入
- 行宽: 100字符

```bash
# 格式化代码
black xuanji/
isort xuanji/

# 检查代码
flake8 xuanji/
mypy xuanji/
```

### 命名规范

- **模块名**: `snake_case`
- **类名**: `PascalCase`
- **函数/变量**: `snake_case`
- **常量**: `UPPER_SNAKE_CASE`
- **私有成员**: `_leading_underscore`

### 文档字符串

使用Google风格的文档字符串：

```python
def my_function(param1: str, param2: int = 10) -> dict:
    \"\"\"简要描述。

    详细描述（可选）。

    Args:
        param1: 参数1描述
        param2: 参数2描述，默认为10

    Returns:
        返回值描述

    Raises:
        ValueError: 当参数无效时

    Example:
        >>> result = my_function("hello")
        >>> print(result)
        {"status": "ok"}
    \"\"\"
    pass
```

### 类型注解

所有公开API必须使用类型注解：

```python
from typing import List, Optional, Dict, Any

def process(data: List[Dict[str, Any]], threshold: float = 0.5) -> Optional[Dict]:
    ...
```

---
"""

    def _commit_conventions(self) -> str:
        return """## 提交规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

### 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type类型

| Type       | 描述                     | Changelog分类 |
|------------|--------------------------|---------------|
| `feat`     | 新功能                   | Added         |
| `fix`      | 修复bug                  | Fixed         |
| `perf`     | 性能优化                 | Changed       |
| `refactor` | 代码重构                 | Changed       |
| `docs`     | 文档变更                 | Changed       |
| `style`    | 代码格式（不影响功能）    | Changed       |
| `test`     | 添加或修改测试           | Changed       |
| `chore`    | 构建/工具配置            | Changed       |
| `ci`       | CI配置                   | Changed       |
| `revert`   | 回退之前的提交           | Removed       |
| `security` | 安全相关修复             | Security      |

### 示例

```bash
# 新功能
git commit -m "feat(memory): 添加向量检索缓存"

# 修复bug
git commit -m "fix(llm): 修复流式响应中断问题"

# Breaking Change
git commit -m "feat(runtime)!: 重构插件加载机制

BREAKING CHANGE: 插件入口格式从 'module:Class' 改为 'module.Class'"

# 多行提交
git commit -m "feat(tool): 添加文件操作工具

- 支持读写多种文件格式
- 添加文件类型检测
- 支持大文件分块处理"
```

### 提交规则

1. **每个提交只做一件事** — 便于review和回退
2. **提交信息要清晰** — 让人知道"为什么"而不只是"做了什么"
3. **不要提交无关改动** — 格式调整单独提交
4. **及时提交** — 不要积累大量改动再一次性提交

---
"""

    def _pr_workflow(self) -> str:
        return """## Pull Request 流程

### 步骤

1. **更新分支**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **确保测试通过**
   ```bash
   pytest -v
   black --check xuanji/
   isort --check-only xuanji/
   flake8 xuanji/
   ```

3. **推送分支**
   ```bash
   git push origin feature/my-feature
   ```

4. **创建PR**
   - 在GitHub上创建Pull Request
   - 填写PR模板
   - 关联相关Issue

### PR模板

```markdown
## 描述

简要描述这个PR做了什么。

## 相关Issue

Fixes #(issue号)

## 变更类型

- [ ] 新功能 (feat)
- [ ] Bug修复 (fix)
- [ ] 代码重构 (refactor)
- [ ] 文档更新 (docs)
- [ ] 性能优化 (perf)
- [ ] 测试相关 (test)
- [ ] 其他 (chore)

## 测试

- [ ] 已添加单元测试
- [ ] 所有测试通过
- [ ] 手动测试通过

## 检查清单

- [ ] 代码遵循项目规范
- [ ] 已更新相关文档
- [ ] 变更日志已更新 (CHANGELOG.md)
- [ ] 无breaking change（或有详细说明）
```

### PR规范

- **标题清晰**: 使用conventional commit格式
- **描述完整**: 说明做了什么、为什么做、如何测试
- **保持小巧**: 每个PR尽量只做一个功能
- **及时回应**: 对review意见及时回复和修改

---
"""

    def _testing(self) -> str:
        return """## 测试要求

### 测试覆盖

- **新代码必须包含测试**
- 单元测试覆盖率不低于 80%
- 核心模块（runtime, memory, llm）覆盖率不低于 90%

### 运行测试

```bash
# 运行所有测试
pytest

# 运行指定模块测试
pytest tests/test_memory.py -v

# 查看覆盖率
pytest --cov=xuanji --cov-report=html

# 运行特定标记的测试
pytest -m slow
pytest -m integration
```

### 测试编写规范

```python
import pytest
from xuanji.memory import MemoryStore


class TestMemoryStore:
    \"\"\"MemoryStore测试\"\"\"

    def test_create_store(self):
        \"\"\"测试创建存储\"\"\"
        store = MemoryStore()
        assert store is not None

    def test_write_and_read(self):
        \"\"\"测试写入和读取\"\"\"
        store = MemoryStore()
        store.write("key", {"data": "value"})
        result = store.read("key")
        assert result == {"data": "value"}

    @pytest.mark.parametrize("input,expected", [
        ("hello", "HELLO"),
        ("world", "WORLD"),
        ("", ""),
    ])
    def test_transform(self, input, expected):
        \"\"\"参数化测试\"\"\"
        assert input.upper() == expected

    @pytest.mark.asyncio
    async def test_async_operation(self):
        \"\"\"异步测试\"\"\"
        store = MemoryStore()
        await store.write_async("key", "value")
        result = await store.read_async("key")
        assert result == "value"
```

### 测试分类

- **单元测试** (`tests/unit/`): 测试单个函数/类
- **集成测试** (`tests/integration/`): 测试模块间交互
- **端到端测试** (`tests/e2e/`): 测试完整流程

---
"""

    def _code_review(self) -> str:
        return """## 代码审查

### 审查标准

所有PR都需要至少1人review才能合并。审查关注：

1. **正确性** — 代码是否实现了预期功能？
2. **安全性** — 是否有安全漏洞？
3. **性能** — 是否有明显的性能问题？
4. **可读性** — 代码是否清晰易懂？
5. **测试** — 是否有足够的测试覆盖？
6. **文档** — 是否有必要的文档更新？

### Reviewer检查清单

- [ ] 代码逻辑正确
- [ ] 没有安全漏洞
- [ ] 性能可接受
- [ ] 代码风格一致
- [ ] 测试充分
- [ ] 文档更新
- [ ] 无硬编码敏感信息
- [ ] 错误处理完善

### 作者检查清单

提交PR前确认：

- [ ] 所有测试通过
- [ ] 代码已格式化 (black + isort)
- [ ] 无flake8警告
- [ ] 提交信息清晰
- [ ] PR描述完整
- [ ] 已更新CHANGELOG.md

---
"""

    def _documentation(self) -> str:
        return """## 文档贡献

文档和代码同等重要！

### 文档类型

- **API文档**: 代码中的docstring，自动生成
- **使用指南**: `docs/guides/` 目录
- **教程**: `docs/tutorials/` 目录
- **架构文档**: `docs/architecture/` 目录

### 编写规范

- 使用清晰的中文
- 包含代码示例
- 保持更新，随代码变更同步
- 使用Markdown格式

### 本地预览

```bash
# 安装文档依赖
pip install -e ".[docs]"

# 启动文档服务器
mkdocs serve

# 访问 http://localhost:8000 预览
```

---
"""

    def _reporting_issues(self) -> str:
        return """## 报告问题

### Bug报告

使用GitHub Issue模板报告bug：

```markdown
**Bug描述**
清晰简洁地描述bug。

**复现步骤**
1. 执行 '...'
2. 点击 '...'
3. 看到错误

**预期行为**
描述你期望发生的事情。

**实际行为**
描述实际发生的事情。

**环境信息**
- Python版本: 
- xuanji版本: 
- 操作系统: 

**附加信息**
截图、日志等。
```

### 功能建议

```markdown
**功能描述**
你想要什么功能？

**使用场景**
这个功能解决什么问题？

**替代方案**
你考虑过其他解决方案吗？

**附加信息**
其他相关上下文。
```

---

## 感谢所有贡献者

❤️ 感谢每一位为 {self.project_name} 做出贡献的人！

[贡献者列表]({self.repo_url}/graphs/contributors)

---

*最后更新: {self.generated_at}*
""".format(self=self)

    def write(self, filepath: str = None) -> str:
        """写入CONTRIBUTING.md文件"""
        content = self.generate()
        path = filepath or "CONTRIBUTING.md"
        Path(path).write_text(content, encoding="utf-8")
        return path

    def preview(self) -> str:
        """预览生成的内容"""
        return self.generate()
