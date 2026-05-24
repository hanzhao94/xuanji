"""
xuanji Agent卡 — 标准化Agent能力描述

Agent Card是Agent的"名片"，描述其名称、版本、能力、工具、技能和端点。
支持生成、解析、验证，与A2A协议对接。

用法:
    # 创建Agent卡
    card = AgentCard(
        name="my-agent",
        version="1.0.0",
        description="我的Agent",
        capabilities=["calculator", "search"],
    )
    card.add_tool("calc", "计算器", {"expr": "string"})
    card.add_endpoint("http", "http://localhost:8080")
    
    # 序列化
    json_str = card.to_json()
    
    # 解析
    card2 = AgentCard.from_json(json_str)
    
    # 验证
    card.validate()
"""

import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


# ─── 数据结构 ──────────────────────────────────────────────

class AgentTool:
    """Agent工具描述"""
    
    def __init__(self, name: str = "", description: str = "",
                 parameters: Optional[Dict] = None,
                 returns: Optional[str] = None):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.returns = returns or ""
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AgentTool":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            parameters=data.get("parameters", {}),
            returns=data.get("returns", ""),
        )
    
    def __repr__(self):
        return f"<AgentTool {self.name}>"


class AgentSkill:
    """Agent技能描述"""
    
    def __init__(self, name: str = "", description: str = "",
                 category: str = "", level: int = 1,
                 requires: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self.category = category
        self.level = level  # 1-5
        self.requires = requires or []
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "level": self.level,
            "requires": self.requires,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AgentSkill":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            level=data.get("level", 1),
            requires=data.get("requires", []),
        )
    
    def __repr__(self):
        return f"<AgentSkill {self.name} (L{self.level})>"


