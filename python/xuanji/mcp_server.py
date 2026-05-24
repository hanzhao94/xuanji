"""
xuanji MCP Server生成器

让用户5行代码写一个MCP Server。

用法:
    from xuanji.mcp_server import MCPServer
    
    server = MCPServer("我的工具集")
    
    @server.tool("查天气", description="查询城市天气")
    def get_weather(city: str) -> str:
        return f"{city}今天晴，25°C"
    
    @server.tool("计算器")
    def calc(expression: str) -> str:
        return _safe_math_eval(expression)
    
    if __name__ == "__main__":
        server.run()

这个Server可以被任何MCP客户端接入（xuanji/Claude Desktop/Cursor等）。
"""

import ast
import inspect
import json
import operator
import sys
from typing import Any, Callable, Dict, List, Optional, get_type_hints


def _safe_math_eval(expression: str) -> str:
    """安全的数学表达式求值，替代eval()。
    
    只允许基本数学运算：加减乘除、幂、取模、括号、负号。
    拒绝所有函数调用、变量访问、属性访问。
    """
    allowed_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.Mod: operator.mod, ast.UAdd: operator.pos,
        ast.FloorDiv: operator.floordiv,
    }
    
    def _eval_node(node):
        if isinstance(node, (ast.Num, ast.Constant)):
            val = node.n if hasattr(node, 'n') else node.value
            if not isinstance(val, (int, float)):
                raise ValueError(f"不允许的值类型: {type(val).__name__}")
            return val
        if isinstance(node, ast.BinOp):
            op_func = allowed_ops.get(type(node.op))
            if not op_func:
                raise ValueError(f"不允许的运算符: {type(node.op).__name__}")
            return op_func(_eval_node(node.left), _eval_node(node.right))
        if isinstance(node, ast.UnaryOp):
            op_func = allowed_ops.get(type(node.op))
            if not op_func:
                raise ValueError(f"不允许的一元运算: {type(node.op).__name__}")
            return op_func(_eval_node(node.operand))
        raise ValueError(f"不允许的表达式: {ast.dump(node)}")
    
    tree = ast.parse(expression.strip(), mode='eval')
    result = _eval_node(tree.body)
    if isinstance(result, float) and result == int(result):
        return str(int(result))
    return str(result)


# Python类型到JSON Schema类型映射
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class _ToolDef:
    """工具定义（内部用）"""
    __slots__ = ("name", "description", "func", "input_schema")
    
    def __init__(self, name: str, description: str, func: Callable, input_schema: Dict):
        self.name = name
        self.description = description
        self.func = func
        self.input_schema = input_schema


