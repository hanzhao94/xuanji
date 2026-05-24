"""
xuanji 浏览器自动化

基于 playwright（优先）或 subprocess（回退）的浏览器控制工具。
支持打开网页、点击、输入、截屏、获取页面内容等操作。

示例:
    browser = BrowserEngine()
    browser.open("https://example.com")
    browser.click("#submit")
    browser.type_text("#search", "Python")
    browser.screenshot("result.png")
    text = browser.get_page_text()
"""

import base64
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 浏览器引擎基类
# ─────────────────────────────────────────────

class BrowserBackend:
    """浏览器后端基类"""

    def open(self, url: str) -> bool:
        raise NotImplementedError

    def click(self, selector: str, timeout: float = 10.0) -> bool:
        raise NotImplementedError

    def type_text(self, selector: str, text: str, timeout: float = 10.0) -> bool:
        raise NotImplementedError

    def screenshot(self, path: Optional[str] = None) -> Optional[bytes]:
        raise NotImplementedError

    def get_page_text(self) -> str:
        raise NotImplementedError

    def get_page_html(self) -> str:
        raise NotImplementedError

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def go_back(self) -> bool:
        raise NotImplementedError

    def go_forward(self) -> bool:
        raise NotImplementedError

    def reload(self) -> bool:
        raise NotImplementedError

    def execute_script(self, script: str) -> Any:
        raise NotImplementedError


# ─────────────────────────────────────────────
# Playwright 后端
# ─────────────────────────────────────────────

