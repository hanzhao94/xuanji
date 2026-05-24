"""
xuanji 插件认证机制

社区插件安全审查 + 性能测试 + 认证管理。

用法 (CLI):
  xuanji plugin submit <路径>        提交插件
  xuanji plugin scan <路径>          安全扫描
  xuanji plugin bench <路径>         性能测试
  xuanji plugin certify <名称>       认证插件
  xuanji plugin list                 列出认证插件
  xuanji plugin info <名称>          查看插件详情
  xuanji plugin revoke <名称>        撤销认证

用法 (API):
  from xuanji.plugin_registry import PluginRegistry
  reg = PluginRegistry()
  result = reg.submit("/path/to/plugin")
  reg.certify("my-plugin")
"""

import ast
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# 安全规则
# ============================================================

DANGEROUS_PATTERNS = [
    # 危险模块导入
    (r'import\s+os\s*$', "直接导入os模块（可能执行系统命令）"),
    (r'from\s+os\s+import', "从os导入（可能执行系统命令）"),
    (r'import\s+subprocess', "导入subprocess（可执行系统命令）"),
    (r'import\s+socket', "导入socket（可能创建网络连接）"),
    (r'import\s+ctypes', "导入ctypes（可绕过Python沙箱）"),
    (r'import\s+pickle', "导入pickle（反序列化安全风险）"),
    (r'import\s+marshal', "导入marshal（反序列化安全风险）"),
    # 危险函数调用
    (r'\beval\s*\(', "使用eval()（代码注入风险）"),
    (r'\bexec\s*\(', "使用exec()（代码注入风险）"),
    (r'\bcompile\s*\(', "使用compile()（动态代码编译）"),
    (r'\b__import__\s*\(', "使用__import__()（动态导入）"),
    (r'\bos\.system\s*\(', "调用os.system()（执行系统命令）"),
    (r'\bos\.popen\s*\(', "调用os.popen()（执行系统命令）"),
    (r'\bshutil\.rmtree\s*\(', "调用shutil.rmtree()（递归删除）"),
    # 文件操作
    (r'\bopen\s*\([^)]*["\']w', "写入文件操作"),
    (r'\bopen\s*\([^)]*["\']a', "追加文件操作"),
    # 网络
    (r'urllib\.request', "使用urllib.request（网络请求）"),
    (r'requests\.', "使用requests库（网络请求）"),
    # 敏感路径
    (r'/etc/passwd', "访问/etc/passwd"),
    (r'/etc/shadow', "访问/etc/shadow"),
    (r'\.ssh/', "访问SSH密钥"),
    (r'\.env', "访问.env文件"),
]

# 允许的安全操作（白名单）
SAFE_IMPORTS = [
    "json", "math", "re", "datetime", "time", "uuid",
    "collections", "functools", "itertools", "operator",
    "typing", "dataclasses", "enum", "pathlib",
    "logging", "hashlib", "base64", "io",
    "xuanji", "xuanji.plugin", "xuanji.context",
]


class SecurityResult:
    """安全扫描结果"""

    def __init__(self):
        self.passed = True
        self.critical: List[dict] = []
        self.warnings: List[dict] = []
        self.info: List[dict] = []
        self.scan_time: float = 0

    def add_critical(self, message: str, location: str = "", code: str = ""):
        self.passed = False
        self.critical.append({"message": message, "location": location, "code": code})

    def add_warning(self, message: str, location: str = "", code: str = ""):
        self.warnings.append({"message": message, "location": location, "code": code})

    def add_info(self, message: str):
        self.info.append({"message": message})

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "critical_count": len(self.critical),
            "warning_count": len(self.warnings),
            "info_count": len(self.info),
            "scan_time": round(self.scan_time, 3),
            "score": self._calculate_score(),
        }

    def _calculate_score(self) -> int:
        """计算安全评分 (0-100)"""
        score = 100
        score -= len(self.critical) * 25
        score -= len(self.warnings) * 10
        score -= len(self.info) * 2
        return max(0, min(100, score))


class PerformanceResult:
    """性能测试结果"""

    def __init__(self):
        self.load_time: float = 0
        self.memory_usage: int = 0
        self.cpu_time: float = 0
        self.code_lines: int = 0
        self.complexity: int = 0
        self.grade: str = "N/A"

    def summary(self) -> dict:
        return {
            "load_time_ms": round(self.load_time * 1000, 2),
            "memory_kb": self.memory_usage,
            "cpu_time_ms": round(self.cpu_time * 1000, 2),
            "code_lines": self.code_lines,
            "complexity": self.complexity,
            "grade": self.grade,
        }


