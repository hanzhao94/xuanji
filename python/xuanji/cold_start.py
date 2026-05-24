"""
xuanji 冷启动优化

分析并优化模块加载时间，找出启动瓶颈。
支持按需加载（lazy load），把启动时间从秒级压到毫秒级。

用法:
    optimizer = ColdStartOptimizer()
    
    # 分析启动时间
    profile = optimizer.profile()
    print(profile.report())
    
    # 找出瓶颈
    bottlenecks = optimizer.find_bottlenecks(threshold_ms=100)
    for b in bottlenecks:
        print(f"{b.module}: {b.time_ms:.1f}ms")
    
    # 启用懒加载
    optimizer.enable_lazy_load()
    
    # 优化后重新分析
    new_profile = optimizer.profile()
    improvement = optimizer.compare(profile, new_profile)
    print(improvement.report())
"""

import importlib
import json
import logging
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─── 加载事件 ──────────────────────────────────────────────

@dataclass
class LoadEvent:
    """模块加载事件"""
    module_name: str = ""
    start_time: float = 0
    end_time: float = 0
    file_path: str = ""
    size_bytes: int = 0
    is_lazy: bool = False
    traceback: str = ""
    
    @property
    def time_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000
    
    def to_dict(self) -> Dict:
        return {
            "module": self.module_name,
            "time_ms": round(self.time_ms, 3),
            "file": self.file_path,
            "size_bytes": self.size_bytes,
            "lazy": self.is_lazy,
        }


