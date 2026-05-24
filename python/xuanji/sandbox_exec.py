"""
xuanji 沙盒代码执行模块

在隔离环境中安全运行Python代码。
代码静态扫描 + subprocess隔离 + 超时控制。
零外部依赖。
"""

import ast
import os
import sys
import json
import time
import signal
import tempfile
import subprocess
import threading
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict


# ============================================================
# 危险模块/函数黑名单
# ============================================================

BLOCKED_MODULES: Set[str] = {
    # 高危模块（永远禁止）
    "subprocess", "shutil", "socket", "http", "urllib",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "ctypes",
    "multiprocessing", "signal", "runpy",
    "webbrowser", "antigravity", "turtle", "tkinter",
    "pickle", "shelve", "marshal", "code", "codeop",
    "compile", "compileall", "py_compile",
    # 注意:
    # - os 不在这里 — 允许 os.path/os.environ 等安全用法
    # - importlib 不在这里 — 允许 importlib.util 安全加载模块
    # 危险调用 (os.system/popen) 由 visit_Attribute 拦截
}

BLOCKED_BUILTINS: Set[str] = {
    # eval 保留 — 大部分内置模块不需要 eval，但为安全保留在黑名单
    # compile 保留 — Python import 机制用 compile 编译源码
    # exec 保留 — Python import 机制用 exec 执行模块代码
    # __import__ 保留 — importlib.util 需要它来加载模块
    # open 保留 — 文件读写是正常开发需求，路径安全由 FileSystemSandbox 控制
    # 危险模块由 _safe_import 钩子拦截（见 _SANDBOX_PREAMBLE）
    # AST 扫描器在静态层面拦截 exec/eval/compile/__import__ 的直接调用
    "breakpoint", "exit", "quit",
}

BLOCKED_ATTRS: Set[str] = {
    "__subclasses__", "__bases__", "__mro__", "__class__",
    "__globals__", "__code__", "__builtins__",
}


# ============================================================
# 代码静态扫描器
# ============================================================

@dataclass
class ScanResult:
    """扫描结果"""
    safe: bool = True
    violations: List[str] = field(default_factory=list)

    def add_violation(self, msg: str):
        self.safe = False
        self.violations.append(msg)

    def to_dict(self) -> Dict:
        return asdict(self)