class PlaywrightBackend(BrowserBackend):
    """基于 Playwright 的浏览器后端

    需要安装: pip install playwright && playwright install chromium
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._page = None
        self._context = None
        self._connected = False

    def _ensure_connected(self) -> None:
        """确保浏览器已启动"""
        if self._connected:
            return

        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()

            # 启动浏览器
            browser_kwargs: Dict[str, Any] = {
                "headless": self._headless,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            }
            self._browser = self._playwright.chromium.launch(**browser_kwargs)

            # 创建上下文
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )

            self._page = self._context.new_page()
            self._connected = True

            logger.info("Playwright 浏览器已启动 (headless=%s)", self._headless)

        except ImportError:
            raise RuntimeError(
                "playwright 未安装。运行: pip install playwright && playwright install chromium"
            )
        except Exception as e:
            raise RuntimeError(f"Playwright 启动失败: {e}")

    def open(self, url: str) -> bool:
        """打开网页"""
        self._ensure_connected()
        try:
            self._page.goto(url, wait_until="networkidle", timeout=30000)
            logger.info("已打开: %s", url)
            return True
        except Exception as e:
            logger.error("打开网页失败: %s - %s", url, e)
            return False

    def click(self, selector: str, timeout: float = 10.0) -> bool:
        """点击元素"""
        self._ensure_connected()
        try:
            self._page.click(selector, timeout=timeout * 1000)
            logger.info("已点击: %s", selector)
            return True
        except Exception as e:
            logger.error("点击失败: %s - %s", selector, e)
            return False

    def type_text(self, selector: str, text: str, timeout: float = 10.0) -> bool:
        """输入文字"""
        self._ensure_connected()
        try:
            self._page.fill(selector, text, timeout=timeout * 1000)
            logger.info("已输入到 %s: %s", selector, text[:50])
            return True
        except Exception as e:
            logger.error("输入失败: %s - %s", selector, e)
            return False

    def screenshot(self, path: Optional[str] = None) -> Optional[bytes]:
        """截屏"""
        self._ensure_connected()
        try:
            kwargs: Dict[str, Any] = {}
            if path:
                kwargs["path"] = path
            data = self._page.screenshot(**kwargs)
            logger.info("截屏完成 (%d bytes)", len(data))
            return data
        except Exception as e:
            logger.error("截屏失败: %s", e)
            return None

    def get_page_text(self) -> str:
        """获取页面文本"""
        self._ensure_connected()
        try:
            text = self._page.inner_text("body")
            return text
        except Exception as e:
            logger.error("获取页面文本失败: %s", e)
            return ""

    def get_page_html(self) -> str:
        """获取页面 HTML"""
        self._ensure_connected()
        try:
            html = self._page.content()
            return html
        except Exception as e:
            logger.error("获取页面 HTML 失败: %s", e)
            return ""

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        """等待元素出现"""
        self._ensure_connected()
        try:
            self._page.wait_for_selector(selector, timeout=timeout * 1000)
            return True
        except Exception as e:
            logger.warning("等待元素超时: %s - %s", selector, e)
            return False

    def go_back(self) -> bool:
        """后退"""
        self._ensure_connected()
        try:
            self._page.go_back()
            return True
        except Exception as e:
            logger.error("后退失败: %s", e)
            return False

    def go_forward(self) -> bool:
        """前进"""
        self._ensure_connected()
        try:
            self._page.go_forward()
            return True
        except Exception as e:
            logger.error("前进失败: %s", e)
            return False

    def reload(self) -> bool:
        """刷新"""
        self._ensure_connected()
        try:
            self._page.reload()
            return True
        except Exception as e:
            logger.error("刷新失败: %s", e)
            return False

    def execute_script(self, script: str) -> Any:
        """执行 JavaScript"""
        self._ensure_connected()
        try:
            result = self._page.evaluate(script)
            return result
        except Exception as e:
            logger.error("执行脚本失败: %s", e)
            return None

    def press_key(self, selector: str, key: str, timeout: float = 10.0) -> bool:
        """按键"""
        self._ensure_connected()
        try:
            self._page.press(selector, key, timeout=timeout * 1000)
            return True
        except Exception as e:
            logger.error("按键失败: %s %s - %s", selector, key, e)
            return False

    def hover(self, selector: str, timeout: float = 10.0) -> bool:
        """悬停"""
        self._ensure_connected()
        try:
            self._page.hover(selector, timeout=timeout * 1000)
            return True
        except Exception as e:
            logger.error("悬停失败: %s - %s", selector, e)
            return False

    def select_option(self, selector: str, value: str = "", label: str = "", timeout: float = 10.0) -> bool:
        """选择下拉选项"""
        self._ensure_connected()
        try:
            kwargs: Dict[str, str] = {}
            if value:
                kwargs["value"] = value
            if label:
                kwargs["label"] = label
            self._page.select_option(selector, **kwargs, timeout=timeout * 1000)
            return True
        except Exception as e:
            logger.error("选择选项失败: %s - %s", selector, e)
            return False

    def get_url(self) -> str:
        """获取当前 URL"""
        self._ensure_connected()
        return self._page.url

    def get_title(self) -> str:
        """获取页面标题"""
        self._ensure_connected()
        return self._page.title()

    def close(self) -> None:
        """关闭浏览器"""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._connected = False
        logger.info("Playwright 浏览器已关闭")

    @property
    def page(self):
        """获取 Playwright page 对象（高级用法）"""
        self._ensure_connected()
        return self._page


# ─────────────────────────────────────────────
# Subprocess 回退后端（使用系统默认浏览器）
# ─────────────────────────────────────────────

class SubprocessBackend(BrowserBackend):
    """基于 subprocess 的浏览器后端（回退方案）

    功能受限，只能打开网页和截屏（通过第三方工具）。
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._last_url: str = ""

    def _open_browser(self, url: str) -> bool:
        """使用系统默认浏览器打开 URL"""
        try:
            if platform.system() == "Windows":
                os.startfile(url)  # type: ignore
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
            self._last_url = url
            logger.info("已用系统浏览器打开: %s", url)
            return True
        except Exception as e:
            logger.error("打开浏览器失败: %s", e)
            return False

    def open(self, url: str) -> bool:
        """打开网页"""
        return self._open_browser(url)

    def click(self, selector: str, timeout: float = 10.0) -> bool:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 click 操作")
        return False

    def type_text(self, selector: str, text: str, timeout: float = 10.0) -> bool:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 type_text 操作")
        return False

    def screenshot(self, path: Optional[str] = None) -> Optional[bytes]:
        """尝试使用系统工具截屏"""
        try:
            if platform.system() == "Windows":
                # 使用 PowerShell 截屏
                script = """
                Add-Type -AssemblyName System.Windows.Forms
                $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
                $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
                $path = "{path}"
                $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
                $graphics.Dispose()
                $bitmap.Dispose()
                """.format(path=path or os.path.join(tempfile.gettempdir(), "screenshot.png"))
                subprocess.run(
                    ["powershell", "-Command", script],
                    capture_output=True, timeout=10,
                )
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        return f.read()
            else:
                # Linux: 尝试 scrot 或 import
                for cmd in ["scrot", "import"]:
                    if shutil.which(cmd):
                        path = path or os.path.join(tempfile.gettempdir(), "screenshot.png")
                        subprocess.run([cmd, path], capture_output=True, timeout=10)
                        if os.path.exists(path):
                            with open(path, "rb") as f:
                                return f.read()
        except Exception as e:
            logger.error("截屏失败: %s", e)
        return None

    def get_page_text(self) -> str:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 get_page_text 操作")
        return ""

    def get_page_html(self) -> str:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 get_page_html 操作")
        return ""

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 wait_for 操作")
        return False

    def go_back(self) -> bool:
        return False

    def go_forward(self) -> bool:
        return False

    def reload(self) -> bool:
        if self._last_url:
            return self._open_browser(self._last_url)
        return False

    def execute_script(self, script: str) -> Any:
        """不可用（回退方案不支持）"""
        logger.warning("Subprocess 后端不支持 execute_script 操作")
        return None

    def close(self) -> None:
        pass


