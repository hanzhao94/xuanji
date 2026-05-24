"""
xuanji Skill系统

Skill = 教Agent怎么做某事的知识包。
最简形态就一个SKILL.md，复杂的可以带tools.py + skill.toml。

用法:
    from xuanji.skill import SkillLoader
    
    loader = SkillLoader()
    loader.scan(["./skills", "~/.xuanji/skills"])
    
    # 根据任务匹配Skill
    skill = loader.match("帮我翻译这段话")
    if skill:
        prompt = loader.inject(skill, original_prompt)
    
    # 列出所有Skill
    for s in loader.list_skills():
        print(s.name, s.description)
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillInfo:
    """Skill元数据"""
    
    __slots__ = (
        "name", "description", "trigger_keywords", "content",
        "path", "directory", "tools_required", "version",
        "author", "dependencies",
    )
    
    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.trigger_keywords: List[str] = []
        self.content: str = ""           # SKILL.md全文
        self.path: str = ""              # SKILL.md路径
        self.directory: str = ""         # Skill目录
        self.tools_required: List[str] = []
        self.version: str = "0.1.0"
        self.author: str = ""
        self.dependencies: List[str] = []
    
    def __repr__(self):
        return f"<Skill '{self.name}' keywords={self.trigger_keywords}>"
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "trigger_keywords": self.trigger_keywords,
            "directory": self.directory,
            "version": self.version,
            "tools_required": self.tools_required,
        }


class SkillLoader:
    """Skill发现、匹配、注入
    
    三步走：
    1. scan(paths) — 扫描目录找Skill
    2. match(task) — 根据任务描述匹配
    3. inject(skill, prompt) — 注入到prompt
    """
    
    def __init__(self):
        self._skills: Dict[str, SkillInfo] = {}  # name → SkillInfo
    
    # ============================================================
    # 扫描
    # ============================================================
    
    def scan(self, paths: List[str]) -> List[SkillInfo]:
        """扫描目录找SKILL.md文件
        
        支持两级目录：
          skills/
            translator/
              SKILL.md
            data-analysis/
              SKILL.md
              skill.toml
              tools.py
        
        Args:
            paths: 要扫描的目录列表
        
        Returns:
            新发现的Skill列表
        """
        found = []
        for base in paths:
            base = os.path.expanduser(base)
            base = os.path.abspath(base)
            if not os.path.isdir(base):
                continue
            
            # 直接在base下找SKILL.md
            skill_md = os.path.join(base, "SKILL.md")
            if os.path.isfile(skill_md):
                info = self._parse_skill(skill_md)
                if info and self._register(info):
                    found.append(info)
            
            # 遍历子目录
            try:
                entries = os.listdir(base)
            except OSError:
                continue
            
            for entry in sorted(entries):
                sub = os.path.join(base, entry)
                if not os.path.isdir(sub):
                    continue
                skill_md = os.path.join(sub, "SKILL.md")
                if os.path.isfile(skill_md):
                    info = self._parse_skill(skill_md)
                    if info and self._register(info):
                        found.append(info)
        
        return found
    
    def load(self, skill_dir: str) -> Optional[SkillInfo]:
        """加载单个Skill目录
        
        Args:
            skill_dir: 包含SKILL.md的目录
        
        Returns:
            SkillInfo 或 None
        """
        skill_dir = os.path.abspath(skill_dir)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            return None
        
        info = self._parse_skill(skill_md)
        if info:
            self._register(info)
        return info
    
    # ============================================================
    # 匹配
    # ============================================================
    
    def match(self, task_description: str) -> Optional[SkillInfo]:
        """根据任务描述匹配最合适的Skill
        
        匹配策略（按优先级）：
        1. 关键词完全匹配（trigger_keywords）
        2. 名称/描述模糊匹配
        3. 内容关键词匹配
        
        Args:
            task_description: 任务描述文本
        
        Returns:
            最匹配的SkillInfo，无匹配返回None
        """
        if not self._skills:
            return None
        
        task_lower = task_description.lower()
        best_skill = None
        best_score = 0
        
        for skill in self._skills.values():
            score = self._calc_match_score(skill, task_lower)
            if score > best_score:
                best_score = score
                best_skill = skill
        
        # 最低阈值：至少有一个关键词匹配
        if best_score < 1:
            return None
        
        return best_skill
    
    def match_all(self, task_description: str, limit: int = 5) -> List[SkillInfo]:
        """返回所有匹配的Skill（按分数排序）
        
        Args:
            task_description: 任务描述
            limit: 最多返回数量
        
        Returns:
            匹配的SkillInfo列表
        """
        if not self._skills:
            return []
        
        task_lower = task_description.lower()
        scored = []
        
        for skill in self._skills.values():
            score = self._calc_match_score(skill, task_lower)
            if score >= 1:
                scored.append((score, skill))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]
    
    # ============================================================
    # 注入
    # ============================================================
    
    def inject(self, skill: SkillInfo, prompt: str) -> str:
        """把Skill内容注入到prompt
        
        在prompt前面插入Skill指导内容。
        
        Args:
            skill: 要注入的Skill
            prompt: 原始prompt
        
        Returns:
            注入后的prompt
        """
        if not skill.content:
            return prompt
        
        injection = (
            f"## Active Skill: {skill.name}\n\n"
            f"{skill.content}\n\n"
            f"---\n\n"
        )
        return injection + prompt
    
    # ============================================================
    # 查询
    # ============================================================
    
    def list_skills(self) -> List[SkillInfo]:
        """列出所有已发现的Skill"""
        return list(self._skills.values())
    
    def get(self, name: str) -> Optional[SkillInfo]:
        """按名称获取Skill"""
        return self._skills.get(name)
    
    def summary(self) -> Dict:
        """概览"""
        return {
            "total": len(self._skills),
            "skills": [s.to_dict() for s in self._skills.values()],
        }
    
    # ============================================================
    # 内部方法
    # ============================================================
    
    def _register(self, info: SkillInfo) -> bool:
        """注册Skill（去重）"""
        if info.name in self._skills:
            # 已存在，跳过
            return False
        self._skills[info.name] = info
        return True
    
    def _parse_skill(self, skill_md_path: str) -> Optional[SkillInfo]:
        """解析SKILL.md + skill.toml"""
        skill_md_path = os.path.abspath(skill_md_path)
        directory = os.path.dirname(skill_md_path)
        
        info = SkillInfo()
        info.path = skill_md_path
        info.directory = directory
        
        # 读取SKILL.md
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return None
        
        info.content = content
        
        # 从SKILL.md提取元数据
        info.name = self._extract_name(content, directory)
        info.description = self._extract_description(content)
        info.trigger_keywords = self._extract_keywords(content, info.name, info.description)
        
        # 如果有skill.toml，合并元数据
        toml_path = os.path.join(directory, "skill.toml")
        if os.path.isfile(toml_path):
            self._merge_toml(info, toml_path)
        
        return info
    
    def _extract_name(self, content: str, directory: str) -> str:
        """从SKILL.md提取名称（第一个#标题或目录名）"""
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                # 去掉 # 和可能的装饰
                name = line.lstrip("# ").strip()
                # 去掉常见后缀如 "— xxx" "- xxx"
                name = re.split(r'\s*[—\-]\s*', name)[0].strip()
                if name:
                    return name
        return os.path.basename(directory)
    
    def _extract_description(self, content: str) -> str:
        """从SKILL.md提取描述（第一个非标题段落）"""
        lines = content.split("\n")
        in_paragraph = False
        desc_lines = []
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_paragraph:
                    break
                continue
            if stripped.startswith("#"):
                if in_paragraph:
                    break
                continue
            in_paragraph = True
            desc_lines.append(stripped)
        
        desc = " ".join(desc_lines)
        if len(desc) > 200:
            desc = desc[:200] + "..."
        return desc
    
    def _extract_keywords(self, content: str, name: str, description: str) -> List[str]:
        """提取触发关键词
        
        来源：
        1. SKILL.md中的"触发条件"/"触发关键词"段落
        2. 名称和描述中的关键词
        """
        keywords = []
        
        # 从"触发"相关段落提取
        trigger_section = self._extract_section(content, 
            ["触发条件", "触发关键词", "trigger", "triggers", "when to use"])
        if trigger_section:
            # 提取列表项
            for line in trigger_section.split("\n"):
                line = line.strip()
                if line.startswith(("- ", "* ", "• ")):
                    kw = line.lstrip("-*• ").strip()
                    if kw and len(kw) < 50:
                        keywords.append(kw.lower())
                elif line and not line.startswith("#") and len(line) < 50:
                    keywords.append(line.lower())
        
        # 从名称提取
        if name:
            keywords.append(name.lower())
            # 中文分词（简单按标点拆分）
            for part in re.split(r'[\s/\-_+]', name):
                if part and len(part) >= 2:
                    keywords.append(part.lower())
        
        # 去重保序
        seen = set()
        unique = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique.append(kw)
        
        return unique
    
    def _extract_section(self, content: str, headings: List[str]) -> Optional[str]:
        """提取指定标题下的内容段落"""
        lines = content.split("\n")
        capturing = False
        section_lines = []
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                heading_text = stripped.lstrip("#").strip().lower()
                if any(h.lower() in heading_text for h in headings):
                    capturing = True
                    continue
                elif capturing:
                    break  # 遇到下一个标题，结束
            
            if capturing:
                section_lines.append(line)
        
        if section_lines:
            return "\n".join(section_lines).strip()
        return None
    
    def _merge_toml(self, info: SkillInfo, toml_path: str):
        """从skill.toml合并元数据"""
        try:
            data = self._read_toml(toml_path)
        except Exception:
            return
        
        skill_section = data.get("skill", data)
        
        if "name" in skill_section:
            info.name = skill_section["name"]
        if "version" in skill_section:
            info.version = skill_section["version"]
        if "description" in skill_section:
            info.description = skill_section["description"]
        if "author" in skill_section:
            info.author = skill_section["author"]
        if "keywords" in skill_section:
            # 合并关键词
            extra = skill_section["keywords"]
            if isinstance(extra, list):
                info.trigger_keywords.extend(k.lower() for k in extra)
        
        # requires段
        requires = data.get("requires", {})
        if "tools" in requires:
            info.tools_required = requires["tools"]
        if "python" in requires:
            info.dependencies = requires["python"]
    
    def _read_toml(self, path: str) -> Dict:
        """读取TOML（复用loader的逻辑）"""
        try:
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            pass
        
        try:
            import tomli
            with open(path, "rb") as f:
                return tomli.load(f)
        except ImportError:
            pass
        
        # 降级：简单解析
        return self._simple_toml_parse(path)
    
    def _simple_toml_parse(self, path: str) -> Dict:
        """简单TOML解析"""
        config: Dict = {}
        current = config
        
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    parts = section.split(".")
                    current = config
                    for part in parts:
                        current = current.setdefault(part, {})
                elif "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    elif val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    elif val.startswith("[") and val.endswith("]"):
                        inner = val[1:-1].strip()
                        if inner:
                            val = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
                        else:
                            val = []
                    else:
                        try:
                            val = int(val)
                        except ValueError:
                            try:
                                val = float(val)
                            except ValueError:
                                pass
                    
                    current[key] = val
        
        return config
    
    def _calc_match_score(self, skill: SkillInfo, task_lower: str) -> int:
        """计算匹配分数
        
        分数规则（双向匹配）：
        - 任务词出现在skill关键词中: +10
        - 任务词出现在skill名称中: +5（核心匹配）
        - skill描述词出现在任务中: +1
        - 名称分词匹配: +3（支持 partial match，如 recycle → content-recycler）
        """
        score = 0
        
        # 1. 关键词匹配: 任务的任何词出现在skill关键词中
        for kw in skill.trigger_keywords:
            if kw in task_lower:
                score += 10
        
        # 2. 名称匹配: 任务词是否在skill名称中（双向）
        skill_name_lower = skill.name.lower()
        if skill_name_lower in task_lower:
            score += 5  # 全名匹配
        elif task_lower in skill_name_lower:
            score += 5  # 任务词是名称的子串
        else:
            # 分词匹配: 任务按空格/标点分词，任一在名称中
            task_words = re.split(r'[\s,，。！？、;；:：\'"/\\()（）\[\]【】{}.-]+', task_lower)
            for tw in task_words:
                if len(tw) >= 2 and tw in skill_name_lower:
                    score += 3
        
        # 3. 描述匹配: skill描述中的重要词出现在任务中
        if skill.description:
            desc_words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{2,}', skill.description.lower())
            for word in desc_words:
                if word in task_lower:
                    score += 1
        
        return score
