"""
xuanji 反模式检测器

检测Agent的坏习惯，推荐成功模式，执行自检清单。

内置检测库：
  失败模式 F1-F7: 跳步/孤岛代码/重复手动/无实验断言/大文件重写/知识闲置/忽略失败
  反模式 A1-A8:   过度设计/跳过流程/重复造轮/任务太大/模糊语言/只写不读/只做不记/多真相源
  成功模式 S1-S6: 先量后质/知识积累/SKILL标准化/实验驱动/根因分析/交叉验证
  自检清单 4套:   代码完成/实验完成/阶段完成/项目完成

零外部依赖，纯Python标准库。

核心逻辑提炼自开源工程实践。
"""

import ast
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# 模式定义
# ═══════════════════════════════════════════════════════════════

FAILURE_PATTERNS = [
    {"id": "F1", "name": "跳过过程直奔结果"},
    {"id": "F2", "name": "零件写好了不串通"},
    {"id": "F3", "name": "手动做了两次还没做成工具"},
    {"id": "F4", "name": "不做实验就下结论"},
    {"id": "F5", "name": "大文件直接重写"},
    {"id": "F6", "name": "知识积累只存不用"},
    {"id": "F7", "name": "只关注成功忽略失败"},
]

ANTI_PATTERNS = [
    {"id": "A1", "name": "先全部设计再动手"},
    {"id": "A2", "name": "为了速度跳过流程"},
    {"id": "A3", "name": "有工具不用自己搞"},
    {"id": "A4", "name": "一个任务做太大", "max_desc_chars": 2000, "max_files": 5},
    {"id": "A5", "name": "手动估算代替精确计算"},
    {"id": "A6", "name": "只写不读"},
    {"id": "A7", "name": "只做不记"},
    {"id": "A8", "name": "多个真相源"},
]

SUCCESS_PATTERNS = [
    {"id": "S1", "name": "先出量再出质",
     "template": "先写完再修，但要有底线",
     "checklist": ["数量目标明确", "质量底线定义", "硬性禁止项", "修改阶段独立"]},
    {"id": "S2", "name": "知识库越写越好",
     "template": "第一个项目的最高水平=第二个项目的起点",
     "checklist": ["提取了精华", "写入了知识库", "新项目加载了知识库", "版本号递增"]},
    {"id": "S3", "name": "SKILL标准化",
     "template": "散落的经验→写成SKILL→新对话照着走",
     "checklist": ["经验不散落", "写成SKILL.md", "有明确步骤", "新对话能执行"]},
    {"id": "S4", "name": "实验驱动决策",
     "template": "有对照组、只改一个变量、数据说话",
     "checklist": ["有对照组", "只改一个变量", "结果用数据说话", "结论写入SKILL"]},
    {"id": "S5", "name": "错误分析到根因",
     "template": "不停留在什么错了，要问为什么错",
     "checklist": ["描述了现象", "分析了原因", "追到了根因", "制定了预防措施"]},
    {"id": "S6", "name": "交叉验证找真相",
     "template": "多个数据源有矛盾时，回到原始数据验证",
     "checklist": ["识别了矛盾", "列出所有数据源", "回到原始数据验证", "确定唯一真相源"]},
]

CHECKLISTS = {
    "code_done": {
        "name": "写完代码必查",
        "items": [
            "py_compile通过", "import不报错", "实际跑一遍有输出",
            "输出格式/类型正确", "评分/指标达标", "比上一版好还是差",
        ],
    },
    "experiment_done": {
        "name": "做完实验必查",
        "items": [
            "只改了一个变量", "有对比基线", "结果记录了",
            "锁定了还是否决了", "更新了STATUS.md",
        ],
    },
    "phase_done": {
        "name": "完成阶段必查",
        "items": [
            "死代码清理了", "STATUS更新了", "端到端跑通了",
            "记忆写入了", "SKILL需要更新吗",
        ],
    },
    "project_done": {
        "name": "项目完成必查",
        "items": [
            "复盘5问做了", "精华提取到知识库了",
            "SKILL更新了", "同步到记忆了",
        ],
    },
}

