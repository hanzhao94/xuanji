"""
xuanji Changelog 自动生成器

基于git commit自动生成CHANGELOG.md，遵循Keep a Changelog规范。

用法 (CLI):
  xuanji changelog generate [版本]     生成changelog
  xuanji changelog diff <v1> <v2>      比较两个版本
  xuanji changelog since <commit>      从指定commit生成

用法 (API):
  from xuanji.changelog import ChangelogGenerator
  gen = ChangelogGenerator()
  md = gen.generate("0.2.0")
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# ============================================================
# Commit类型映射
# ============================================================

TYPE_MAPPING = {
    "feat": ("Added", "新增"),
    "fix": ("Fixed", "修复"),
    "perf": ("Changed", "优化"),
    "refactor": ("Changed", "重构"),
    "docs": ("Changed", "文档"),
    "style": ("Changed", "格式"),
    "test": ("Changed", "测试"),
    "chore": ("Changed", "构建"),
    "ci": ("Changed", "CI"),
    "build": ("Changed", "构建"),
    "revert": ("Removed", "回退"),
    "remove": ("Removed", "移除"),
    "deps": ("Changed", "依赖"),
    "security": ("Security", "安全"),
}

SECTION_ORDER = ["Added", "Changed", "Fixed", "Removed", "Security", "Deprecated"]


class Commit:
    """单次commit"""

    def __init__(self, hash: str, type: str, scope: str, subject: str, body: str,
                 author: str, date: str, is_breaking: bool = False):
        self.hash = hash
        self.type = type
        self.scope = scope
        self.subject = subject
        self.body = body
        self.author = author
        self.date = date
        self.is_breaking = is_breaking

    @property
    def section(self) -> str:
        """获取所属分类"""
        mapping = TYPE_MAPPING.get(self.type, ("Changed", ""))
        return mapping[0]

    @property
    def formatted_subject(self) -> str:
        """格式化后的subject"""
        prefix = f"**{self.scope}**: " if self.scope else ""
        return f"{prefix}{self.subject}"

    def to_dict(self) -> dict:
        return {
            "hash": self.hash,
            "type": self.type,
            "scope": self.scope,
            "subject": self.subject,
            "section": self.section,
            "breaking": self.is_breaking,
            "author": self.author,
            "date": self.date,
        }


class ChangelogGenerator:
    """Changelog生成器"""

    def __init__(self, repo_path: str = None):
        self.repo_path = repo_path or os.getcwd()
        self.commits: List[Commit] = []
        self._conventional_pattern = re.compile(
            r'^(\w+)(?:\(([^)]+)\))?!?:\s+(.+)$'
        )
        self._breaking_pattern = re.compile(r'BREAKING\s*CHANGE', re.IGNORECASE)

    # ============================================================
    # Git操作
    # ============================================================

    def _run_git(self, args: List[str]) -> str:
        """执行git命令"""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def _get_tags(self) -> List[str]:
        """获取所有tag"""
        output = self._run_git(["tag", "-l", "--sort=-v:refname"])
        return [t for t in output.split("\n") if t] if output else []

    def _get_commits(self, range_spec: str = None) -> List[Commit]:
        """获取commit列表"""
        format_str = "%H%n%an%n%ai%n%s%n%b%n----COMMIT_SEPARATOR----"

        if range_spec:
            args = ["log", range_spec, "--format=" + format_str, "--no-merges"]
        else:
            args = ["log", "--format=" + format_str, "--no-merges"]

        output = self._run_git(args)
        if not output:
            return []

        commits = []
        blocks = output.split("----COMMIT_SEPARATOR----")

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            lines = block.split("\n")
            if len(lines) < 4:
                continue

            commit_hash = lines[0].strip()[:8]
            author = lines[1].strip()
            date = lines[2].strip()
            subject = lines[3].strip()
            body = "\n".join(lines[4:]).strip() if len(lines) > 4 else ""

            # 解析conventional commit
            match = self._conventional_pattern.match(subject)
            if match:
                commit_type = match.group(1)
                scope = match.group(2) or ""
                msg = match.group(3)
            else:
                commit_type = "changed"
                scope = ""
                msg = subject

            is_breaking = bool(self._breaking_pattern.search(body)) or subject.endswith("!")

            commit = Commit(
                hash=commit_hash,
                type=commit_type,
                scope=scope,
                subject=msg,
                body=body,
                author=author,
                date=date,
                is_breaking=is_breaking,
            )
            commits.append(commit)

        return commits

    # ============================================================
    # 生成Changelog
    # ============================================================

    def generate(self, version: str = None, since: str = None, until: str = None) -> str:
        """生成CHANGELOG Markdown"""
        # 确定版本
        if not version:
            tags = self._get_tags()
            version = tags[0] if tags else "unreleased"

        # 确定commit范围
        range_spec = None
        if since and until:
            range_spec = f"{since}..{until}"
        elif since:
            range_spec = f"{since}..HEAD"
        else:
            # 默认：上一个tag到HEAD
            tags = self._get_tags()
            if len(tags) >= 2:
                range_spec = f"{tags[1]}..{tags[0]}"
            elif tags:
                range_spec = f"{tags[0]}..HEAD"

        self.commits = self._get_commits(range_spec)

        if not self.commits:
            return f"## [{version}] - {datetime.now().strftime('%Y-%m-%d')}\n\n_No changes._\n"

        # 按分类组织
        sections: Dict[str, List[Commit]] = {}
        breaking_changes: List[Commit] = []

        for commit in self.commits:
            if commit.is_breaking:
                breaking_changes.append(commit)

            section = commit.section
            if section not in sections:
                sections[section] = []
            sections[section].append(commit)

        # 生成Markdown
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines.append(f"## [{version}] - {date_str}")
        lines.append("")

        # Breaking Changes
        if breaking_changes:
            lines.append("### ⚠️ Breaking Changes")
            lines.append("")
            for c in breaking_changes:
                lines.append(f"- {c.formatted_subject} ({c.hash})")
            lines.append("")

        # 各分类
        for section_name in SECTION_ORDER:
            if section_name not in sections:
                continue

            commits = sections[section_name]
            # 如果有breaking changes，从对应分类中移除
            if breaking_changes:
                commits = [c for c in commits if not c.is_breaking]

            if not commits:
                continue

            lines.append(f"### {section_name}")
            lines.append("")
            for c in commits:
                lines.append(f"- {c.formatted_subject} ({c.hash})")
            lines.append("")

        return "\n".join(lines)

    def generate_between(self, from_tag: str, to_tag: str) -> str:
        """生成两个tag之间的changelog"""
        return self.generate(version=to_tag, since=from_tag, until=to_tag)

    def generate_since(self, commit_ref: str) -> str:
        """从指定commit生成到HEAD的changelog"""
        return self.generate(since=commit_ref)

    # ============================================================
    # 辅助功能
    # ============================================================

    def get_stats(self) -> dict:
        """获取commit统计"""
        stats = {
            "total": len(self.commits),
            "by_type": {},
            "by_section": {},
            "by_author": {},
            "breaking": 0,
        }

        for c in self.commits:
            stats["by_type"][c.type] = stats["by_type"].get(c.type, 0) + 1
            stats["by_section"][c.section] = stats["by_section"].get(c.section, 0) + 1
            stats["by_author"][c.author] = stats["by_author"].get(c.author, 0) + 1
            if c.is_breaking:
                stats["breaking"] += 1

        return stats

    def get_commits(self) -> List[dict]:
        """获取所有commit"""
        return [c.to_dict() for c in self.commits]

    def write_changelog(self, filepath: str = None, version: str = None):
        """写入CHANGELOG.md文件"""
        content = self.generate(version)
        path = filepath or os.path.join(self.repo_path, "CHANGELOG.md")

        # 如果文件存在，追加到顶部
        if os.path.exists(path):
            existing = Path(path).read_text(encoding="utf-8")
            content = content + "\n\n---\n\n" + existing

        Path(path).write_text(content, encoding="utf-8")
        return path


# ============================================================
# 完整CHANGELOG模板头部
# ============================================================

CHANGELOG_HEADER = """# Changelog

所有重要变更都会记录在这个文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/)，
版本遵循 [Semantic Versioning](https://semver.org/)。

"""


def init_changelog(repo_path: str = None):
    """初始化CHANGELOG.md"""
    path = os.path.join(repo_path or os.getcwd(), "CHANGELOG.md")
    if not os.path.exists(path):
        Path(path).write_text(CHANGELOG_HEADER, encoding="utf-8")
        print(f"✅ CHANGELOG.md 已创建")
    else:
        print("⚠️ CHANGELOG.md 已存在")
