"""xuanji 专家人格库

提供搜索、推荐、团队组合等功能。
"""

import json
import os
import re
from typing import List, Optional, Dict
from xuanji.persona_data import (
    ExpertPersona,
    BUILTIN_PERSONAS,
    TEAM_TEMPLATES,
)

class PersonaLibrary:
    """专家人格管理库
    
    - 内置20个人格
    - 支持从JSON文件加载更多人格
    - 支持自定义人格（JSONL持久化）
    - 与TeamEngine角色自动匹配
    """

    def __init__(self, extra_json_path: str = "",
                 custom_jsonl_path: str = ""):
        """
        Args:
            extra_json_path: 额外人格JSON文件路径（如imported_personas.json）
            custom_jsonl_path: 自定义人格JSONL文件路径
        """
        self._builtin = {p.id: p for p in BUILTIN_PERSONAS}
        self._extra: Dict[str, ExpertPersona] = {}
        self._custom: Dict[str, ExpertPersona] = {}
        
        self._extra_path = extra_json_path
        self._custom_path = custom_jsonl_path
        
        if extra_json_path:
            self._load_extra(extra_json_path)
        if custom_jsonl_path:
            self._load_custom(custom_jsonl_path)

    # ── 加载 ──────────────────────────────────────────────────

    def _load_extra(self, path: str):
        """从JSON文件加载额外人格"""
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                p = ExpertPersona.from_dict(item)
                if p.id:
                    self._extra[p.id] = p
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def _load_custom(self, path: str):
        """从JSONL文件加载自定义人格"""
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    p = ExpertPersona.from_dict(data)
                    if p.id:
                        self._custom[p.id] = p
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def load_from_file(self, path: str) -> int:
        """从JSON文件加载人格，返回加载数量"""
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = 0
            for item in data:
                p = ExpertPersona.from_dict(item)
                if p.id and p.id not in self._extra:
                    self._extra[p.id] = p
                    count += 1
            return count
        except (json.JSONDecodeError, TypeError):
            return 0

    # ── 核心查询 ──────────────────────────────────────────────

    def get(self, persona_id: str) -> Optional[ExpertPersona]:
        """按ID获取人格（优先自定义→额外→内置）"""
        return (self._custom.get(persona_id) or
                self._extra.get(persona_id) or
                self._builtin.get(persona_id))

    def list_personas(self, domain: str = "") -> List[ExpertPersona]:
        """列出所有人格，可按领域过滤"""
        all_personas = {**self._builtin, **self._extra, **self._custom}
        if domain:
            return [p for p in all_personas.values() if p.domain == domain]
        return list(all_personas.values())

    def list_domains(self) -> List[str]:
        """列出所有领域"""
        return sorted(set(p.domain for p in self.list_personas()))

    def search(self, query: str) -> List[ExpertPersona]:
        """关键词搜索人格"""
        query_lower = query.lower()
        results = []
        for p in self.list_personas():
            searchable = " ".join([
                p.id, p.name, p.name_cn, p.domain, p.role,
                p.personality, " ".join(p.expertise),
            ]).lower()
            if query_lower in searchable:
                results.append(p)
        return results

    def get_system_prompt(self, persona_id: str) -> Optional[str]:
        """获取指定人格的system_prompt"""
        persona = self.get(persona_id)
        return persona.system_prompt if persona else None

    def suggest(self, task_description: str) -> List[ExpertPersona]:
        """根据任务描述推荐适合的专家人格
        
        基于关键词匹配（支持中文），返回按匹配度排序的列表。
        """
        task_lower = task_description.lower()
        scored: List[tuple] = []

        for p in self.list_personas():
            score = 0.0
            # expertise权重最高
            for exp in p.expertise:
                exp_l = exp.lower()
                if exp_l in task_lower or task_lower in exp_l:
                    score += 3
                for ch in task_lower:
                    if ch.strip() and ch in exp_l:
                        score += 0.5
            # role匹配
            role_l = p.role.lower()
            for ch in task_lower:
                if ch.strip() and ch in role_l:
                    score += 1
            # name_cn匹配
            name_cn_l = p.name_cn.lower()
            if name_cn_l in task_lower or task_lower in name_cn_l:
                score += 5
            for ch in task_lower:
                if ch.strip() and ch in name_cn_l:
                    score += 1
            # id匹配
            for part in p.id.split("-"):
                if len(part) > 2 and part in task_lower:
                    score += 2
            # domain
            if p.domain in task_lower:
                score += 1

            if score > 0:
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    # ── 自定义人格管理 ────────────────────────────────────────

    def add_custom(self, persona: ExpertPersona):
        """添加自定义人格（持久化到JSONL）"""
        self._custom[persona.id] = persona
        if self._custom_path:
            self._save_custom()

    def _save_custom(self):
        """保存所有自定义人格到JSONL"""
        if not self._custom_path:
            return
        parent = os.path.dirname(self._custom_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._custom_path, "w", encoding="utf-8") as f:
            for persona in self._custom.values():
                f.write(json.dumps(persona.to_dict(), ensure_ascii=False) + "\n")

    # ── 团队 & 导出 ──────────────────────────────────────────

    def team_composition(self, project_type: str) -> List[ExpertPersona]:
        """根据项目类型返回推荐团队组合
        
        Args:
            project_type: game / web_app / novel / anime / ai_system / full_stack
        """
        ids = TEAM_TEMPLATES.get(project_type, [])
        team = []
        for pid in ids:
            persona = self.get(pid)
            if persona:
                team.append(persona)
        return team

    def match_team_role(self, role: str) -> Optional[ExpertPersona]:
        """将TeamEngine角色匹配到专家人格
        
        Args:
            role: team.py中的Role值（如 "developer", "tester", "pm"）
        
        Returns:
            匹配的ExpertPersona，或None
        """
        persona_id = ROLE_PERSONA_MAP.get(role.lower())
        if persona_id:
            return self.get(persona_id)
        return None

    def export_for_openclaw(self, persona_id: str) -> Optional[dict]:
        """导出为OpenClaw子代理可用的配置"""
        persona = self.get(persona_id)
        if not persona:
            return None
        return {
            "persona_id": persona.id,
            "name": f"{persona.name_cn} ({persona.name})",
            "system_prompt": persona.system_prompt,
            "tools_needed": persona.tools_needed,
            "domain": persona.domain,
            "deliverables": persona.deliverables,
        }

    # ── 统计 ──────────────────────────────────────────────────

    def stats(self) -> dict:
        """人格库统计信息"""
        all_personas = self.list_personas()
        domains: Dict[str, int] = {}
        for p in all_personas:
            domains[p.domain] = domains.get(p.domain, 0) + 1
        return {
            "total": len(all_personas),
            "builtin": len(self._builtin),
            "extra": len(self._extra),
            "custom": len(self._custom),
            "domains": domains,
            "team_templates": list(TEAM_TEMPLATES.keys()),
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (f"PersonaLibrary(total={s['total']}, builtin={s['builtin']}, "
                f"extra={s['extra']}, custom={s['custom']})")