class AgentEndpoint:
    """Agent端点描述"""
    
    def __init__(self, protocol: str = "http",
                 url: str = "",
                 capabilities: Optional[List[str]] = None):
        self.protocol = protocol  # http / websocket / grpc / stdio
        self.url = url
        self.capabilities = capabilities or []
    
    def to_dict(self) -> Dict:
        return {
            "protocol": self.protocol,
            "url": self.url,
            "capabilities": self.capabilities,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AgentEndpoint":
        return cls(
            protocol=data.get("protocol", "http"),
            url=data.get("url", ""),
            capabilities=data.get("capabilities", []),
        )
    
    def __repr__(self):
        return f"<AgentEndpoint {self.protocol}://{self.url}>"


# ─── Agent卡 ───────────────────────────────────────────────

class AgentCard:
    """标准化Agent能力描述
    
    包含Agent的完整信息：名称、版本、能力、工具、技能、端点。
    支持生成、解析、验证、发现。
    """
    
    # 必填字段
    REQUIRED_FIELDS = {"name", "version", "capabilities"}
    
    # 能力值正则
    _NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    _VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
    
    def __init__(self, name: str = "", version: str = "1.0.0",
                 description: str = "",
                 capabilities: Optional[List[str]] = None,
                 author: str = "", license: str = "MIT",
                 metadata: Optional[Dict] = None):
        self.name = name
        self.version = version
        self.description = description
        self.capabilities = capabilities or []
        self.author = author
        self.license = license
        self.metadata = metadata or {}
        
        # 工具列表
        self.tools: List[AgentTool] = []
        # 技能列表
        self.skills: List[AgentSkill] = []
        # 端点列表
        self.endpoints: List[AgentEndpoint] = []
        
        # 元信息
        self.created_at = time.time()
        self.updated_at = time.time()
        self.card_id = str(uuid.uuid4())
    
    # ─── 工具管理 ──────────────────────────────────────────
    
    def add_tool(self, name: str, description: str,
                 parameters: Optional[Dict] = None,
                 returns: Optional[str] = None) -> "AgentCard":
        """添加工具（链式调用）"""
        self.tools.append(AgentTool(name, description, parameters, returns))
        self.updated_at = time.time()
        return self
    
    def remove_tool(self, name: str) -> bool:
        """移除工具"""
        for i, t in enumerate(self.tools):
            if t.name == name:
                self.tools.pop(i)
                self.updated_at = time.time()
                return True
        return False
    
    def get_tool(self, name: str) -> Optional[AgentTool]:
        """获取工具"""
        for t in self.tools:
            if t.name == name:
                return t
        return None
    
    # ─── 技能管理 ──────────────────────────────────────────
    
    def add_skill(self, name: str, description: str,
                  category: str = "", level: int = 1,
                  requires: Optional[List[str]] = None) -> "AgentCard":
        """添加技能（链式调用）"""
        self.skills.append(AgentSkill(name, description, category, level, requires))
        self.updated_at = time.time()
        return self
    
    def get_skill(self, name: str) -> Optional[AgentSkill]:
        """获取技能"""
        for s in self.skills:
            if s.name == name:
                return s
        return None
    
    # ─── 端点管理 ──────────────────────────────────────────
    
    def add_endpoint(self, protocol: str, url: str,
                     capabilities: Optional[List[str]] = None) -> "AgentCard":
        """添加端点（链式调用）"""
        self.endpoints.append(AgentEndpoint(protocol, url, capabilities))
        self.updated_at = time.time()
        return self
    
    def get_primary_endpoint(self) -> Optional[AgentEndpoint]:
        """获取主端点"""
        return self.endpoints[0] if self.endpoints else None
    
    # ─── 序列化 ────────────────────────────────────────────
    
    def to_dict(self) -> Dict:
        """转为字典"""
        return {
            "card_id": self.card_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "author": self.author,
            "license": self.license,
            "tools": [t.to_dict() for t in self.tools],
            "skills": [s.to_dict() for s in self.skills],
            "endpoints": [e.to_dict() for e in self.endpoints],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """转为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AgentCard":
        """从字典创建"""
        card = cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            author=data.get("author", ""),
            license=data.get("license", "MIT"),
            metadata=data.get("metadata", {}),
        )
        card.card_id = data.get("card_id", card.card_id)
        card.created_at = data.get("created_at", card.created_at)
        card.updated_at = data.get("updated_at", card.updated_at)
        
        for t in data.get("tools", []):
            card.tools.append(AgentTool.from_dict(t))
        for s in data.get("skills", []):
            card.skills.append(AgentSkill.from_dict(s))
        for e in data.get("endpoints", []):
            card.endpoints.append(AgentEndpoint.from_dict(e))
        
        return card
    
    @classmethod
    def from_json(cls, json_str: str) -> "AgentCard":
        """从JSON字符串创建"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    @classmethod
    def from_file(cls, path: str) -> "AgentCard":
        """从文件加载"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())
    
    def save(self, path: str) -> None:
        """保存到文件"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
    
    # ─── 验证 ──────────────────────────────────────────────
    
    def validate(self) -> Tuple[bool, List[str]]:
        """验证Agent卡
        
        Returns:
            (是否有效, 错误列表)
        """
        errors = []
        data = self.to_dict()
        
        # 必填字段
        for field in self.REQUIRED_FIELDS:
            if field == "capabilities":
                if not data.get("capabilities"):
                    errors.append(f"缺少必填字段: {field}")
            elif not data.get(field):
                errors.append(f"缺少必填字段: {field}")
        
        # 名称格式
        if self.name and not self._NAME_RE.match(self.name):
            errors.append(f"名称格式无效: {self.name}（只能包含字母、数字、-、_）")
        
        # 版本号格式
        if self.version and not self._VERSION_RE.match(self.version):
            errors.append(f"版本号格式无效: {self.version}（需要 x.y.z 格式）")
        
        # 工具名去重
        tool_names = [t.name for t in self.tools]
        if len(tool_names) != len(set(tool_names)):
            errors.append("工具名重复")
        
        # 端点URL
        for ep in self.endpoints:
            if ep.url and not ep.url.startswith(("http://", "https://", "ws://", "wss://")):
                errors.append(f"端点URL格式无效: {ep.url}")
        
        return (len(errors) == 0, errors)
    
    # ─── 能力匹配 ──────────────────────────────────────────
    
    def has_capability(self, capability: str) -> bool:
        """检查是否有指定能力"""
        return capability in self.capabilities
    
    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定工具"""
        return any(t.name == tool_name for t in self.tools)
    
    def has_skill(self, skill_name: str) -> bool:
        """检查是否有指定技能"""
        return any(s.name == skill_name for s in self.skills)
    
    def matches(self, requirements: List[str]) -> List[str]:
        """匹配需求列表，返回缺失的能力
        
        Args:
            requirements: 需要的能力列表
        
        Returns:
            缺失的能力
        """
        missing = []
        for req in requirements:
            if req not in self.capabilities:
                missing.append(req)
        return missing
    
    def compatibility_score(self, other: "AgentCard") -> float:
        """计算与另一个Agent的兼容性分数
        
        Returns:
            0.0-1.0的兼容性分数
        """
        if not self.capabilities or not other.capabilities:
            return 0.0
        
        common = set(self.capabilities) & set(other.capabilities)
        union = set(self.capabilities) | set(other.capabilities)
        
        return len(common) / len(union) if union else 0.0
    
    # ─── 发现 ──────────────────────────────────────────────
    
    @staticmethod
    def fetch_from_url(url: str, timeout: float = 10.0) -> Optional["AgentCard"]:
        """从URL获取Agent卡
        
        Args:
            url: Agent卡URL
            timeout: 超时秒数
        
        Returns:
            AgentCard或None
        """
        try:
            req = Request(url, headers={"Accept": "application/json"})
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read().decode("utf-8"))
            return AgentCard.from_dict(data)
        except Exception as e:
            logger.warning(f"获取Agent卡失败 ({url}): {e}")
            return None
    
    def __repr__(self):
        return f"<AgentCard {self.name} v{self.version} ({len(self.capabilities)} capabilities)>"
    
    def __str__(self):
        lines = [
            f"📋 Agent卡: {self.name} v{self.version}",
            f"   描述: {self.description}",
            f"   能力: {', '.join(self.capabilities)}",
            f"   工具: {len(self.tools)}",
            f"   技能: {len(self.skills)}",
            f"   端点: {len(self.endpoints)}",
        ]
        if self.author:
            lines.append(f"   作者: {self.author}")
        return "\n".join(lines)


# ─── Agent发现器 ───────────────────────────────────────────

class AgentDiscovery:
    """Agent发现器
    
    支持本地和网络发现Agent卡。
    """
    
    def __init__(self):
        self._cards: Dict[str, AgentCard] = {}
        self._registry_urls: List[str] = []
    
    def register_card(self, card: AgentCard) -> None:
        """注册Agent卡"""
        key = f"{card.name}:{card.version}"
        self._cards[key] = card
        logger.debug(f"注册Agent卡: {key}")
    
    def unregister(self, name: str, version: str = "") -> bool:
        """注销Agent卡"""
        key = f"{name}:{version}" if version else name
        if key in self._cards:
            del self._cards[key]
            return True
        # 尝试只按名称匹配
        for k in list(self._cards.keys()):
            if k.startswith(f"{name}:"):
                del self._cards[k]
                return True
        return False
    
    def search(self, capability: Optional[str] = None,
               name: Optional[str] = None,
               min_version: Optional[str] = None) -> List[AgentCard]:
        """搜索Agent卡
        
        Args:
            capability: 按能力过滤
            name: 按名称过滤
            min_version: 最低版本
        
        Returns:
            匹配的Agent卡列表
        """
        results = []
        for card in self._cards.values():
            if capability and capability not in card.capabilities:
                continue
            if name and name.lower() not in card.name.lower():
                continue
            if min_version and card.version < min_version:
                continue
            results.append(card)
        return results
    
    def get(self, name: str, version: str = "") -> Optional[AgentCard]:
        """获取Agent卡"""
        if version:
            return self._cards.get(f"{name}:{version}")
        # 返回最新版本
        best = None
        for key, card in self._cards.items():
            if key.startswith(f"{name}:"):
                if best is None or card.version > best.version:
                    best = card
        return best
    
    def add_registry_url(self, url: str) -> None:
        """添加远程注册中心URL"""
        self._registry_urls.append(url)
    
    def discover_remote(self, timeout: float = 10.0) -> List[AgentCard]:
        """从远程注册中心发现Agent
        
        Returns:
            发现的Agent卡列表
        """
        found = []
        for url in self._registry_urls:
            try:
                req = Request(url, headers={"Accept": "application/json"})
                resp = urlopen(req, timeout=timeout)
                data = json.loads(resp.read().decode("utf-8"))
                
                # 支持单个或列表
                cards_data = data if isinstance(data, list) else [data]
                for cd in cards_data:
                    card = AgentCard.from_dict(cd)
                    self.register_card(card)
                    found.append(card)
            except Exception as e:
                logger.warning(f"远程发现失败 ({url}): {e}")
        
        return found
    
    def list_all(self) -> List[AgentCard]:
        """列出所有Agent卡"""
        return list(self._cards.values())
    
    def stats(self) -> Dict:
        """统计信息"""
        caps = set()
        for card in self._cards.values():
            caps.update(card.capabilities)
        
        return {
            "total_agents": len(self._cards),
            "total_capabilities": len(caps),
            "capabilities": list(caps),
            "registry_urls": len(self._registry_urls),
        }
    
    def __repr__(self):
        return f"<AgentDiscovery {len(self._cards)} agents>"
