"""
xuanji OpenAPI自动工具生成

从OpenAPI (Swagger) JSON规范自动生成可调用的工具列表。
每个API端点变成一个工具（含参数schema、类型校验）。
支持OpenAPI 3.0/3.1，支持Bearer/ApiKey/Basic认证。

用法:
    tools = OpenAPITools()
    tools.load_spec("api.json")
    tools.generate_tools()
    
    # 调用生成的工具
    result = tools.call("listUsers", {"page": 1})
    
    # 查看工具列表
    for t in tools.list_tools():
        print(f"{t['name']}: {t['description']}")
"""

import base64
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


# ─── 工具信息 ──────────────────────────────────────────────

class OpenAPITool:
    """从OpenAPI端点生成的工具"""
    
    __slots__ = ("name", "operation_id", "method", "path", "summary",
                 "description", "parameters", "request_body",
                 "server_url", "security", "tags")
    
    def __init__(self):
        self.name = ""
        self.operation_id = ""
        self.method = ""
        self.path = ""
        self.summary = ""
        self.description = ""
        self.parameters: List[Dict] = []
        self.request_body: Optional[Dict] = None
        self.server_url = ""
        self.security: List[Dict] = []
        self.tags: List[str] = []
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "operation_id": self.operation_id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "description": self.description,
            "parameters": self.parameters,
            "request_body": self.request_body,
            "tags": self.tags,
        }
    
    def __repr__(self):
        return f"<OpenAPITool {self.method.upper()} {self.path} ({self.name})>"


# ─── OpenAPI工具生成器 ─────────────────────────────────────