# ─────────────────────────────────────────────
# 浏览器引擎（主入口）
# ─────────────────────────────────────────────

class BrowserEngine:
    """浏览器引擎

    自动选择最佳后端：Playwright > Subprocess。

    Args:
        headless: 是否无头模式（仅 Playwright）
        force_backend: 强制使用指定后端 (playwright/subprocess)

    示例:
        browser = BrowserEngine()
        browser.open("https://example.com")
        browser.click("#button")
        browser.type_text("#input", "hello")
        browser.screenshot("page.png")
        print(browser.get_page_text())
        browser.close()
    """

    def __init__(
        self,
        headless: bool = True,
        force_backend: Optional[str] = None,
    ) -> None:
        self._headless = headless
        self._backend: Optional[BrowserBackend] = None
        self._backend_name: str = ""

        if force_backend == "playwright":
            self._backend = PlaywrightBackend(headless=headless)
            self._backend_name = "playwright"
        elif force_backend == "subprocess":
            self._backend = SubprocessBackend(headless=headless)
            self._backend_name = "subprocess"
        else:
            # 自动检测
            self._backend = self._detect_backend()
            self._backend_name = type(self._backend).__name__.replace("Backend", "").lower()

        logger.info("浏览器后端: %s (headless=%s)", self._backend_name, headless)

    def _detect_backend(self) -> BrowserBackend:
        """自动检测最佳后端"""
        # 尝试 Playwright
        try:
            backend = PlaywrightBackend(headless=self._headless)
            # 测试连接
            backend._ensure_connected()
            backend.close()
            # 重新创建（因为 close 了）
            return PlaywrightBackend(headless=self._headless)
        except Exception as e:
            logger.info("Playwright 不可用: %s，回退到 subprocess", e)

        # 回退到 subprocess
        return SubprocessBackend(headless=self._headless)

    @property
    def backend_name(self) -> str:
        """当前使用的后端名称"""
        return self._backend_name

    @property
    def is_playwright(self) -> bool:
        """是否使用 Playwright 后端"""
        return self._backend_name == "playwright"

    # ── 基本操作 ──

    def open(self, url: str) -> bool:
        """打开网页

        Args:
            url: 目标 URL

        Returns:
            是否成功
        """
        return self._backend.open(url)

    def click(self, selector: str, timeout: float = 10.0) -> bool:
        """点击元素

        Args:
            selector: CSS 选择器
            timeout: 超时秒数

        Returns:
            是否成功
        """
        return self._backend.click(selector, timeout=timeout)

    def type_text(self, selector: str, text: str, timeout: float = 10.0) -> bool:
        """输入文字

        Args:
            selector: CSS 选择器
            text: 要输入的文字
            timeout: 超时秒数

        Returns:
            是否成功
        """
        return self._backend.type_text(selector, text, timeout=timeout)

    def screenshot(self, path: Optional[str] = None) -> Optional[bytes]:
        """截屏

        Args:
            path: 保存路径（可选）

        Returns:
            图片字节数据（PNG 格式）
        """
        return self._backend.screenshot(path=path)

    def get_page_text(self) -> str:
        """获取页面文本

        Returns:
            页面可见文本
        """
        return self._backend.get_page_text()

    def get_page_html(self) -> str:
        """获取页面 HTML

        Returns:
            页面 HTML 源码
        """
        return self._backend.get_page_html()

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        """等待元素出现

        Args:
            selector: CSS 选择器
            timeout: 超时秒数

        Returns:
            是否成功等到
        """
        return self._backend.wait_for(selector, timeout=timeout)

    def close(self) -> None:
        """关闭浏览器"""
        self._backend.close()

    # ── 导航 ──

    def go_back(self) -> bool:
        """后退"""
        return self._backend.go_back()

    def go_forward(self) -> bool:
        """前进"""
        return self._backend.go_forward()

    def reload(self) -> bool:
        """刷新页面"""
        return self._backend.reload()

    def execute_script(self, script: str) -> Any:
        """执行 JavaScript

        Args:
            script: JavaScript 代码

        Returns:
            执行结果
        """
        return self._backend.execute_script(script)

    # ── Playwright 特有方法（仅 Playwright 后端可用） ──

    def press_key(self, selector: str, key: str, timeout: float = 10.0) -> bool:
        """按键（仅 Playwright）

        Args:
            selector: CSS 选择器
            key: 按键名（如 Enter, Tab, Escape）
            timeout: 超时秒数

        Returns:
            是否成功
        """
        if not self.is_playwright:
            logger.warning("press_key 仅 Playwright 后端支持")
            return False
        return self._backend.press_key(selector, key, timeout=timeout)  # type: ignore

    def hover(self, selector: str, timeout: float = 10.0) -> bool:
        """悬停（仅 Playwright）

        Args:
            selector: CSS 选择器
            timeout: 超时秒数

        Returns:
            是否成功
        """
        if not self.is_playwright:
            logger.warning("hover 仅 Playwright 后端支持")
            return False
        return self._backend.hover(selector, timeout=timeout)  # type: ignore

    def select_option(self, selector: str, value: str = "", label: str = "", timeout: float = 10.0) -> bool:
        """选择下拉选项（仅 Playwright）

        Args:
            selector: CSS 选择器
            value: option 的 value
            label: option 的显示文本
            timeout: 超时秒数

        Returns:
            是否成功
        """
        if not self.is_playwright:
            logger.warning("select_option 仅 Playwright 后端支持")
            return False
        return self._backend.select_option(selector, value=value, label=label, timeout=timeout)  # type: ignore

    def get_url(self) -> str:
        """获取当前 URL（仅 Playwright）"""
        if not self.is_playwright:
            return ""
        return self._backend.get_url()  # type: ignore

    def get_title(self) -> str:
        """获取页面标题（仅 Playwright）"""
        if not self.is_playwright:
            return ""
        return self._backend.get_title()  # type: ignore

    # ── 高级操作 ──

    def fill_form(self, form_data: Dict[str, str], timeout: float = 10.0) -> bool:
        """批量填写表单

        Args:
            form_data: {选择器: 值} 字典
            timeout: 每个字段超时

        Returns:
            是否全部成功
        """
        success = True
        for selector, value in form_data.items():
            if not self.type_text(selector, value, timeout=timeout):
                success = False
        return success

    def wait_and_click(self, selector: str, timeout: float = 10.0) -> bool:
        """等待元素出现后点击

        Args:
            selector: CSS 选择器
            timeout: 超时秒数

        Returns:
            是否成功
        """
        if self.wait_for(selector, timeout=timeout):
            return self.click(selector, timeout=timeout)
        return False

    def scroll_to_bottom(self, smooth: bool = True) -> None:
        """滚动到页面底部

        Args:
            smooth: 是否平滑滚动
        """
        if self.is_playwright:
            mode = "smooth" if smooth else "auto"
            self.execute_script(
                f"window.scrollTo({{top: document.body.scrollHeight, behavior: '{mode}'}})"
            )
        else:
            logger.warning("scroll_to_bottom 仅 Playwright 后端支持")

    def get_cookies(self) -> List[Dict[str, Any]]:
        """获取 cookies（仅 Playwright）"""
        if not self.is_playwright:
            return []
        try:
            return self._backend.page.context.cookies()  # type: ignore
        except Exception as e:
            logger.error("获取 cookies 失败: %s", e)
            return []

    def set_cookies(self, cookies: List[Dict[str, Any]]) -> bool:
        """设置 cookies（仅 Playwright）"""
        if not self.is_playwright:
            return False
        try:
            self._backend.page.context.add_cookies(cookies)  # type: ignore
            return True
        except Exception as e:
            logger.error("设置 cookies 失败: %s", e)
            return False

    def __enter__(self) -> "BrowserEngine":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

_default_browser: Optional[BrowserEngine] = None


def open_browser(headless: bool = True) -> BrowserEngine:
    """创建浏览器实例

    Args:
        headless: 是否无头模式

    Returns:
        BrowserEngine 实例
    """
    return BrowserEngine(headless=headless)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=== 浏览器引擎测试 ===")
    browser = BrowserEngine()
    print(f"后端: {browser.backend_name}")
    print()

    # 打开页面
    print("打开百度...")
    browser.open("https://www.baidu.com")

    if browser.is_playwright:
        print(f"URL: {browser.get_url()}")
        print(f"标题: {browser.get_title()}")

        # 搜索
        browser.type_text("#kw", "Python 教程")
        browser.click("#su")
        time.sleep(2)

        print(f"搜索后 URL: {browser.get_url()}")
        print(f"搜索后标题: {browser.get_title()}")

        # 获取文本
        text = browser.get_page_text()
        print(f"\n页面文本前 500 字符:")
        print(text[:500])

        # 截屏
        data = browser.screenshot()
        if data:
            print(f"\n截屏大小: {len(data)} bytes")

    browser.close()
    print("\n测试完成")
