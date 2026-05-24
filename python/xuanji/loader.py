"""
xuanji 插件发现+加载器

三种发现方式：
1. 目录扫描 — 扫描指定目录下的plugin.toml
2. 配置声明 — config.toml中[plugins]声明
3. pip安装 — entry_points发现（xuanji.plugins）

加载流程：
  发现 → 解析plugin.toml → 导入模块 → 实例化类 → 分类注册

用法：
    from xuanji.loader import PluginLoader
    
    loader = PluginLoader()
    plugins = loader.discover("./plugins")
    loader.load_all()
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# 支持的插件类型
PLUGIN_TYPES = frozenset({
    "agent", "tool", "channel", "llm", "memory", "scheduler",
})

# 类型到基类名的映射（用于校验）
_TYPE_TO_BASE = {
    "agent": "AgentPlugin",
    "tool": "ToolPlugin",
    "channel": "ChannelPlugin",
    "llm": "LLMPlugin",
    "memory": "MemoryPlugin",
    "scheduler": "SchedulerPlugin",
}


class PluginInfo:
    """插件元数据 — 从plugin.toml解析"""
    
    __slots__ = (
        "name", "type", "version", "entry", "description",
        "author", "dependencies", "config_schema",
        "path", "directory", "loaded", "instance", "error",
    )
    
    def __init__(self):
        self.name: str = ""
        self.type: str = ""
        self.version: str = "0.1.0"
        self.entry: str = ""  # "module.py:ClassName"
        self.description: str = ""
        self.author: str = ""
        self.dependencies: List[str] = []
        self.config_schema: Dict = {}
        self.path: str = ""       # plugin.toml路径
        self.directory: str = ""  # 插件目录
        self.loaded: bool = False
        self.instance: Any = None
        self.error: Optional[str] = None
    
    def __repr__(self):
        status = "✅" if self.loaded else ("❌" if self.error else "⏳")
        return f"<Plugin {status} {self.name} ({self.type}) v{self.version}>"
    
    def to_dict(self) -> Dict:
        """转为dict（用于状态展示）"""
        return {
            "name": self.name,
            "type": self.type,
            "version": self.version,
            "description": self.description,
            "directory": self.directory,
            "loaded": self.loaded,
            "error": self.error,
        }


class PluginLoader:
    """插件发现+加载器
    
    线程安全，支持热加载（重新discover+load_all）。
    单个插件加载失败不影响其他插件。
    """
    
    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}  # name → info
        self._by_type: Dict[str, List[PluginInfo]] = {}  # type → [info]
        self._discovered_paths: Set[str] = set()
    
    # ============================================================
    # 发现
    # ============================================================
    
    def discover(self, *paths: str) -> List[PluginInfo]:
        """发现插件 — 扫描目录找plugin.toml
        
        Args:
            paths: 插件目录列表
        
        Returns:
            新发现的插件列表
        """
        found = []
        for base in paths:
            base = os.path.abspath(base)
            if not os.path.isdir(base):
                continue
            
            # 直接在base下找plugin.toml
            toml_path = os.path.join(base, "plugin.toml")
            if os.path.isfile(toml_path):
                info = self._parse_toml(toml_path)
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
                toml_path = os.path.join(sub, "plugin.toml")
                if os.path.isfile(toml_path):
                    info = self._parse_toml(toml_path)
                    if info and self._register(info):
                        found.append(info)
                
                # 二级子目录（按类型分组: agents/hello/plugin.toml）
                try:
                    sub_entries = os.listdir(sub)
                except OSError:
                    continue
                for sub_entry in sorted(sub_entries):
                    subsub = os.path.join(sub, sub_entry)
                    if not os.path.isdir(subsub):
                        continue
                    toml_path = os.path.join(subsub, "plugin.toml")
                    if os.path.isfile(toml_path):
                        info = self._parse_toml(toml_path)
                        if info and self._register(info):
                            found.append(info)
        
        return found
    
    def discover_from_config(self, config: Dict) -> List[PluginInfo]:
        """从配置声明中发现插件
        
        config.toml格式:
            [plugins]
            paths = ["./plugins", "/opt/xuanji/plugins"]
            
            [plugins.extra]
            my-tool = {path = "./my-tool", type = "tool"}
        """
        found = []
        
        # 路径发现
        paths = config.get("plugins", {}).get("paths", [])
        if paths:
            found.extend(self.discover(*paths))
        
        # 显式声明
        extra = config.get("plugins", {}).get("extra", {})
        for name, decl in extra.items():
            if isinstance(decl, dict) and "path" in decl:
                plugin_dir = os.path.abspath(decl["path"])
                toml_path = os.path.join(plugin_dir, "plugin.toml")
                if os.path.isfile(toml_path):
                    info = self._parse_toml(toml_path)
                    if info:
                        # 配置覆盖
                        if "type" in decl:
                            info.type = decl["type"]
                        if self._register(info):
                            found.append(info)
        
        return found
    
    def discover_from_entrypoints(self) -> List[PluginInfo]:
        """从pip entry_points发现插件
        
        pyproject.toml:
            [project.entry-points."xuanji.plugins"]
            my-tool = "my_package:MyTool"
        """
        found = []
        
        try:
            if sys.version_info >= (3, 12):
                from importlib.metadata import entry_points
                eps = entry_points(group="xuanji.plugins")
            elif sys.version_info >= (3, 10):
                from importlib.metadata import entry_points
                eps = entry_points(group="xuanji.plugins")
            else:
                from importlib.metadata import entry_points as _ep
                all_eps = _ep()
                eps = all_eps.get("xuanji.plugins", [])
        except Exception:
            return found
        
        for ep in eps:
            info = PluginInfo()
            info.name = ep.name
            info.entry = f"__entrypoint__:{ep.name}"
            info.version = "0.0.0"
            # 类型从入口点元数据推断
            info.type = "agent"  # 默认
            
            try:
                cls = ep.load()
                info.instance = cls() if callable(cls) else cls
                info.loaded = True
                
                # 推断类型
                cls_name = type(info.instance).__mro__
                for base in cls_name:
                    base_name = base.__name__
                    for ptype, bname in _TYPE_TO_BASE.items():
                        if base_name == bname:
                            info.type = ptype
                            break
                
                if hasattr(info.instance, "name") and info.instance.name:
                    info.name = info.instance.name
                if hasattr(info.instance, "version"):
                    info.version = info.instance.version
                if hasattr(info.instance, "description"):
                    info.description = info.instance.description
                
                if self._register(info):
                    found.append(info)
            except Exception as e:
                info.error = str(e)
                info.loaded = False
                if self._register(info):
                    found.append(info)
        
        return found
    
    # ============================================================
    # 加载
    # ============================================================
    
    def load_all(self) -> Tuple[int, int]:
        """加载所有已发现的插件
        
        Returns:
            (成功数, 失败数)
        """
        ok = 0
        fail = 0
        for info in self._plugins.values():
            if info.loaded:
                ok += 1
                continue
            if self._load_one(info):
                ok += 1
            else:
                fail += 1
        return ok, fail
    
    def load(self, name: str) -> bool:
        """加载单个插件"""
        info = self._plugins.get(name)
        if not info:
            return False
        if info.loaded:
            return True
        return self._load_one(info)
    
    def unload(self, name: str) -> bool:
        """卸载单个插件"""
        info = self._plugins.get(name)
        if not info or not info.loaded:
            return False
        
        try:
            if info.instance and hasattr(info.instance, "on_unload"):
                info.instance.on_unload()
        except Exception:
            pass
        
        info.instance = None
        info.loaded = False
        info.error = None
        return True
    
    # ============================================================
    # 查询
    # ============================================================
    
    def get(self, name: str) -> Optional[PluginInfo]:
        """按名称获取插件"""
        return self._plugins.get(name)
    
    def get_instance(self, name: str) -> Optional[Any]:
        """获取插件实例"""
        info = self._plugins.get(name)
        if info and info.loaded:
            return info.instance
        return None
    
    def by_type(self, plugin_type: str) -> List[PluginInfo]:
        """按类型获取插件列表"""
        return list(self._by_type.get(plugin_type, []))
    
    def all_plugins(self) -> List[PluginInfo]:
        """获取所有插件"""
        return list(self._plugins.values())
    
    def loaded_plugins(self) -> List[PluginInfo]:
        """获取所有已加载的插件"""
        return [p for p in self._plugins.values() if p.loaded]
    
    def failed_plugins(self) -> List[PluginInfo]:
        """获取所有加载失败的插件"""
        return [p for p in self._plugins.values() if p.error]
    
    def summary(self) -> Dict:
        """加载摘要"""
        total = len(self._plugins)
        loaded = sum(1 for p in self._plugins.values() if p.loaded)
        failed = sum(1 for p in self._plugins.values() if p.error)
        by_type = {}
        for ptype in PLUGIN_TYPES:
            plugins = self._by_type.get(ptype, [])
            if plugins:
                by_type[ptype] = len(plugins)
        
        return {
            "total": total,
            "loaded": loaded,
            "failed": failed,
            "pending": total - loaded - failed,
            "by_type": by_type,
        }
    
    # ============================================================
    # 内部方法
    # ============================================================
    
    def _register(self, info: PluginInfo) -> bool:
        """注册插件（去重）"""
        real_path = os.path.realpath(info.path) if info.path else info.name
        if real_path in self._discovered_paths:
            return False
        self._discovered_paths.add(real_path)
        
        # 名称冲突检查
        if info.name in self._plugins:
            existing = self._plugins[info.name]
            # 版本更高的覆盖
            if self._version_cmp(info.version, existing.version) <= 0:
                return False
            # 从旧类型列表中移除
            old_list = self._by_type.get(existing.type, [])
            self._by_type[existing.type] = [
                p for p in old_list if p.name != info.name
            ]
        
        self._plugins[info.name] = info
        self._by_type.setdefault(info.type, []).append(info)
        return True
    
    def _parse_toml(self, toml_path: str) -> Optional[PluginInfo]:
        """解析plugin.toml"""
        toml_path = os.path.abspath(toml_path)
        
        try:
            data = self._read_toml(toml_path)
        except Exception as e:
            info = PluginInfo()
            info.path = toml_path
            info.directory = os.path.dirname(toml_path)
            info.name = os.path.basename(info.directory)
            info.error = f"TOML解析失败: {e}"
            return info
        
        plugin_section = data.get("plugin", data)
        
        info = PluginInfo()
        info.path = toml_path
        info.directory = os.path.dirname(toml_path)
        info.name = plugin_section.get("name", os.path.basename(info.directory))
        info.type = plugin_section.get("type", "agent")
        info.version = plugin_section.get("version", "0.1.0")
        info.entry = plugin_section.get("entry", "")
        info.description = plugin_section.get("description", "")
        info.author = plugin_section.get("author", "")
        info.dependencies = plugin_section.get("dependencies", [])
        info.config_schema = plugin_section.get("config", {})
        
        # 类型校验
        if info.type not in PLUGIN_TYPES:
            info.error = f"未知插件类型: {info.type}（支持: {', '.join(sorted(PLUGIN_TYPES))}）"
        
        return info
    
    def _read_toml(self, path: str) -> Dict:
        """读取TOML文件（兼容多种方式）"""
        # 优先用标准库tomllib (Python 3.11+)
        try:
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        except ImportError:
            pass
        
        # 尝试tomli
        try:
            import tomli
            with open(path, "rb") as f:
                return tomli.load(f)
        except ImportError:
            pass
        
        # 降级：手动解析简单TOML
        return self._simple_toml_parse(path)
    
    def _simple_toml_parse(self, path: str) -> Dict:
        """简单TOML解析（不依赖第三方库）
        
        支持: [section], key = "value", key = value
        不支持: 多行字符串、内联表、数组表
        """
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
                    
                    # 解析值类型
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    elif val.lower() == "true":
                        val = True
                    elif val.lower() == "false":
                        val = False
                    elif val.startswith("[") and val.endswith("]"):
                        # 简单数组
                        inner = val[1:-1].strip()
                        if inner:
                            val = [
                                v.strip().strip('"').strip("'")
                                for v in inner.split(",")
                                if v.strip()
                            ]
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
    
    def _load_one(self, info: PluginInfo) -> bool:
        """加载单个插件
        
        加载失败记录error，不抛异常，不影响其他插件。
        """
        if info.loaded:
            return True
        
        # 已有实例（entry_points方式）
        if info.instance is not None:
            info.loaded = True
            return True
        
        if not info.entry:
            info.error = "未指定entry（plugin.toml中需要 entry = \"module.py:ClassName\"）"
            return False
        
        try:
            cls = self._import_entry(info)
            info.instance = cls()
            info.loaded = True
            info.error = None
            
            # 同步元数据
            if hasattr(info.instance, "name") and info.instance.name:
                info.instance.name = info.instance.name or info.name
            if not getattr(info.instance, "name", ""):
                info.instance.name = info.name
            if hasattr(info.instance, "version"):
                info.instance.version = info.version
            
            return True
            
        except Exception as e:
            info.error = f"加载失败: {e}"
            info.loaded = False
            return False
    
    def _import_entry(self, info: PluginInfo) -> type:
        """导入entry指定的类
        
        格式: "module.py:ClassName" 或 "package.module:ClassName"
        """
        entry = info.entry
        
        if ":" not in entry:
            raise ValueError(
                f"entry格式错误: '{entry}'（应为 'module.py:ClassName'）"
            )
        
        module_part, class_name = entry.rsplit(":", 1)
        
        # 文件路径方式: "agent.py:HelloAgent"
        if module_part.endswith(".py"):
            module_file = os.path.join(info.directory, module_part)
            if not os.path.isfile(module_file):
                raise FileNotFoundError(f"模块文件不存在: {module_file}")
            
            # 生成唯一模块名避免冲突
            mod_name = f"xuanji._plugins_.{info.name}.{module_part[:-3]}"
            
            spec = importlib.util.spec_from_file_location(
                mod_name, module_file,
                submodule_search_locations=[info.directory]
            )
            if not spec or not spec.loader:
                raise ImportError(f"无法加载模块: {module_file}")
            
            # 确保插件目录在sys.path中（临时）
            if info.directory not in sys.path:
                sys.path.insert(0, info.directory)
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        
        else:
            # 包路径方式: "my_package.my_module:MyClass"
            try:
                module = importlib.import_module(module_part)
            except ImportError:
                # 尝试从插件目录导入
                if info.directory not in sys.path:
                    sys.path.insert(0, info.directory)
                module = importlib.import_module(module_part)
        
        cls = getattr(module, class_name, None)
        if cls is None:
            raise AttributeError(
                f"模块 {module_part} 中找不到类 {class_name}"
            )
        
        return cls
    
    @staticmethod
    def _version_cmp(a: str, b: str) -> int:
        """版本比较: >0 a更新, <0 b更新, 0 相同"""
        def parts(v):
            try:
                return [int(x) for x in v.split(".")]
            except (ValueError, AttributeError):
                return [0]
        
        pa, pb = parts(a), parts(b)
        # 补齐长度
        max_len = max(len(pa), len(pb))
        pa.extend([0] * (max_len - len(pa)))
        pb.extend([0] * (max_len - len(pb)))
        
        for x, y in zip(pa, pb):
            if x != y:
                return 1 if x > y else -1
        return 0