# 模糊词库
VAGUE_WORDS_CN = [
    "大概", "差不多", "估计", "可能", "应该", "大约", "左右",
    "差不离", "大致", "好像", "似乎", "也许", "或许",
]
VAGUE_WORDS_EN = [
    "approximately", "about", "roughly", "maybe", "probably",
    "around", "guess", "suppose", "might", "perhaps", "likely", "seems",
]

# 流程跳过指示词
SKIP_INDICATORS = [
    r'先写了再说', r'跳过测试', r'不用审查', r'直接上',
    r'不用大纲', r'直接写', r'省掉.*步骤', r'跳过.*流程',
    r'skip\s+test', r'no\s+review', r'just\s+ship',
]


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _collect_py_files(directory: str) -> List[str]:
    """递归收集目录下所有.py文件"""
    skip = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache"}
    py_files = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    return py_files


def _extract_imports(filepath: str) -> set:
    """从Python文件中提取所有import的模块名"""
    imports = set()
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            tree = ast.parse(f.read(), filename=filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    except (SyntaxError, UnicodeDecodeError, OSError):
        pass
    return imports


def _read_safe(filepath: str, max_bytes: int = 1024 * 1024) -> str:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_bytes)
    except (OSError, PermissionError):
        return ""


def _jsonl_append(filepath: str, record: dict):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════
# 主类
# ═══════════════════════════════════════════════════════════════

