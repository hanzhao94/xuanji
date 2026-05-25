"""
xuanji 团队协作引擎

多Agent组队做项目:
  PM分解任务 → 分配给成员 → 并行执行 → 汇总结果
  共享项目记忆 → 不同角色看到不同视角 → 冲突自动解决

核心问题:
  1. 谁做什么(角色分工)
  2. 先做什么后做什么(任务依赖)
  3. 信息怎么共享(项目记忆)
  4. 冲突怎么办(仲裁机制)
  5. 怎么知道做完了(进度追踪)
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("xuanji.team")


# ============================================================
# 角色定义
# ============================================================

class Role(str, Enum):
    """团队角色"""
    PM = "pm"                  # 项目经理:分解任务、分配、跟踪
    ARCHITECT = "architect"    # 架构师:技术方案、架构决策
    DEVELOPER = "developer"    # 开发:写代码
    TESTER = "tester"          # 测试:写测试、跑测试
    REVIEWER = "reviewer"      # 审查:代码审查、质量把关
    DESIGNER = "designer"      # 设计:UI/UX设计
    RESEARCHER = "researcher"  # 研究:调研、搜索、分析
    WRITER = "writer"          # 写手:文档、文案
    DEVOPS = "devops"          # 运维:部署、监控
    CUSTOM = "custom"          # 自定义角色


@dataclass
class TeamMember:
    """团队成员"""
    name: str
    role: Role
    agent_id: str = ""          # 对应的Agent ID
    skills: List[str] = field(default_factory=list)
    max_concurrent: int = 1     # 最多同时做几个任务
    current_tasks: List[str] = field(default_factory=list)
    completed_count: int = 0
    failed_count: int = 0

    @property
    def available(self) -> bool:
        return len(self.current_tasks) < self.max_concurrent

    @property
    def success_rate(self) -> float:
        total = self.completed_count + self.failed_count
        return self.completed_count / total if total > 0 else 1.0


# ============================================================
# 任务定义
# ============================================================

class TaskStatus(str, Enum):
    PENDING = "pending"         # 等待分配
    ASSIGNED = "assigned"       # 已分配
    RUNNING = "running"         # 执行中
    REVIEW = "review"           # 等待审查
    DONE = "done"               # 完成
    FAILED = "failed"           # 失败
    BLOCKED = "blocked"         # 被阻塞(等依赖)


@dataclass
class TeamTask:
    """团队任务"""
    task_id: str = ""
    title: str = ""
    description: str = ""
    role: Role = Role.DEVELOPER       # 需要什么角色
    assignee: str = ""                 # 分配给谁
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5                  # 1-10,1最高

    # 依赖
    depends_on: List[str] = field(default_factory=list)  # 依赖的任务ID

    # 输入输出
    input_data: Dict = field(default_factory=dict)   # 前置任务的输出
    output_data: Dict = field(default_factory=dict)  # 本任务的输出
    output_files: List[str] = field(default_factory=list)

    # 时间
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    deadline: float = 0.0             # 超时时间

    # 结果
    result: str = ""
    error: str = ""
    review_comments: str = ""

    def __post_init__(self):
        if not self.task_id:
            self.task_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = time.time()

    @property
    def is_ready(self) -> bool:
        """依赖是否都完成了"""
        return True  # 由TeamProject检查

    @property
    def duration(self) -> float:
        if self.finished_at and self.started_at:
            return self.finished_at - self.started_at
        return 0


# ============================================================
# 项目
# ============================================================

@dataclass
class TeamProject:
    """团队项目"""

    project_id: str = ""
    name: str = ""
    description: str = ""

    # 团队
    members: Dict[str, TeamMember] = field(default_factory=dict)

    # 任务
    tasks: Dict[str, TeamTask] = field(default_factory=dict)

    # 项目记忆(所有成员共享)
    shared_context: Dict[str, Any] = field(default_factory=dict)
    decisions: List[Dict] = field(default_factory=list)

    # 状态
    status: str = "planning"  # planning/running/done/failed
    created_at: float = 0.0

    def __post_init__(self):
        if not self.project_id:
            self.project_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = time.time()

    def add_member(self, name: str, role: Role, **kwargs) -> TeamMember:
        member = TeamMember(name=name, role=role, **kwargs)
        self.members[name] = member
        return member

    def add_task(self, title: str, role: Role = Role.DEVELOPER,
                 description: str = "", depends_on: List[str] = None,
                 priority: int = 5, **kwargs) -> TeamTask:
        task = TeamTask(
            title=title, role=role, description=description,
            depends_on=depends_on or [], priority=priority, **kwargs
        )
        self.tasks[task.task_id] = task
        return task

    def get_ready_tasks(self) -> List[TeamTask]:
        """获取可执行的任务(依赖已完成 + 未分配)"""
        ready = []
        done_ids = {tid for tid, t in self.tasks.items()
                    if t.status == TaskStatus.DONE}

        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            # 检查依赖
            if all(dep in done_ids for dep in task.depends_on):
                ready.append(task)

        # 按优先级排序
        ready.sort(key=lambda t: t.priority)
        return ready

    def get_tasks_for_role(self, role: Role) -> List[TeamTask]:
        """获取某角色的待做任务"""
        return [t for t in self.get_ready_tasks() if t.role == role]

    def find_assignee(self, task: TeamTask) -> Optional[TeamMember]:
        """为任务找合适的成员"""
        candidates = [
            m for m in self.members.values()
            if m.role == task.role and m.available
        ]
        if not candidates:
            # 没有专职角色,找有相关技能的
            candidates = [
                m for m in self.members.values()
                if m.available and task.role.value in m.skills
            ]
        if not candidates:
            return None
        # 按成功率排序
        candidates.sort(key=lambda m: m.success_rate, reverse=True)
        return candidates[0]

    def progress(self) -> Dict:
        """项目进度"""
        total = len(self.tasks)
        if total == 0:
            return {"total": 0, "done": 0, "percent": 0, "by_status": {}}

        by_status = {}
        for t in self.tasks.values():
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1

        done = by_status.get("done", 0)
        return {
            "total": total,
            "done": done,
            "percent": round(done / total * 100, 1),
            "by_status": by_status,
        }

    def share_context(self, key: str, value: Any, from_member: str = ""):
        """共享信息到项目级别"""
        self.shared_context[key] = {
            "value": value,
            "from": from_member,
            "ts": time.time(),
        }

    def record_decision(self, decision: str, by: str = "", reason: str = ""):
        """记录项目决策"""
        self.decisions.append({
            "decision": decision,
            "by": by,
            "reason": reason,
            "ts": time.time(),
        })


# ============================================================
# 团队引擎
# ============================================================

class TeamEngine:
    """团队协作引擎

    管理多Agent团队做项目的完整流程:

    1. 组建团队 → 分配角色
    2. 分解任务 → 建立依赖关系
    3. 自动分配 → 按角色+技能匹配
    4. 并行执行 → 无依赖的任务同时跑
    5. 结果传递 → 前置任务输出→后续任务输入
    6. 冲突解决 → 多人改同一文件时仲裁
    7. 进度追踪 → 实时状态
    8. 汇总交付 → 合并所有产出
    """

    def __init__(self, memory_manager=None, bus=None, persona_lib=None, arbiter=None):
        """
        Args:
            memory_manager: 统一记忆管理器(项目记忆用)
            bus: 消息总线(Agent间通信用)
            persona_lib: 专家人格库(角色自动匹配人格)
            arbiter: 资源仲裁器(多Agent冲突解决)
        """
        self.memory = memory_manager
        self.bus = bus
        self.persona_lib = persona_lib  # 专家人格库
        self.arbiter = arbiter  # 资源仲裁器
        self.projects: Dict[str, TeamProject] = {}

        # 任务执行回调(由外部注册,实际派Agent执行)
        self._task_executor: Optional[Callable] = None

    def set_executor(self, executor: Callable):
        """设置任务执行器

        executor签名: async def execute(task, member, project) -> result
        """
        self._task_executor = executor

    # === 项目管理 ===

    def create_project(self, name: str, description: str = "") -> TeamProject:
        """创建项目"""
        project = TeamProject(name=name, description=description)
        self.projects[project.project_id] = project
        return project

    def quick_team(self, project: TeamProject,
                   roles: List[str] = None) -> TeamProject:
        """快速组建团队

        Args:
            roles: 角色列表,如 ["pm", "developer", "tester"]
                   不传则默认 PM+Developer+Tester
        """
        if roles is None:
            roles = ["pm", "developer", "tester"]

        role_names = {
            "pm": ("项目经理", Role.PM),
            "architect": ("架构师", Role.ARCHITECT),
            "developer": ("开发", Role.DEVELOPER),
            "dev": ("开发", Role.DEVELOPER),
            "tester": ("测试", Role.TESTER),
            "test": ("测试", Role.TESTER),
            "reviewer": ("审查", Role.REVIEWER),
            "designer": ("设计师", Role.DESIGNER),
            "researcher": ("研究员", Role.RESEARCHER),
            "writer": ("文档", Role.WRITER),
            "devops": ("运维", Role.DEVOPS),
        }

        for r in roles:
            r_lower = r.lower()
            if r_lower in role_names:
                name, role = role_names[r_lower]
                project.add_member(name, role)
            else:
                project.add_member(r, Role.CUSTOM)

        return project

    async def decompose_task(self, project: TeamProject,
                              goal: str,
                              llm_adapter=None) -> List[TeamTask]:
        """用LLM分解项目目标为具体任务

        Args:
            project: 项目
            goal: 项目目标描述
            llm_adapter: LLM适配器(用于智能分解)

        Returns:
            分解出的任务列表
        """
        if llm_adapter:
            # 用LLM智能分解
            members_desc = ", ".join([
                f"{m.name}({m.role.value})"
                for m in project.members.values()
            ])

            prompt = (
                f"你是项目经理。团队成员: {members_desc}\n"
                f"项目目标: {goal}\n\n"
                "请把项目分解为具体任务,每个任务一行,格式:\n"
                "角色|优先级(1-10)|任务标题|描述|依赖(逗号分隔的任务序号,没有写无)\n\n"
                "例如:\n"
                "architect|1|设计架构|确定技术方案和模块划分|无\n"
                "developer|3|实现核心模块|按架构设计实现|1\n"
                "tester|5|编写测试|为核心模块写单元测试|2\n"
            )

            response = await llm_adapter.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=1000
            )
            content = str(response)

            # 解析LLM输出
            tasks = []
            task_map = {}  # 序号→task_id映射

            for i, line in enumerate(content.strip().split("\n"), 1):
                line = line.strip().lstrip("0123456789.-) ")
                parts = line.split("|")
                if len(parts) >= 3:
                    role_str = parts[0].strip().lower()
                    try:
                        priority = int(parts[1].strip())
                    except ValueError:
                        priority = 5
                    title = parts[2].strip()
                    desc = parts[3].strip() if len(parts) > 3 else ""
                    deps_str = parts[4].strip() if len(parts) > 4 else ""

                    # 角色映射
                    role_map = {
                        "pm": Role.PM, "architect": Role.ARCHITECT,
                        "developer": Role.DEVELOPER, "dev": Role.DEVELOPER,
                        "tester": Role.TESTER, "test": Role.TESTER,
                        "reviewer": Role.REVIEWER, "designer": Role.DESIGNER,
                        "researcher": Role.RESEARCHER, "writer": Role.WRITER,
                        "devops": Role.DEVOPS,
                    }
                    role = role_map.get(role_str, Role.DEVELOPER)

                    # 解析依赖
                    deps = []
                    if deps_str and deps_str != "无" and deps_str != "none":
                        for d in deps_str.split(","):
                            d = d.strip()
                            if d.isdigit() and int(d) in task_map:
                                deps.append(task_map[int(d)])

                    task = project.add_task(
                        title=title, role=role,
                        description=desc, depends_on=deps,
                        priority=priority
                    )
                    task_map[i] = task.task_id
                    tasks.append(task)

            return tasks
        else:
            # 无LLM,返回单个任务
            task = project.add_task(
                title=goal, role=Role.DEVELOPER,
                description=goal, priority=5
            )
            return [task]

    # === 执行引擎 ===

    async def run_project(self, project: TeamProject,
                          llm_adapter=None,
                          max_parallel: int = 3,
                          timeout: float = 600) -> Dict:
        """运行项目--自动分配+并行执行+汇总

        Args:
            project: 项目
            llm_adapter: LLM适配器
            max_parallel: 最大并行任务数
            timeout: 总超时(秒)

        Returns:
            项目结果
        """
        project.status = "running"
        start_time = time.time()

        # 如果没有任务,先用LLM分解
        if not project.tasks and llm_adapter:
            await self.decompose_task(project, project.description, llm_adapter)
            logger.info(f"[Team] Decomposed {len(project.tasks)} tasks for {project.name}")

        while True:
            # 超时检查
            if time.time() - start_time > timeout:
                project.status = "timeout"
                break

            # 获取可执行的任务
            ready = project.get_ready_tasks()
            if not ready:
                # 没有可执行的任务
                progress = project.progress()
                if progress["done"] == progress["total"]:
                    project.status = "done"
                    break

                # 有任务但都被阻塞
                running = [t for t in project.tasks.values()
                          if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED)]
                if not running:
                    # 没有在跑的也没有ready的 = 死锁
                    project.status = "deadlocked"
                    break

                # 等在跑的完成
                await asyncio.sleep(1)
                continue

            # 分配并执行(最多max_parallel个并行)
            batch = ready[:max_parallel]
            coros = []

            for task in batch:
                # 找人
                assignee = project.find_assignee(task)
                if not assignee:
                    # 没有合适的人,跳过
                    continue

                # 分配
                task.assignee = assignee.name
                task.status = TaskStatus.ASSIGNED
                assignee.current_tasks.append(task.task_id)

                # 传递前置任务的输出
                for dep_id in task.depends_on:
                    dep_task = project.tasks.get(dep_id)
                    if dep_task and dep_task.output_data:
                        task.input_data.update(dep_task.output_data)

                # 执行
                coros.append(self._execute_task(task, assignee, project, llm_adapter))

            if coros:
                # 并行执行这批任务,带超时保护
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time > 0:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*coros, return_exceptions=True),
                            timeout=remaining_time
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"[Team] Batch timeout ({remaining_time:.0f}s remaining)")
                else:
                    project.status = "timeout"
                    break
            else:
                await asyncio.sleep(0.5)

        # 汇总
        return self._summarize_project(project)

    async def _execute_task(self, task: TeamTask, member: TeamMember,
                             project: TeamProject, llm_adapter=None):
        """执行单个任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()

        try:
            if self._task_executor:
                # 用注册的执行器（带超时保护）
                result = await asyncio.wait_for(
                    self._task_executor(task, member, project),
                    timeout=180  # 每任务最多3分钟
                )
            elif llm_adapter:
                # 用LLM执行
                result = await self._llm_execute(task, member, project, llm_adapter)
            else:
                result = f"模拟完成: {task.title}"

            # 成功
            task.status = TaskStatus.DONE
            task.result = str(result)
            task.output_data = {"result": str(result)}
            task.finished_at = time.time()
            member.completed_count += 1

            # 存入项目记忆
            if self.memory:
                await self.memory.remember(
                    f"[{project.name}] {member.name}完成: {task.title} → {str(result)[:200]}",
                    importance=5,
                    tags=["project", project.project_id, "task_done"]
                )

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.finished_at = time.time()
            member.failed_count += 1

            if self.memory:
                await self.memory.remember(
                    f"[{project.name}] {member.name}失败: {task.title} → {str(e)[:200]}",
                    importance=8,
                    tags=["project", project.project_id, "task_failed"]
                )

        finally:
            if task.task_id in member.current_tasks:
                member.current_tasks.remove(task.task_id)

    async def _llm_execute(self, task: TeamTask, member: TeamMember,
                            project: TeamProject, llm_adapter) -> str:
        """用LLM执行任务"""
        # 组装上下文
        context_parts = [
            f"你是{member.name},角色是{member.role.value}。",
            f"项目: {project.name}",
            f"任务: {task.title}",
        ]

        if task.description:
            context_parts.append(f"详细描述: {task.description}")

        if task.input_data:
            context_parts.append(f"前置任务输出: {json.dumps(task.input_data, ensure_ascii=False)[:500]}")

        # 注入项目共享上下文
        if project.shared_context:
            ctx_items = []
            for k, v in project.shared_context.items():
                ctx_items.append(f"- {k}: {v['value']}")
            context_parts.append("项目共享信息:\n" + "\n".join(ctx_items[:5]))

        # 注入项目决策
        if project.decisions:
            recent = project.decisions[-3:]
            dec_text = "\n".join([f"- {d['decision']}" for d in recent])
            context_parts.append(f"最近决策:\n{dec_text}")

        system_prompt = "\n".join(context_parts)

        response = await llm_adapter.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请完成任务: {task.title}\n{task.description}"}
            ],
            max_tokens=500
        )

        return str(response)

    def _summarize_project(self, project: TeamProject) -> Dict:
        """汇总项目结果"""
        progress = project.progress()

        # 收集所有产出
        outputs = {}
        for task in project.tasks.values():
            if task.output_data:
                outputs[task.title] = task.output_data

        # 时间统计
        task_times = [t.duration for t in project.tasks.values() if t.duration > 0]
        total_time = sum(task_times)

        return {
            "project": project.name,
            "status": project.status,
            "progress": progress,
            "total_time": round(total_time, 1),
            "members": {
                name: {
                    "role": m.role.value,
                    "completed": m.completed_count,
                    "failed": m.failed_count,
                    "success_rate": round(m.success_rate * 100, 1),
                }
                for name, m in project.members.items()
            },
            "tasks": {
                t.task_id: {
                    "title": t.title,
                    "status": t.status.value,
                    "assignee": t.assignee,
                    "duration": round(t.duration, 1),
                    "result": t.result[:200] if t.result else "",
                }
                for t in project.tasks.values()
            },
            "outputs": outputs,
            "decisions": project.decisions,
        }

    # === 便捷方法 ===

    async def quick_project(self, name: str, goal: str,
                             roles: List[str] = None,
                             llm_adapter=None) -> Dict:
        """一键跑项目:组队→分解→执行→汇总

        用法:
            result = await engine.quick_project(
                "我的项目",
                "开发一个TODO应用",
                roles=["architect", "developer", "tester"],
                llm_adapter=llm
            )
        """
        # 1. 创建项目
        project = self.create_project(name, goal)

        # 2. 组队
        self.quick_team(project, roles)

        # 3. 分解任务
        await self.decompose_task(project, goal, llm_adapter)

        # 4. 执行
        result = await self.run_project(project, llm_adapter)

        return result