class MCPServer:
    """MCP Server — 5行代码创建工具服务
    
    遵循MCP协议（JSON-RPC 2.0 over stdio）。
    支持 initialize / tools/list / tools/call。
    """
    
    def __init__(self, name: str = "xuanji-mcp-server", version: str = "0.1.0"):
        self.name = name
        self.version = version
        self._tools: Dict[str, _ToolDef] = {}
    
    # ============================================================
    # 工具注册
    # ============================================================
    
    def tool(self, name: str = "", description: str = ""):
        """装饰器 — 注册一个MCP工具
        
        用法:
            @server.tool("工具名", description="工具描述")
            def my_tool(param1: str, param2: int = 0) -> str:
                '''也可以用docstring作为描述'''
                return "result"
        
        参数类型从类型注解自动推断。
        描述优先用description参数，其次用docstring。
        """
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or (func.__doc__ or "").strip()
            
            # 自动生成input_schema
            schema = self._func_to_schema(func)
            
            self._tools[tool_name] = _ToolDef(
                name=tool_name,
                description=tool_desc,
                func=func,
                input_schema=schema,
            )
            return func
        
        return decorator
    
    def add_tool(self, name: str, func: Callable, description: str = ""):
        """非装饰器方式注册工具"""
        tool_desc = description or (func.__doc__ or "").strip()
        schema = self._func_to_schema(func)
        self._tools[name] = _ToolDef(
            name=name,
            description=tool_desc,
            func=func,
            input_schema=schema,
        )
    
    # ============================================================
    # 运行
    # ============================================================
    
    def run(self):
        """启动stdio模式监听
        
        从stdin读取JSON-RPC请求，向stdout写入响应。
        按MCP协议处理 initialize / tools/list / tools/call。
        """
        # 用stderr输出日志（stdout留给JSON-RPC）
        sys.stderr.write(f"[MCP] {self.name} v{self.version} starting (stdio mode)\n")
        sys.stderr.write(f"[MCP] {len(self._tools)} tools registered\n")
        sys.stderr.flush()
        
        while True:
            try:
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                break
            
            if not line:
                break  # EOF
            
            line = line.strip()
            if not line:
                continue
            
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                self._write_error(None, -32700, f"Parse error: {e}")
                continue
            
            self._handle_request(request)
        
        sys.stderr.write(f"[MCP] {self.name} stopped\n")
        sys.stderr.flush()
    
    # ============================================================
    # 请求处理
    # ============================================================
    
    def _handle_request(self, request: Dict):
        """处理单个JSON-RPC请求"""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})
        
        # 通知（无id）：不需要响应
        if req_id is None:
            return
        
        if method == "initialize":
            self._handle_initialize(req_id, params)
        elif method == "tools/list":
            self._handle_tools_list(req_id, params)
        elif method == "tools/call":
            self._handle_tools_call(req_id, params)
        elif method == "resources/list":
            self._write_result(req_id, {"resources": []})
        elif method == "prompts/list":
            self._write_result(req_id, {"prompts": []})
        elif method == "ping":
            self._write_result(req_id, {})
        else:
            self._write_error(req_id, -32601, f"Method not found: {method}")
    
    def _handle_initialize(self, req_id, params: Dict):
        """处理初始化请求"""
        self._write_result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": self.name,
                "version": self.version,
            },
        })
    
    def _handle_tools_list(self, req_id, params: Dict):
        """处理工具列表请求"""
        tools = []
        for tool_def in self._tools.values():
            tools.append({
                "name": tool_def.name,
                "description": tool_def.description,
                "inputSchema": tool_def.input_schema,
            })
        
        self._write_result(req_id, {"tools": tools})
    
    def _handle_tools_call(self, req_id, params: Dict):
        """处理工具调用请求"""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        
        tool_def = self._tools.get(tool_name)
        if not tool_def:
            self._write_error(req_id, -32602, f"Tool not found: {tool_name}")
            return
        
        try:
            result = tool_def.func(**arguments)
            
            # 统一转为content数组
            if isinstance(result, str):
                content = [{"type": "text", "text": result}]
            elif isinstance(result, dict):
                content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            elif isinstance(result, list):
                content = result  # 假设已经是content格式
            else:
                content = [{"type": "text", "text": str(result)}]
            
            self._write_result(req_id, {"content": content})
            
        except Exception as e:
            self._write_result(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
    
    # ============================================================
    # JSON-RPC 输出
    # ============================================================
    
    def _write_result(self, req_id, result: Any):
        """写入成功响应"""
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }
        self._write(response)
    
    def _write_error(self, req_id, code: int, message: str):
        """写入错误响应"""
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
        self._write(response)
    
    def _write(self, data: Dict):
        """写入一行JSON到stdout"""
        line = json.dumps(data, ensure_ascii=False) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()
    
    # ============================================================
    # Schema生成
    # ============================================================
    
    def _func_to_schema(self, func: Callable) -> Dict:
        """从函数签名自动生成JSON Schema
        
        使用类型注解推断参数类型。
        有默认值的参数不在required中。
        """
        sig = inspect.signature(func)
        
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        
        properties = {}
        required = []
        
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            
            # 类型
            param_type = hints.get(param_name, str)
            json_type = _TYPE_MAP.get(param_type, "string")
            
            prop: Dict[str, Any] = {"type": json_type}
            
            # 默认值
            if param.default is not inspect.Parameter.empty:
                prop["default"] = param.default
            else:
                required.append(param_name)
            
            properties[param_name] = prop
        
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        
        return schema