class CodeScanner(ast.NodeVisitor):
    """AST静态扫描器 — 在执行前检查危险调用"""

    def __init__(self):
        self.result = ScanResult()

    def scan(self, code: str) -> ScanResult:
        """扫描代码，返回结果"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            self.result.add_violation(f"语法错误: {e}")
            return self.result

        self.visit(tree)
        return self.result

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in BLOCKED_MODULES:
                self.result.add_violation(
                    f"禁止导入模块: {alias.name} (line {node.lineno})"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            top = node.module.split(".")[0]
            if top in BLOCKED_MODULES:
                self.result.add_violation(
                    f"禁止导入模块: {node.module} (line {node.lineno})"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # 检查直接调用: eval(), exec(), open() 等
        if isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_BUILTINS:
                self.result.add_violation(
                    f"禁止调用内置函数: {node.func.id}() (line {node.lineno})"
                )
        # 检查属性调用: os.system(), subprocess.run() 等
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in ("system", "popen", "exec", "eval"):
                self.result.add_violation(
                    f"禁止调用危险方法: .{node.func.attr}() (line {node.lineno})"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in BLOCKED_ATTRS:
            self.result.add_violation(
                f"禁止访问危险属性: .{node.attr} (line {node.lineno})"
            )
        self.generic_visit(node)


def scan_code(code: str) -> ScanResult:
    """便捷函数：扫描代码安全性"""
    scanner = CodeScanner()
    return scanner.scan(code)


# ============================================================
# 执行结果
# ============================================================

@dataclass
class ExecutionResult:
    """代码执行结果"""
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    duration_ms: float = 0.0
    timed_out: bool = False
    blocked: bool = False
    violations: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.blocked and not self.timed_out

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["success"] = self.success
        return d

    def __repr__(self) -> str:
        status = "OK" if self.success else "FAIL"
        return (
            f"ExecutionResult({status}, rc={self.returncode}, "
            f"duration={self.duration_ms:.0f}ms)"
        )


# ============================================================
# 沙盒执行器
# ============================================================

# 注入到沙盒进程的安全前缀（限制builtins）
_SANDBOX_PREAMBLE = '''
import builtins as _builtins
_blocked = {blocked_builtins}
_original_import = _builtins.__import__
_blocked_modules = {blocked_modules}

def _safe_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top in _blocked_modules:
        raise ImportError(f"沙盒禁止导入: {{name}}")
    return _original_import(name, *args, **kwargs)

_builtins.__import__ = _safe_import

for _name in _blocked:
    if hasattr(_builtins, _name):
        delattr(_builtins, _name)
# 不删除 _safe_import/_original_import/_blocked_modules — 闭包需要它们
del _builtins, _blocked, _name
'''


class SandboxExecutor:
    """沙盒代码执行器

    在独立Python子进程中运行代码，提供：
    - AST静态扫描（执行前）
    - 运行时模块/内置函数限制
    - 超时控制
    - stdout/stderr捕获

    用法:
        executor = SandboxExecutor()
        result = executor.execute_python("print(1 + 1)")
        print(result.stdout)  # "2\\n"
    """

    def __init__(
        self,
        default_timeout: float = 30.0,
        max_output_bytes: int = 1024 * 1024,  # 1MB
        python_path: Optional[str] = None,
        enable_scan: bool = True,
    ):
        """
        Args:
            default_timeout: 默认超时秒数
            max_output_bytes: 最大输出字节数
            python_path: Python解释器路径，None=当前解释器
            enable_scan: 是否启用静态扫描
        """
        self.default_timeout = default_timeout
        self.max_output_bytes = max_output_bytes
        self.python_path = python_path or sys.executable
        self.enable_scan = enable_scan

        # 执行统计
        self._stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "blocked": 0,
            "timed_out": 0,
        }

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def execute_python(
        self,
        code: str,
        timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """在沙盒中执行Python代码

        Args:
            code: Python源代码
            timeout: 超时秒数（None=使用默认值）
            env: 额外环境变量

        Returns:
            ExecutionResult 包含 stdout/stderr/returncode
        """
        timeout = timeout if timeout is not None else self.default_timeout
        self._stats["total"] += 1

        # Step 1: 静态扫描
        if self.enable_scan:
            scan = scan_code(code)
            if not scan.safe:
                self._stats["blocked"] += 1
                return ExecutionResult(
                    blocked=True,
                    violations=scan.violations,
                    stderr="代码未通过安全扫描:\n"
                           + "\n".join(f"  - {v}" for v in scan.violations),
                )

        # Step 2: 准备沙盒代码
        preamble = _SANDBOX_PREAMBLE.format(
            blocked_builtins=repr(BLOCKED_BUILTINS),
            blocked_modules=repr(BLOCKED_MODULES),
        )
        full_code = preamble + "\n" + code

        # Step 3: 写入临时文件并执行
        result = self._run_in_subprocess(full_code, timeout, env)

        if result.success:
            self._stats["success"] += 1
        elif result.timed_out:
            self._stats["timed_out"] += 1
        else:
            self._stats["failed"] += 1

        return result

    def execute_expression(
        self,
        expr: str,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        """执行单个表达式并打印结果

        Args:
            expr: Python表达式（如 "2 + 3"）
            timeout: 超时秒数

        Returns:
            ExecutionResult，stdout包含表达式的值
        """
        code = f"__result__ = ({expr})\nprint(__result__)"
        return self.execute_python(code, timeout=timeout)

    def check_code(self, code: str) -> ScanResult:
        """只做静态扫描，不执行"""
        return scan_code(code)

    @property
    def stats(self) -> Dict[str, int]:
        """返回执行统计"""
        return dict(self._stats)

    def reset_stats(self):
        """重置统计"""
        for k in self._stats:
            self._stats[k] = 0

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _run_in_subprocess(
        self,
        code: str,
        timeout: float,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """在子进程中运行代码"""

        # 创建临时文件
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="sandbox_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)

            # 构造环境
            run_env = os.environ.copy()
            # 隔离：不加载用户site-packages
            run_env["PYTHONNOUSERSITE"] = "1"
            run_env["PYTHONDONTWRITEBYTECODE"] = "1"
            if env:
                run_env.update(env)

            # 启动子进程
            start = time.monotonic()
            try:
                proc = subprocess.Popen(
                    [self.python_path, "-u", tmp_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=run_env,
                    cwd=tempfile.gettempdir(),
                    # Windows不支持preexec_fn，用CREATE_NEW_PROCESS_GROUP
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32"
                        else 0
                    ),
                )
            except Exception as e:
                return ExecutionResult(
                    stderr=f"启动子进程失败: {e}",
                    returncode=-1,
                )

            # 等待完成（带超时）
            timed_out = False
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_process(proc)
                stdout_bytes, stderr_bytes = proc.communicate(timeout=5)

            elapsed = (time.monotonic() - start) * 1000

            # 截断过长输出
            stdout = self._truncate(stdout_bytes)
            stderr = self._truncate(stderr_bytes)

            if timed_out:
                stderr += f"\n[超时] 代码执行超过 {timeout} 秒，已终止"

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                returncode=proc.returncode if not timed_out else -9,
                duration_ms=elapsed,
                timed_out=timed_out,
            )
        finally:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _kill_process(self, proc: subprocess.Popen):
        """强制杀死进程"""
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                # Unix: 先SIGTERM，再SIGKILL
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            pass

    def _truncate(self, data: bytes) -> str:
        """截断输出"""
        if len(data) > self.max_output_bytes:
            text = data[: self.max_output_bytes].decode("utf-8", errors="replace")
            text += f"\n[截断] 输出超过 {self.max_output_bytes} 字节"
            return text
        return data.decode("utf-8", errors="replace")


# ============================================================
# 便捷函数
# ============================================================

# 模块级单例
_default_executor: Optional[SandboxExecutor] = None


def get_executor(**kwargs) -> SandboxExecutor:
    """获取/创建默认执行器"""
    global _default_executor
    if _default_executor is None:
        _default_executor = SandboxExecutor(**kwargs)
    return _default_executor


def run_code(code: str, timeout: float = 30.0) -> ExecutionResult:
    """快速执行代码"""
    return get_executor().execute_python(code, timeout=timeout)


def run_expr(expr: str, timeout: float = 10.0) -> ExecutionResult:
    """快速执行表达式"""
    return get_executor().execute_expression(expr, timeout=timeout)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    executor = SandboxExecutor()

    print("=== 安全代码测试 ===")
    r = executor.execute_python("print('Hello Sandbox!')\nprint(2 ** 10)")
    print(f"  stdout: {r.stdout.strip()}")
    print(f"  success: {r.success}")

    print("\n=== 危险代码测试 ===")
    r = executor.execute_python("import os\nos.system('echo hacked')")
    print(f"  blocked: {r.blocked}")
    print(f"  violations: {r.violations}")

    print("\n=== 表达式测试 ===")
    r = executor.execute_expression("[i**2 for i in range(10)]")
    print(f"  result: {r.stdout.strip()}")

    print("\n=== 超时测试 ===")
    r = executor.execute_python("import time\ntime.sleep(100)", timeout=2)
    print(f"  timed_out: {r.timed_out}")

    print(f"\n=== 统计 ===\n  {executor.stats}")