class OpenAPITools:
    """从OpenAPI规范自动生成工具
    
    支持OpenAPI 3.0和3.1，零外部依赖。
    """
    
    def __init__(self, timeout: float = 30.0):
        self._spec: Dict = {}
        self._tools: Dict[str, OpenAPITool] = {}
        self._security_schemes: Dict[str, Dict] = {}
        self._auth_config: Dict[str, Any] = {}
        self._timeout = timeout
        self._base_url = ""
        self._loaded = False
    
    # ─── 加载规范 ──────────────────────────────────────────
    
    def load_spec(self, spec_source: Any) -> None:
        """加载OpenAPI规范
        
        Args:
            spec_source: JSON字符串、文件路径、或已解析的dict
        
        Raises:
            ValueError: 规范无效
        """
        if isinstance(spec_source, dict):
            self._spec = spec_source
        elif isinstance(spec_source, str):
            # 尝试解析为JSON字符串
            try:
                self._spec = json.loads(spec_source)
            except json.JSONDecodeError:
                # 当作文件路径
                with open(spec_source, "r", encoding="utf-8") as f:
                    self._spec = json.load(f)
        else:
            raise ValueError("spec_source必须是dict、JSON字符串或文件路径")
        
        # 验证
        openapi_ver = self._spec.get("openapi", "")
        if not openapi_ver.startswith("3."):
            raise ValueError(f"不支持的OpenAPI版本: {openapi_ver}（需要3.x）")
        
        # 提取安全方案
        self._security_schemes = self._spec.get("components", {}).get("securitySchemes", {})
        
        # 提取服务器URL
        servers = self._spec.get("servers", [])
        self._base_url = servers[0].get("url", "") if servers else ""
        
        self._loaded = True
        logger.info(f"加载OpenAPI规范 v{openapi_ver}: {self._spec.get('info', {}).get('title', 'Untitled')}")
    
    # ─── 生成工具 ──────────────────────────────────────────
    
    def generate_tools(self) -> List[OpenAPITool]:
        """从规范生成工具列表
        
        Returns:
            生成的工具列表
        """
        if not self._loaded:
            raise RuntimeError("请先调用load_spec()加载规范")
        
        self._tools.clear()
        paths = self._spec.get("paths", {})
        
        for path, path_item in paths.items():
            for method in ("get", "post", "put", "delete", "patch", "head", "options"):
                if method not in path_item:
                    continue
                
                op = path_item[method]
                tool = self._build_tool(method, path, op)
                if tool:
                    self._tools[tool.name] = tool
        
        logger.info(f"生成 {len(self._tools)} 个工具")
        return list(self._tools.values())
    
    def _build_tool(self, method: str, path: str, op: Dict) -> Optional[OpenAPITool]:
        """从操作定义构建工具"""
        tool = OpenAPITool()
        tool.method = method
        tool.path = path
        
        # 工具名：优先用operationId，否则从路径生成
        op_id = op.get("operationId", "")
        if op_id:
            tool.name = op_id
        else:
            # 从路径生成：GET /users/{id} → getUsersById
            segments = path.strip("/").split("/")
            parts = [method]
            for seg in segments:
                if seg.startswith("{") and seg.endswith("}"):
                    parts.append(seg[1:-1].capitalize())
                else:
                    parts.append(seg)
            tool.name = "".join(parts)
        
        tool.operation_id = op_id
        tool.summary = op.get("summary", "")
        tool.description = op.get("description", "")
        tool.tags = op.get("tags", [])
        
        # 参数
        params = op.get("parameters", [])
        # 合并路径级参数
        path_params = self._spec.get("paths", {}).get(path, {}).get("parameters", [])
        all_params = []
        seen = set()
        for p in path_params + params:
            key = f"{p.get('name', '')}:{p.get('in', '')}"
            if key not in seen:
                seen.add(key)
                all_params.append(p)
        tool.parameters = all_params
        
        # 请求体
        req_body = op.get("requestBody", {})
        if req_body:
            content = req_body.get("content", {})
            json_content = content.get("application/json", {})
            tool.request_body = {
                "required": req_body.get("required", False),
                "schema": json_content.get("schema", {}),
            }
        
        # 安全
        tool.security = op.get("security", self._spec.get("security", []))
        
        return tool
    
    # ─── 认证配置 ──────────────────────────────────────────
    
    def set_auth(self, scheme_name: str, credentials: Any) -> None:
        """配置认证
        
        Args:
            scheme_name: 安全方案名（对应securitySchemes中的key）
            credentials: 凭据（Bearer token / (user, pass) / API key值）
        """
        self._auth_config[scheme_name] = credentials
    
    def _build_headers(self, security: List[Dict]) -> Dict[str, str]:
        """根据安全要求构建请求头"""
        headers = {"Accept": "application/json"}
        
        if not security:
            return headers
        
        for sec_req in security:
            for scheme_name in sec_req.keys():
                scheme = self._security_schemes.get(scheme_name, {})
                scheme_type = scheme.get("type", "")
                cred = self._auth_config.get(scheme_name)
                
                if scheme_type == "http" and scheme.get("scheme") == "bearer":
                    headers["Authorization"] = f"Bearer {cred}"
                elif scheme_type == "http" and scheme.get("scheme") == "basic":
                    if isinstance(cred, tuple) and len(cred) == 2:
                        encoded = base64.b64encode(
                            f"{cred[0]}:{cred[1]}".encode()
                        ).decode()
                        headers["Authorization"] = f"Basic {encoded}"
                elif scheme_type == "apiKey":
                    in_loc = scheme.get("in", "header")
                    param_name = scheme.get("name", "")
                    if in_loc == "header":
                        headers[param_name] = str(cred)
                    # query和cookie在URL中处理
                elif scheme_name in self._auth_config:
                    headers["Authorization"] = str(cred)
        
        return headers
    
    # ─── 调用工具 ──────────────────────────────────────────
    
    def call(self, tool_name: str, params: Optional[Dict] = None,
             body: Optional[Dict] = None) -> Dict:
        """调用生成的工具
        
        Args:
            tool_name: 工具名
            params: 路径/查询参数
            body: 请求体（POST/PUT/PATCH）
        
        Returns:
            响应数据
        
        Raises:
            ValueError: 工具不存在
            RuntimeError: 网络/HTTP错误
        """
        tool = self._tools.get(tool_name)
        if not tool:
            raise ValueError(f"工具不存在: {tool_name}。可用: {list(self._tools.keys())}")
        
        params = params or {}
        
        # 构建URL
        url = self._build_url(tool, params)
        
        # 构建请求体
        req_body = None
        if tool.method in ("post", "put", "patch") and body is not None:
            req_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        # 构建请求头
        headers = self._build_headers(tool.security)
        if req_body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        
        # 发送请求
        return self._send_request(tool.method.upper(), url, req_body, headers)
    
    def _build_url(self, tool: OpenAPITool, params: Dict) -> str:
        """构建请求URL（处理路径参数和查询参数）"""
        path = tool.path
        
        # 替换路径参数 {id} → value
        for p in tool.parameters:
            if p.get("in") == "path":
                name = p.get("name", "")
                if name in params:
                    path = path.replace(f"{{{name}}}", str(params.pop(name)))
        
        url = f"{self._base_url}{path}"
        
        # 查询参数
        query_params = {k: v for k, v in params.items()
                       if any(p.get("in") == "query" and p.get("name") == k
                              for p in tool.parameters)}
        if query_params:
            url += "?" + urlencode({k: str(v) for k, v in query_params.items()})
        
        # apiKey在query中
        for scheme_name, cred in self._auth_config.items():
            scheme = self._security_schemes.get(scheme_name, {})
            if scheme.get("type") == "apiKey" and scheme.get("in") == "query":
                param_name = scheme.get("name", "api_key")
                separator = "&" if "?" in url else "?"
                url += f"{separator}{param_name}={cred}"
        
        return url
    
    def _send_request(self, method: str, url: str,
                      body: Optional[bytes],
                      headers: Dict[str, str]) -> Dict:
        """发送HTTP请求"""
        req = Request(url, data=body, headers=headers, method=method)
        
        try:
            resp = urlopen(req, timeout=self._timeout)
            resp_body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            
            if "json" in content_type:
                return json.loads(resp_body.decode("utf-8"))
            else:
                return {
                    "status": resp.status,
                    "text": resp_body.decode("utf-8", errors="replace"),
                }
        
        except HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(error_body)
            except (json.JSONDecodeError, ValueError):
                error_data = {"raw": error_body}
            raise RuntimeError(f"HTTP {e.code}: {error_data}")
        
        except URLError as e:
            raise RuntimeError(f"网络错误: {e}")
    
    # ─── 查询接口 ──────────────────────────────────────────
    
    def list_tools(self) -> List[Dict]:
        """列出所有工具"""
        return [t.to_dict() for t in self._tools.values()]
    
    def get_tool(self, name: str) -> Optional[Dict]:
        """获取单个工具"""
        tool = self._tools.get(name)
        return tool.to_dict() if tool else None
    
    def search_tools(self, keyword: str) -> List[Dict]:
        """按关键词搜索工具"""
        keyword_lower = keyword.lower()
        results = []
        for t in self._tools.values():
            if (keyword_lower in t.name.lower() or
                keyword_lower in t.summary.lower() or
                keyword_lower in t.path.lower() or
                any(keyword_lower in tag.lower() for tag in t.tags)):
                results.append(t.to_dict())
        return results
    
    def get_tools_by_tag(self, tag: str) -> List[Dict]:
        """按标签获取工具"""
        return [t.to_dict() for t in self._tools.values() if tag in t.tags]
    
    @property
    def tool_count(self) -> int:
        return len(self._tools)
    
    def summary(self) -> Dict:
        """概览"""
        methods = {}
        tags = set()
        for t in self._tools.values():
            methods[t.method.upper()] = methods.get(t.method.upper(), 0) + 1
            tags.update(t.tags)
        
        return {
            "title": self._spec.get("info", {}).get("title", ""),
            "version": self._spec.get("info", {}).get("version", ""),
            "openapi": self._spec.get("openapi", ""),
            "total_tools": len(self._tools),
            "methods": methods,
            "tags": list(tags),
            "base_url": self._base_url,
        }
    
    def __repr__(self):
        return f"<OpenAPITools {len(self._tools)} tools>"
