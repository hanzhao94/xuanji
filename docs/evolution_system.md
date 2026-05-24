# 玄机进化系统 v1.0

从"能跑"到"越跑越聪明"的四大进化引擎。

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    玄机进化系统 v1.0                          │
├──────────────┬──────────────┬──────────────┬────────────────┤
│ 失败模式学习  │ 成功模式复用  │ 跨任务泛化    │ 安全策略自适配  │
│ failure_     │ pattern_     │ cross_task_  │ adaptive_      │
│ learning.py  │ reuse.py     │ index.py     │ security.py    │
├──────────────┴──────────────┴──────────────┴────────────────┤
│                    进化反馈环                                │
│  失败→学习→调整→成功→沉淀→复用→泛化→自适应                   │
└─────────────────────────────────────────────────────────────┘
```

## 1. 失败模式学习 (failure_learning.py)

**功能**: 从失败中提取教训，沉淀为"坑点记忆"，下次同类任务自动规避。

**核心流程**:
```
错误捕获 → 分类 → 沉淀记忆 → 下次自动加载规避策略
```

**错误分类体系**:
- `sandbox_too_strict` - 沙盒过严阻止合法操作
- `sandbox_too_loose` - 沙盒过松漏掉危险操作
- `llm_syntax_error` - LLM生成代码语法错误
- `import_error` - 模块导入失败
- `runtime_error` - 运行时错误
- 等12种分类

**API**:
```python
from xuanji.failure_learning import FailureLearner, ErrorCategory

learner = FailureLearner(data_dir="workspace/pitfalls")

# 记录失败
learner.record(
    category=ErrorCategory.SANDBOX_TOO_STRICT,
    task_type="cli_app",
    error="禁止导入模块: os",
    root_cause="沙盒黑名单包含os，但os.path是安全的",
    workaround="从BLOCKED_MODULES移除os",
    prevention="下次任务自动调宽沙盒白名单",
    severity=3,
    confidence=0.95,
)

# 查询预防策略
strategies = learner.get_prevention_strategies("cli_app")

# 沙盒调整建议
adj = learner.should_adjust_sandbox("cli_app")
```

**已沉淀的坑点**:
1. 沙盒黑名单包含os → 允许os导入，用visit_Attribute拦截os.system
2. exec在BLOCKED_BUILTINS中 → 保留exec，Python import机制需要
3. Ollama生成代码有语法错误 → 代码生成后先AST解析验证

## 2. 成功模式复用 (pattern_reuse.py)

**功能**: 从跑通的任务中提取可复用套路，沉淀为"做事模板"，下次同类任务直接套用。

**核心流程**:
```
任务完成 → 提取要素 → 生成模板 → 下次任务自动加载
```

**模板格式**:
- 团队分工模板（角色+技能+任务）
- 代码结构模板（文件+用途+模板）
- 执行流程（步骤+动作+输出+验证）
- 测试策略（单元测试+集成测试+沙盒测试）
- 效果数据（时间+tokens+质量分数）

**API**:
```python
from xuanji.pattern_reuse import PatternLibrary, PatternExtractor

lib = PatternLibrary(data_dir="workspace/patterns")
extractor = PatternExtractor()

# 提取模式
pattern = extractor.extract_cli_app_pattern(task_result)
lib.save(pattern)

# 加载模板
template = lib.load_template("cli_app")
# 返回: {team_roles, code_structure, workflow, test_strategy, expected_metrics}

# 泛化查询
matches = lib.find(task_type="crud_app")  # 匹配cli_app模板
```

**已提取的模式**:
1. CLI应用开发标准流程（6步闭环）
2. 网页抓取+分析标准流程（6步闭环）

## 3. 跨任务泛化索引 (cross_task_index.py)

**功能**: 不同领域的经验在记忆库中交叉索引，LLM从不同领域的经验中提取共性策略。

**核心流程**:
```
任务完成 → 提取策略 → 交叉索引 → 泛化检索 → 共性策略提取
```

**策略模式库**:
- `divide_and_conquer` - 分而治之
- `iterative_refinement` - 迭代精炼
- `template_based` - 模板驱动
- `llm_driven` - LLM驱动
- `sandbox_verify` - 沙盒验证
- `memory_driven` - 记忆驱动

**API**:
```python
from xuanji.cross_task_index import CrossTaskIndex

index = CrossTaskIndex(data_dir="workspace/cross_index")