class AntipatternDetector:
    """
    反模式检测器 — 检测Agent的坏习惯，推荐好模式。

    集成失败模式检测、反模式检测、成功模式推荐、自检清单、复盘系统。
    """

    def __init__(self, project_dir: str, data_dir: str = "data"):
        self.project_dir = os.path.abspath(project_dir)
        self.data_dir = os.path.join(self.project_dir, data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.scan_log_path = os.path.join(self.data_dir, "scan_log.jsonl")

    # ───────────────────────────────────────────────────────────
    # 孤岛代码检测 (F2)
    # ───────────────────────────────────────────────────────────

    def detect_dead_code(self, directory: str = None) -> dict:
        """
        找到所有没被import的.py文件（孤岛代码）。

        排除：__init__.py, __main__.py, 测试文件, CLI入口文件
        """
        scan_dir = directory or self.project_dir
        py_files = _collect_py_files(scan_dir)
        if not py_files:
            return {"total_files": 0, "dead_files": [], "entry_point_only": []}

        stem_to_path = {Path(fp).stem: fp for fp in py_files}
        all_imported = set()
        for fp in py_files:
            all_imported.update(_extract_imports(fp))

        skip_prefixes = {"__init__", "__main__", "test_", "conftest", "setup"}
        dead_files, entry_only = [], []
        for stem, path in stem_to_path.items():
            if any(stem.startswith(p) or stem == p for p in skip_prefixes):
                continue
            if stem in all_imported:
                continue
            content = _read_safe(path, 8192)
            is_entry = ("if __name__" in content and "__main__" in content)
            entry = {"file": path, "stem": stem, "is_entry_point": is_entry}
            if is_entry:
                entry_only.append(entry)
            else:
                dead_files.append(entry)

        return {
            "total_files": len(py_files),
            "dead_files": dead_files,
            "entry_point_only": entry_only,
        }

    # ───────────────────────────────────────────────────────────
    # 模糊语言检测 (A5)
    # ───────────────────────────────────────────────────────────

    def check_vague_language(self, text: str) -> List[dict]:
        """检测中英文模糊词，返回位置和上下文"""
        findings = []
        for line_num, line in enumerate(text.split("\n"), 1):
            for word in VAGUE_WORDS_CN:
                for m in re.finditer(re.escape(word), line):
                    ctx = line[max(0, m.start() - 10):m.end() + 10].strip()
                    findings.append({"word": word, "lang": "cn", "line": line_num, "context": ctx})
            for word in VAGUE_WORDS_EN:
                for m in re.finditer(r'\b' + re.escape(word) + r'\b', line, re.IGNORECASE):
                    ctx = line[max(0, m.start() - 10):m.end() + 10].strip()
                    findings.append({"word": word, "lang": "en", "line": line_num, "context": ctx})
        return findings

    # ───────────────────────────────────────────────────────────
    # 任务大小检测 (A4)
    # ───────────────────────────────────────────────────────────

    def check_task_size(self, description: str, files: list) -> dict:
        """检查任务是否太大（描述>2000字或文件>5个）"""
        reasons = []
        desc_len = len(description)
        file_count = len(files)
        if desc_len > 2000:
            reasons.append(f"描述{desc_len}字，超过2000字上限")
        if file_count > 5:
            reasons.append(f"涉及{file_count}个文件，超过5个上限")
        actions = re.findall(
            r'(创建|修改|删除|重构|添加|实现|修复|优化|测试|部署|'
            r'create|modify|delete|refactor|add|implement|fix|optimize|test|deploy)',
            description, re.IGNORECASE,
        )
        if len(actions) > 5:
            reasons.append(f"包含{len(actions)}个动作词，建议拆分")
        return {
            "too_big": len(reasons) > 0,
            "description_chars": desc_len,
            "file_count": file_count,
            "action_count": len(actions),
            "reasons": reasons,
        }

    # ───────────────────────────────────────────────────────────
    # 文本反模式扫描
    # ───────────────────────────────────────────────────────────

    def scan_antipatterns(self, text: str = None, task: dict = None) -> List[dict]:
        """
        扫描文本/任务中的反模式。

        Args:
            text: 要检查的文本
            task: {"description": str, "files": list}

        Returns:
            检测到的反模式列表
        """
        findings = []
        if text:
            # A2: 跳过流程
            skip_found = []
            for pat in SKIP_INDICATORS:
                skip_found.extend(re.findall(pat, text, re.IGNORECASE))
            if skip_found:
                findings.append({
                    "pattern_id": "A2", "name": "为了速度跳过流程",
                    "severity": "HIGH", "details": {"indicators": skip_found},
                })
            # A5: 模糊语言
            vague = self.check_vague_language(text)
            if vague:
                findings.append({
                    "pattern_id": "A5", "name": "手动估算代替精确计算",
                    "severity": "MEDIUM" if len(vague) > 5 else "LOW",
                    "details": {"vague_words": vague},
                })
        if task:
            size = self.check_task_size(task.get("description", ""), task.get("files", []))
            if size["too_big"]:
                findings.append({
                    "pattern_id": "A4", "name": "一个任务做太大",
                    "severity": "HIGH", "details": size,
                })
        return findings

    # ───────────────────────────────────────────────────────────
    # 自检清单
    # ───────────────────────────────────────────────────────────

    def run_checklist(self, checklist_type: str, answers: dict = None) -> dict:
        """
        执行自检清单。

        Args:
            checklist_type: code_done / experiment_done / phase_done / project_done
            answers: {item_index_or_text: True/False}

        Returns:
            包含passed/failed/unchecked/score的字典
        """
        if checklist_type not in CHECKLISTS:
            return {"error": f"未知清单: {checklist_type}"}
        cl = CHECKLISTS[checklist_type]
        items = cl["items"]
        passed, failed, unchecked = [], [], []
        if answers:
            for i, item in enumerate(items):
                ans = answers.get(i, answers.get(item))
                if ans is True:
                    passed.append(item)
                elif ans is False:
                    failed.append(item)
                else:
                    unchecked.append(item)
        else:
            unchecked = list(items)
        total = len(items)
        score = round(len(passed) / total * 100, 1) if total else 0
        return {
            "checklist_type": checklist_type,
            "checklist_name": cl["name"],
            "total_items": total,
            "passed": passed,
            "failed": failed,
            "unchecked": unchecked,
            "score": score,
            "all_passed": len(passed) == total,
        }

    # ───────────────────────────────────────────────────────────
    # 成功模式推荐
    # ───────────────────────────────────────────────────────────

    def suggest_pattern(self, situation: str) -> List[dict]:
        """根据情况描述推荐适用的成功模式，按匹配度排序"""
        kw_map = {
            "S1": ["写完", "数量", "质量", "先做完", "底线", "精修", "批量"],
            "S2": ["知识库", "积累", "复用", "起点", "经验"],
            "S3": ["标准化", "skill", "流程", "规范", "方法论"],
            "S4": ["实验", "对比", "测试", "决策", "选择", "哪个好"],
            "S5": ["错误", "bug", "问题", "为什么", "根因", "失败"],
            "S6": ["矛盾", "不一致", "多个", "数据源", "冲突", "验证"],
        }
        sit_lower = situation.lower()
        scored = []
        for i, sp in enumerate(SUCCESS_PATTERNS):
            keywords = kw_map.get(sp["id"], [])
            score = sum(1 for kw in keywords if kw in sit_lower)
            if score > 0:
                scored.append({**sp, "match_score": score})
        scored.sort(key=lambda x: -x["match_score"])
        return scored

    # ───────────────────────────────────────────────────────────
    # 复盘
    # ───────────────────────────────────────────────────────────

    def retrospective(self, project: str, what_done: str, what_learned: str,
                      pitfalls: str, prevention: str, reusable: str) -> str:
        """项目复盘5问，生成报告并存档"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report = (
            f"# 项目复盘：{project}\n\n**日期**: {now}\n\n"
            f"## 1. 做了什么\n{what_done}\n\n"
            f"## 2. 学到什么\n{what_learned}\n\n"
            f"## 3. 踩了什么坑\n{pitfalls}\n\n"
            f"## 4. 怎么避免\n{prevention}\n\n"
            f"## 5. 什么可复用\n{reusable}\n"
        )
        retro_dir = os.path.join(self.data_dir, "retrospectives")
        os.makedirs(retro_dir, exist_ok=True)
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', project)
        date_str = datetime.now().strftime("%Y%m%d")
        path = os.path.join(retro_dir, f"{date_str}_{safe_name}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return report

    # ───────────────────────────────────────────────────────────
    # 完整扫描报告
    # ───────────────────────────────────────────────────────────

    def full_scan_report(self, directory: str = None) -> str:
        """完整扫描：孤岛代码 + 反模式检测，生成Markdown报告"""
        scan_dir = directory or self.project_dir
        lines = [
            f"# 🔍 反模式扫描报告",
            f"",
            f"**目录**: `{scan_dir}`",
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
        ]

        # 孤岛代码
        dead = self.detect_dead_code(scan_dir)
        lines.append("## 代码健康度")
        lines.append(f"- 总Python文件: {dead['total_files']}")
        lines.append(f"- 孤岛文件: {len(dead['dead_files'])}")
        lines.append(f"- 入口点文件: {len(dead['entry_point_only'])}")
        if dead["dead_files"]:
            lines.append("")
            lines.append("**孤岛代码（未被import）:**")
            for df in dead["dead_files"][:10]:
                lines.append(f"  - `{os.path.basename(df['file'])}`")
        lines.append("")

        # 统计
        total_issues = len(dead["dead_files"])
        if total_issues == 0:
            lines.append("**健康度**: 🌟 优秀")
        elif total_issues <= 3:
            lines.append(f"**健康度**: 🟢 良好 ({total_issues}个问题)")
        else:
            lines.append(f"**健康度**: 🟡 需改进 ({total_issues}个问题)")

        report = "\n".join(lines)
        _jsonl_append(self.scan_log_path, {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "full_scan",
            "scan_dir": scan_dir,
            "dead_files": len(dead["dead_files"]),
        })
        return report