@dataclass
class LoadProfile:
    """启动加载画像"""
    events: List[LoadEvent] = field(default_factory=list)
    total_time_ms: float = 0
    module_count: int = 0
    lazy_count: int = 0
    start_time: float = 0
    end_time: float = 0
    
    @property
    def eager_time_ms(self) -> float:
        """非懒加载总时间"""
        return sum(e.time_ms for e in self.events if not e.is_lazy)
    
    @property
    def lazy_time_ms(self) -> float:
        """懒加载总时间"""
        return sum(e.time_ms for e in self.events if e.is_lazy)
    
    @property
    def avg_time_ms(self) -> float:
        if not self.events:
            return 0
        return self.total_time_ms / len(self.events)
    
    def top_modules(self, n: int = 10) -> List[LoadEvent]:
        """加载最慢的N个模块"""
        return sorted(self.events, key=lambda e: e.time_ms, reverse=True)[:n]
    
    def report(self) -> str:
        """生成文本报告"""
        lines = [
            "=" * 60,
            "⏱️  冷启动分析报告",
            "=" * 60,
            f"  总加载时间: {self.total_time_ms:.1f}ms",
            f"  模块数量:   {self.module_count}",
            f"  懒加载:     {self.lazy_count}",
            f"  非懒加载:   {self.eager_time_ms:.1f}ms",
            f"  平均:       {self.avg_time_ms:.1f}ms/模块",
            "",
            "  ── 最慢的10个模块 ──",
        ]
        
        for i, e in enumerate(self.top_modules(10), 1):
            lazy_tag = " [懒]" if e.is_lazy else ""
            lines.append(
                f"    {i:2d}. {e.module_name:<30s} "
                f"{e.time_ms:>8.1f}ms{lazy_tag}"
            )
        
        lines.append("=" * 60)
        return "\n".join(lines)
    
    def to_dict(self) -> Dict:
        return {
            "total_time_ms": round(self.total_time_ms, 3),
            "module_count": self.module_count,
            "lazy_count": self.lazy_count,
            "eager_time_ms": round(self.eager_time_ms, 3),
            "lazy_time_ms": round(self.lazy_time_ms, 3),
            "avg_time_ms": round(self.avg_time_ms, 3),
            "modules": [e.to_dict() for e in self.events],
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class ImprovementReport:
    """优化对比报告"""
    before: LoadProfile
    after: LoadProfile
    
    @property
    def time_saved_ms(self) -> float:
        return self.before.total_time_ms - self.after.total_time_ms
    
    @property
    def improvement_pct(self) -> float:
        if self.before.total_time_ms == 0:
            return 0
        return (self.time_saved_ms / self.before.total_time_ms) * 100
    
    def report(self) -> str:
        lines = [
            "=" * 60,
            "🚀 启动优化对比",
            "=" * 60,
            f"  优化前: {self.before.total_time_ms:.1f}ms ({self.before.module_count} modules)",
            f"  优化后: {self.after.total_time_ms:.1f}ms ({self.after.module_count} modules)",
            f"  节省:   {self.time_saved_ms:.1f}ms ({self.improvement_pct:+.1f}%)",
        ]
        
        if self.improvement_pct > 0:
            lines.append(f"  ✅ 启动速度提升 {self.improvement_pct:.1f}%")
        else:
            lines.append(f"  ⚠️  启动速度下降 {abs(self.improvement_pct):.1f}%")
        
        lines.append("=" * 60)
        return "\n".join(lines)


# ─── 加载追踪器 ────────────────────────────────────────────

class _ImportTracer:
    """导入追踪器（利用sys.meta_path）"""
    
    def __init__(self):
        self._events: List[LoadEvent] = []
        self._lock = threading.Lock()
        self._active = False
        self._original_meta_path = None
        self._lazy_modules: Set[str] = set()
    
    def start(self) -> None:
        """开始追踪"""
        if self._active:
            return
        
        self._active = True
        self._events = []
        self._original_meta_path = list(sys.meta_path)
        
        # 插入追踪finder
        tracer = self
        original_path_hooks = list(sys.path_hooks)
        
        class TracingFinder:
            """追踪性finder"""
            
            def find_module(self, fullname, path=None):
                if fullname in sys.modules:
                    return None  # 已加载
                
                return TracingLoader(fullname, tracer)
        
        class TracingLoader:
            """追踪性loader"""
            
            def __init__(self, name, tracer):
                self._name = name
                self._tracer = tracer
            
            def load_module(self, fullname):
                if fullname in sys.modules:
                    return sys.modules[fullname]
                
                event = LoadEvent(
                    module_name=fullname,
                    start_time=time.perf_counter(),
                    is_lazy=fullname in self._tracer._lazy_modules,
                )
                
                # 使用原始finder加载
                loaded = False
                for finder in tracer._original_meta_path:
                    if isinstance(finder, TracingFinder):
                        continue
                    try:
                        spec = finder.find_spec(fullname, None)
                        if spec is not None:
                            loader = spec.loader
                            if loader is not None:
                                mod = importlib.util.module_from_spec(spec)
                                sys.modules[fullname] = mod
                                loader.exec_module(mod)
                                loaded = True
                                
                                # 记录文件信息
                                if hasattr(loader, 'get_filename'):
                                    try:
                                        event.file_path = loader.get_filename()
                                    except Exception:
                                        pass
                                
                                # 文件大小
                                if event.file_path:
                                    try:
                                        import os
                                        event.size_bytes = os.path.getsize(event.file_path)
                                    except Exception:
                                        pass
                            break
                    except Exception:
                        continue
                
                if not loaded:
                    # 回退到标准导入
                    mod = importlib.import_module(fullname)
                
                event.end_time = time.perf_counter()
                
                with self._tracer._lock:
                    self._tracer._events.append(event)
                
                return sys.modules.get(fullname)
            
            def create_module(self, spec):
                return None
            
            def exec_module(self, module):
                pass
        
        self._tracing_finder = TracingFinder()
        sys.meta_path.insert(0, self._tracing_finder)
    
    def stop(self) -> List[LoadEvent]:
        """停止追踪，返回事件列表"""
        if not self._active:
            return []
        
        self._active = False
        
        # 移除追踪finder
        if self._original_meta_path:
            sys.meta_path = list(self._original_meta_path)
        
        with self._lock:
            events = list(self._events)
        
        return events
    
    def mark_lazy(self, module_name: str) -> None:
        """标记为懒加载"""
        self._lazy_modules.add(module_name)
    
    @property
    def events(self) -> List[LoadEvent]:
        with self._lock:
            return list(self._events)


# ─── 懒加载代理 ────────────────────────────────────────────

class LazyModule:
    """懒加载模块代理
    
    首次访问时才真正导入模块。
    
    用法:
        lazy_json = LazyModule("json")
        # 此时json还未导入
        
        data = lazy_json.loads('{"a": 1}')
        # 首次访问时才导入
    """
    
    def __init__(self, module_name: str, preload_attrs: Optional[List[str]] = None):
        self._module_name = module_name
        self._module: Optional[Any] = None
        self._load_time_ms: float = 0
        self._preload_attrs = preload_attrs or []
    
    def _load(self) -> Any:
        """延迟加载模块"""
        if self._module is None:
            start = time.perf_counter()
            self._module = importlib.import_module(self._module_name)
            self._load_time_ms = (time.perf_counter() - start) * 1000
            logger.debug(f"懒加载: {self._module_name} ({self._load_time_ms:.1f}ms)")
        return self._module
    
    def __getattr__(self, name: str) -> Any:
        return getattr(self._load(), name)
    
    def __dir__(self) -> List[str]:
        return dir(self._load())
    
    @property
    def loaded(self) -> bool:
        return self._module is not None
    
    @property
    def load_time_ms(self) -> float:
        return self._load_time_ms
    
    def __repr__(self):
        status = "已加载" if self.loaded else "未加载"
        return f"<LazyModule {self._module_name} ({status})>"


# ─── 冷启动优化器 ──────────────────────────────────────────

class ColdStartOptimizer:
    """冷启动优化器
    
    分析启动时间、找出瓶颈、应用懒加载优化。
    """
    
    def __init__(self):
        self._tracer = _ImportTracer()
        self._lazy_modules: Dict[str, LazyModule] = {}
        self._profile_history: List[LoadProfile] = []
    
    def profile(self, target_modules: Optional[List[str]] = None) -> LoadProfile:
        """分析启动时间
        
        Args:
            target_modules: 指定要分析的模块（None则分析所有）
        
        Returns:
            加载画像
        """
        start = time.perf_counter()
        self._tracer.start()
        
        # 导入目标模块
        modules_to_load = target_modules or self._get_default_modules()
        
        for mod_name in modules_to_load:
            if mod_name not in sys.modules:
                try:
                    importlib.import_module(mod_name)
                except Exception as e:
                    logger.warning(f"导入失败: {mod_name} — {e}")
        
        # 停止追踪
        events = self._tracer.stop()
        end = time.perf_counter()
        
        # 构建画像
        profile = LoadProfile(
            events=events,
            total_time_ms=(end - start) * 1000,
            module_count=len(events),
            lazy_count=sum(1 for e in events if e.is_lazy),
            start_time=start,
            end_time=end,
        )
        
        self._profile_history.append(profile)
        return profile
    
    def _get_default_modules(self) -> List[str]:
        """获取默认要分析的模块列表"""
        # 当前已加载的模块（排除内置和已导入的）
        existing = set(sys.modules.keys())
        
        # xuanji相关模块
        xuanji_modules = []
        try:
            import xuanji
            xuanji_dir = xuanji.__path__[0] if hasattr(xuanji, '__path__') else ""
            
            import os
            if xuanji_dir and os.path.isdir(xuanji_dir):
                for f in os.listdir(xuanji_dir):
                    if f.endswith(".py") and not f.startswith("_"):
                        mod_name = f"xuanji.{f[:-3]}"
                        if mod_name not in existing:
                            xuanji_modules.append(mod_name)
        except Exception:
            pass
        
        # 常用标准库
        stdlib = [
            "json", "logging", "threading", "queue", "collections",
            "dataclasses", "pathlib", "io", "re", "hashlib",
            "base64", "struct", "uuid", "timeit",
        ]
        
        return xuanji_modules + stdlib
    
    def find_bottlenecks(self, threshold_ms: float = 50.0) -> List[LoadEvent]:
        """找出启动瓶颈
        
        Args:
            threshold_ms: 阈值（毫秒），超过此值的模块视为瓶颈
        
        Returns:
            瓶颈模块列表
        """
        if not self._profile_history:
            self.profile()
        
        latest = self._profile_history[-1]
        bottlenecks = [e for e in latest.events if e.time_ms > threshold_ms]
        return sorted(bottlenecks, key=lambda e: e.time_ms, reverse=True)
    
    def enable_lazy_load(self, modules: Optional[List[str]] = None) -> Dict[str, LazyModule]:
        """启用懒加载
        
        Args:
            modules: 要懒加载的模块列表（None则标记所有非核心模块）
        
        Returns:
            懒加载模块字典
        """
        core_modules = {
            "sys", "os", "json", "logging", "time", "threading",
            "types", "abc", "collections", "functools", "importlib",
        }
        
        if modules is None:
            # 自动选择：排除核心模块
            all_loaded = set(sys.modules.keys())
            modules = [m for m in all_loaded if m.split(".")[0] not in core_modules
                      and not m.startswith("_")]
        
        for mod_name in modules:
            if mod_name not in self._lazy_modules:
                self._lazy_modules[mod_name] = LazyModule(mod_name)
                self._tracer.mark_lazy(mod_name)
        
        logger.info(f"懒加载已启用: {len(self._lazy_modules)} 个模块")
        return self._lazy_modules
    
    def lazy(self, module_name: str) -> LazyModule:
        """获取懒加载模块代理
        
        Args:
            module_name: 模块名
        
        Returns:
            LazyModule代理
        """
        if module_name not in self._lazy_modules:
            self._lazy_modules[module_name] = LazyModule(module_name)
        return self._lazy_modules[module_name]
    
    def compare(self, before: LoadProfile, after: LoadProfile) -> ImprovementReport:
        """对比两次分析结果"""
        return ImprovementReport(before, after)
    
    def summary(self) -> str:
        """汇总报告"""
        if not self._profile_history:
            return "尚未分析"
        
        latest = self._profile_history[-1]
        lines = [
            f"📊 启动分析: {latest.total_time_ms:.1f}ms / {latest.module_count} modules",
        ]
        
        bottlenecks = self.find_bottlenecks(100)
        if bottlenecks:
            lines.append(f"  瓶颈模块: {len(bottlenecks)}")
            for b in bottlenecks[:5]:
                lines.append(f"    - {b.module_name}: {b.time_ms:.1f}ms")
        
        if len(self._profile_history) > 1:
            first = self._profile_history[0]
            report = self.compare(first, latest)
            lines.append(f"  优化效果: {report.time_saved_ms:.1f}ms ({report.improvement_pct:+.1f}%)")
        
        return "\n".join(lines)
    
    def save_profile(self, path: str, profile: Optional[LoadProfile] = None) -> None:
        """保存画像到文件"""
        p = profile or (self._profile_history[-1] if self._profile_history else None)
        if not p:
            raise ValueError("无画像数据")
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(p.to_json())
    
    def __repr__(self):
        profiles = len(self._profile_history)
        lazy = len(self._lazy_modules)
        return f"<ColdStartOptimizer {profiles} profiles, {lazy} lazy modules>"