# 记录经验
index.record(
    task_type="cli_app",
    domain="development",
    strategy="分而治之→LLM驱动→记忆驱动",
    strategy_tags=["divide_and_conquer", "llm_driven", "memory_driven"],
    success=True,
    metrics={"time": 90, "tokens": 15000},
    generalizable_to=["crud_app", "data_tool"],
)

# 跨领域共性策略
common = index.find_common_strategies(
    domains=["development", "research", "analysis"],
)

# 推荐策略
recs = index.recommend_strategy("data_tool", "development")
```

## 4. 安全策略自适配 (adaptive_security.py)

**功能**: 沙盒严宽策略不是人工调，而是根据任务类型和风险等级自动分级。

**核心流程**:
```
任务接收 → 风险评估 → 策略分级 → 沙盒配置 → 执行 → 反馈调整
```

**风险分级**:
- `low` - 低风险：纯计算、只读操作
- `medium` - 中风险：文件读写、网络请求
- `high` - 高风险：进程执行、系统调用
- `critical` - 极高风险：网络写入、用户数据修改

**API**:
```python
from xuanji.adaptive_security import AdaptiveSecurityEngine, RiskLevel

engine = AdaptiveSecurityEngine(config_path="workspace/security_policy.json")

# 获取策略
policy = engine.get_policy({
    "category": "file_write",
    "has_process": True,
    "has_user_data": True,
})
print(f"风险等级: {policy.risk_level}")
print(f"允许模块: {policy.allow_modules}")
print(f"需要审批: {policy.require_approval}")

# 反馈执行结果
engine.feedback(task_features, policy, success=True, error=None)

# 快速获取
policy = get_sandbox_policy("cli_app")
```

## 集成使用

### 任务启动前
```python
from xuanji.failure_learning import FailureLearner, auto_adjust_for_task
from xuanji.pattern_reuse import PatternLibrary, auto_load_template
from xuanji.adaptive_security import get_sandbox_policy

learner = FailureLearner()
lib = PatternLibrary()

# 1. 加载预防策略
task_type = "cli_app"
config = {"sandbox": {...}}
config = auto_adjust_for_task(learner, task_type, config)

# 2. 加载成功模板
template = auto_load_template(lib, task_type)

# 3. 获取沙盒策略
policy = get_sandbox_policy(task_type)
```

### 任务完成后
```python
from xuanji.failure_learning import ErrorCategory
from xuanji.pattern_reuse import PatternExtractor
from xuanji.cross_task_index import CrossTaskIndex

learner = FailureLearner()
lib = PatternLibrary()
index = CrossTaskIndex()

if task_success:
    # 提取成功模式
    extractor = PatternExtractor()
    pattern = extractor.extract_cli_app_pattern(task_result)
    lib.save(pattern)
    
    # 记录经验索引
    index.record(
        task_type=task_type,
        domain="development",
        strategy="分而治之→LLM驱动→记忆驱动",
        strategy_tags=["divide_and_conquer", "llm_driven", "memory_driven"],
        success=True,
        metrics={"time": elapsed, "tokens": tokens_used},
    )
else:
    # 记录失败教训
    learner.record(
        category=ErrorCategory.LLM_SYNTAX_ERROR,
        task_type=task_type,
        error=error_message,
        root_cause=analysis,
        workaround=fix,
        prevention=prevention,
    )
```

## 文件结构

```
D:\openagent\python\xuanji\
├── failure_learning.py    # 失败模式学习
├── pattern_reuse.py       # 成功模式复用
├── cross_task_index.py    # 跨任务泛化索引
└── adaptive_security.py   # 安全策略自适配

D:\openagent\workspace\
├── pitfalls\              # 坑点记忆数据库
│   └── pitfalls.json
├── patterns\              # 成功模式数据库
│   └── patterns.json
├── cross_index\           # 跨任务索引数据库
│   └── experience_index.json
└── security_policy.json   # 安全策略配置
```

## 测试验证

```bash
# 测试单个模块
python python/xuanji/failure_learning.py
python python/xuanji/pattern_reuse.py
python python/xuanji/cross_task_index.py
python python/xuanji/adaptive_security.py

# 完整集成测试
python tests/test_full.py  # 47/47 PASS
```

## 下一步

1. 与现有任务调度器集成（在任务启动/完成时自动调用）
2. 积累更多坑点和模式（每跑通一个任务就提取）
3. 策略效果量化（对比使用/未使用进化系统的任务质量）
4. 与其他引擎交叉索引