class PluginInfo:
    """插件信息"""

    def __init__(self, name: str, version: str, author: str = "",
                 description: str = "", path: str = ""):
        self.name = name
        self.version = version
        self.author = author
        self.description = description
        self.path = path
        self.certified = False
        self.certified_at: Optional[str] = None
        self.certified_by: str = "community"
        self.security_score: int = 0
        self.performance_grade: str = "N/A"
        self.submitted_at: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.hash: str = ""
        self.compat_version: str = ">=0.1.0"
        self.tags: List[str] = []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "certified": self.certified,
            "certified_at": self.certified_at,
            "certified_by": self.certified_by,
            "security_score": self.security_score,
            "performance_grade": self.performance_grade,
            "submitted_at": self.submitted_at,
            "hash": self.hash,
            "compat_version": self.compat_version,
            "tags": self.tags,
        }


# ============================================================
# PluginRegistry 类
# ============================================================

class PluginRegistry:
    """插件认证注册中心"""

    def __init__(self, registry_path: str = None):
        if registry_path is None:
            registry_path = str(Path.home() / ".xuanji" / "registry.json")
        self.registry_path = registry_path
        self.plugins: Dict[str, PluginInfo] = {}
        self._load_registry()

    def _load_registry(self):
        """加载注册表"""
        path = Path(self.registry_path)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for name, info in data.get("plugins", {}).items():
                    plugin = PluginInfo(
                        name=info.get("name", name),
                        version=info.get("version", "0.0.0"),
                        author=info.get("author", ""),
                        description=info.get("description", ""),
                        path=info.get("path", ""),
                    )
                    plugin.certified = info.get("certified", False)
                    plugin.certified_at = info.get("certified_at")
                    plugin.certified_by = info.get("certified_by", "community")
                    plugin.security_score = info.get("security_score", 0)
                    plugin.performance_grade = info.get("performance_grade", "N/A")
                    plugin.submitted_at = info.get("submitted_at", "")
                    plugin.hash = info.get("hash", "")
                    plugin.compat_version = info.get("compat_version", ">=0.1.0")
                    plugin.tags = info.get("tags", [])
                    self.plugins[name] = plugin
            except Exception:
                pass

    def _save_registry(self):
        """保存注册表"""
        path = Path(self.registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plugins": {name: p.to_dict() for name, p in self.plugins.items()},
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ============================================================
    # 提交插件
    # ============================================================

    def submit(self, plugin_path: str) -> dict:
        """提交插件到注册中心"""
        path = Path(plugin_path).resolve()

        # 验证插件结构
        plugin_toml = path / "plugin.toml"
        if not plugin_toml.is_file():
            return {"success": False, "error": "找不到 plugin.toml", "path": str(path)}

        # 解析plugin.toml
        info = self._parse_plugin_toml(str(plugin_toml))
        if not info:
            return {"success": False, "error": "plugin.toml格式错误", "path": str(path)}

        name = info.get("name", path.name)
        version = info.get("version", "0.0.0")

        # 计算文件hash
        file_hash = self._hash_directory(str(path))

        # 创建插件信息
        plugin = PluginInfo(
            name=name,
            version=version,
            author=info.get("author", ""),
            description=info.get("description", ""),
            path=str(path),
        )
        plugin.hash = file_hash
        plugin.tags = info.get("tags", [])

        # 检查是否已存在
        if name in self.plugins:
            existing = self.plugins[name]
            if existing.hash == file_hash and existing.version == version:
                return {"success": True, "message": f"插件 {name} v{version} 已存在", "duplicate": True}
            # 新版本
            plugin.certified = False  # 新版本需要重新认证

        self.plugins[name] = plugin
        self._save_registry()

        return {
            "success": True,
            "name": name,
            "version": version,
            "hash": file_hash,
            "message": f"插件 {name} v{version} 已提交",
        }

    # ============================================================
    # 安全扫描
    # ============================================================

    def scan(self, plugin_path: str) -> SecurityResult:
        """安全扫描插件"""
        result = SecurityResult()
        start = time.time()

        path = Path(plugin_path).resolve()

        # 扫描所有Python文件
        py_files = list(path.rglob("*.py"))
        result.add_info(f"扫描了 {len(py_files)} 个Python文件")

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                self._scan_content(content, str(py_file), result)
            except Exception as e:
                result.add_warning(f"无法读取文件: {py_file}: {e}")

        # 检查文件大小
        total_size = sum(f.stat().st_size for f in py_files) if py_files else 0
        if total_size > 10 * 1024 * 1024:  # 10MB
            result.add_warning(f"插件文件过大: {total_size / 1024 / 1024:.1f}MB")

        # AST分析
        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                self._analyze_ast(tree, str(py_file), result)
            except SyntaxError as e:
                result.add_critical(f"语法错误: {py_file}: {e}", str(py_file))

        result.scan_time = time.time() - start
        return result

    def _scan_content(self, content: str, filepath: str, result: SecurityResult):
        """扫描文件内容中的危险模式"""
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # 跳过注释
            if stripped.startswith("#"):
                continue

            for pattern, message in DANGEROUS_PATTERNS:
                if re.search(pattern, stripped):
                    result.add_critical(
                        message,
                        location=f"{filepath}:{i}",
                        code=stripped[:80],
                    )

    def _analyze_ast(self, tree: ast.AST, filepath: str, result: SecurityResult):
        """AST分析"""
        for node in ast.walk(tree):
            # 检查导入
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module not in SAFE_IMPORTS and not module.startswith("xuanji"):
                        result.add_warning(
                            f"导入非白名单模块: {alias.name}",
                            location=f"{filepath}:{node.lineno}",
                        )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module not in SAFE_IMPORTS and not module.startswith("xuanji"):
                        result.add_warning(
                            f"从非白名单模块导入: {node.module}",
                            location=f"{filepath}:{node.lineno}",
                        )

    # ============================================================
    # 性能测试
    # ============================================================

    def bench(self, plugin_path: str) -> PerformanceResult:
        """性能基准测试"""
        result = PerformanceResult()
        path = Path(plugin_path).resolve()

        # 代码行数
        py_files = list(path.rglob("*.py"))
        total_lines = 0
        for f in py_files:
            try:
                total_lines += len(f.read_text(encoding="utf-8").split("\n"))
            except Exception:
                pass
        result.code_lines = total_lines

        # 加载时间测试
        start = time.time()
        try:
            # 尝试导入插件入口
            plugin_toml = path / "plugin.toml"
            if plugin_toml.is_file():
                info = self._parse_plugin_toml(str(plugin_toml))
                entry = info.get("entry", "")
                if entry and ":" in entry:
                    module_path, class_name = entry.rsplit(":", 1)
                    module_file = path / (module_path.replace(".", "/") + ".py")
                    if module_file.is_file():
                        # 测量导入时间
                        import importlib.util
                        spec = importlib.util.spec_from_file_location(
                            f"plugin_{path.name}", str(module_file)
                        )
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
        except Exception:
            pass
        result.load_time = time.time() - start

        # 复杂度估算
        result.complexity = self._estimate_complexity(py_files)

        # 评级
        result.grade = self._grade_performance(result)

        return result

    def _estimate_complexity(self, py_files: List[Path]) -> int:
        """估算代码复杂度"""
        complexity = 0
        for f in py_files:
            try:
                content = f.read_text(encoding="utf-8")
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.If, ast.For, ast.While, ast.With)):
                        complexity += 1
                    elif isinstance(node, ast.Try):
                        complexity += 1
            except Exception:
                pass
        return complexity

    def _grade_performance(self, result: PerformanceResult) -> str:
        """性能评级"""
        score = 100

        # 加载时间
        if result.load_time > 1.0:
            score -= 30
        elif result.load_time > 0.5:
            score -= 15
        elif result.load_time > 0.1:
            score -= 5

        # 代码行数
        if result.code_lines > 5000:
            score -= 20
        elif result.code_lines > 2000:
            score -= 10
        elif result.code_lines > 500:
            score -= 5

        # 复杂度
        if result.complexity > 100:
            score -= 20
        elif result.complexity > 50:
            score -= 10

        if score >= 90:
            return "S"
        elif score >= 80:
            return "A"
        elif score >= 70:
            return "B"
        elif score >= 60:
            return "C"
        else:
            return "D"

    # ============================================================
    # 认证管理
    # ============================================================

    def certify(self, plugin_name: str, certifier: str = "community") -> dict:
        """认证插件"""
        if plugin_name not in self.plugins:
            return {"success": False, "error": f"插件不存在: {plugin_name}"}

        plugin = self.plugins[plugin_name]

        # 必须先通过安全扫描
        if not Path(plugin.path).is_dir():
            return {"success": False, "error": f"插件路径不存在: {plugin.path}"}

        scan_result = self.scan(plugin.path)
        if not scan_result.passed:
            return {
                "success": False,
                "error": "安全扫描未通过",
                "critical": len(scan_result.critical),
                "warnings": len(scan_result.warnings),
            }

        # 性能测试
        perf_result = self.bench(plugin.path)

        # 更新认证状态
        plugin.certified = True
        plugin.certified_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plugin.certified_by = certifier
        plugin.security_score = scan_result.summary()["score"]
        plugin.performance_grade = perf_result.grade

        self._save_registry()

        return {
            "success": True,
            "name": plugin_name,
            "version": plugin.version,
            "security_score": plugin.security_score,
            "performance_grade": plugin.performance_grade,
            "certified_at": plugin.certified_at,
        }

    def revoke(self, plugin_name: str) -> dict:
        """撤销认证"""
        if plugin_name not in self.plugins:
            return {"success": False, "error": f"插件不存在: {plugin_name}"}

        plugin = self.plugins[plugin_name]
        plugin.certified = False
        plugin.certified_at = None
        self._save_registry()

        return {"success": True, "message": f"插件 {plugin_name} 认证已撤销"}

    # ============================================================
    # 查询
    # ============================================================

    def list_certified(self) -> List[dict]:
        """列出所有认证插件"""
        certified = [p for p in self.plugins.values() if p.certified]
        return [p.to_dict() for p in certified]

    def list_all(self) -> List[dict]:
        """列出所有已提交插件"""
        return [p.to_dict() for p in self.plugins.values()]

    def get_info(self, plugin_name: str) -> Optional[dict]:
        """获取插件详情"""
        plugin = self.plugins.get(plugin_name)
        return plugin.to_dict() if plugin else None

    def search(self, keyword: str) -> List[dict]:
        """搜索插件"""
        keyword = keyword.lower()
        results = []
        for p in self.plugins.values():
            if (keyword in p.name.lower() or
                keyword in p.description.lower() or
                any(keyword in t.lower() for t in p.tags)):
                results.append(p.to_dict())
        return results

    def check_compatibility(self, plugin_name: str, framework_version: str = "0.1.0") -> dict:
        """检查插件兼容性"""
        plugin = self.plugins.get(plugin_name)
        if not plugin:
            return {"compatible": False, "error": "插件不存在"}

        # 简单版本比较
        compat = plugin.compat_version
        if compat.startswith(">="):
            required = compat[2:]
            return {
                "compatible": self._version_gte(framework_version, required),
                "required": required,
                "current": framework_version,
            }
        return {"compatible": True, "message": "无版本限制"}

    # ============================================================
    # 内部方法
    # ============================================================

    def _parse_plugin_toml(self, path: str) -> dict:
        """解析plugin.toml"""
        try:
            content = Path(path).read_text(encoding="utf-8")
            result = {}
            current_section = None

            for line in content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # 段落头
                section_match = re.match(r'^\[(\w+(?:\.\w+)*)\]$', line)
                if section_match:
                    current_section = section_match.group(1)
                    if current_section not in result:
                        result[current_section] = {}
                    continue

                # 键值对
                kv_match = re.match(r'^(\w+)\s*=\s*"([^"]*)"', line)
                if kv_match:
                    key, value = kv_match.group(1), kv_match.group(2)
                    if current_section:
                        result[current_section][key] = value
                    else:
                        result[key] = value

            # 扁平化
            plugin = result.get("plugin", {})
            return {
                "name": plugin.get("name", ""),
                "version": plugin.get("version", "0.0.0"),
                "type": plugin.get("type", "agent"),
                "entry": plugin.get("entry", ""),
                "description": plugin.get("description", ""),
                "author": plugin.get("author", ""),
                "tags": plugin.get("tags", []),
            }
        except Exception:
            return {}

    def _hash_directory(self, path: str) -> str:
        """计算目录内容的hash"""
        h = hashlib.sha256()
        path_obj = Path(path)
        for f in sorted(path_obj.rglob("*")):
            if f.is_file():
                try:
                    content = f.read_bytes()
                    h.update(f.relative_to(path_obj).as_posix().encode())
                    h.update(content)
                except Exception:
                    pass
        return h.hexdigest()[:16]

    def _version_gte(self, current: str, required: str) -> bool:
        """版本比较: current >= required"""
        try:
            cur_parts = [int(x) for x in current.split(".")]
            req_parts = [int(x) for x in required.split(".")]
            for i in range(max(len(cur_parts), len(req_parts))):
                c = cur_parts[i] if i < len(cur_parts) else 0
                r = req_parts[i] if i < len(req_parts) else 0
                if c > r:
                    return True
                if c < r:
                    return False
            return True
        except Exception:
            return False
