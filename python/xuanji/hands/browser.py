"""
浏览器操控

优先用 playwright（如果可用）。
回退用 subprocess 打开浏览器 URL。
"""

import subprocess
import sys
import shutil
from typing import Any, Optional


class BrowserHands:
    """浏览器操控引擎

    自动检测可用后端：
    1. playwright（完整控制）
    2. subprocess + 系统浏览器（仅打开URL）

    Usage:
        browser = BrowserHands()
        await browser.open_url("https://example.com")
        text = await browser.get_page_text()
        await browser.click_element("button.submit")
        await browser.close()
    """

    def __init__(self):
        self._pw = None        # playwright 实例
        self._browser = None   # browser 实例
        self._page = None      # 当前页面
        self._backend = "none"
        self._detect_backend()

    def _detect_backend(self):
        """检测可用后端"""
        try:
            import playwright
            self._backend = "playwright"
        except ImportError:
            self._backend = "subprocess"

    @property
    def backend(self) -> str:
        """当前使用的后端"""
        return self._backend

    async def _ensure_playwright(self):
        """懒初始化 playwright"""
        if self._page is not None:
            return

        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=False)
        self._page = await self._browser.new_page()

    async def open_url(self, url: str) -> bool:
        """打开URL

        Args:
            url: 要打开的网址

        Returns:
            是否成功
        """
        if self._backend == "playwright":
            try:
                await self._ensure_playwright()
                await self._page.goto(url, wait_until="domcontentloaded")
                return True
            except Exception:
                # 回退到 subprocess
                return self._open_url_subprocess(url)
        else:
            return self._open_url_subprocess(url)

    def _open_url_subprocess(self, url: str) -> bool:
        """用系统浏览器打开 URL"""
        try:
            if sys.platform == "win32":
                import os
                os.startfile(url)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url])
            else:
                # Linux: 尝试 xdg-open
                if shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", url])
                elif shutil.which("firefox"):
                    subprocess.Popen(["firefox", url])
                elif shutil.which("chromium-browser"):
                    subprocess.Popen(["chromium-browser", url])
                elif shutil.which("google-chrome"):
                    subprocess.Popen(["google-chrome", url])
                else:
                    return False
            return True
        except Exception:
            return False

    async def get_page_text(self) -> str:
        """获取当前页面文本内容

        仅 playwright 后端支持。

        Returns:
            页面文本内容，subprocess 后端返回空字符串
        """
        if self._backend != "playwright" or self._page is None:
            return ""
        try:
            return await self._page.inner_text("body")
        except Exception:
            return ""

    async def get_page_title(self) -> str:
        """获取当前页面标题"""
        if self._backend != "playwright" or self._page is None:
            return ""
        try:
            return await self._page.title()
        except Exception:
            return ""

    async def get_page_url(self) -> str:
        """获取当前页面URL"""
        if self._backend != "playwright" or self._page is None:
            return ""
        return self._page.url

    async def click_element(self, selector: str) -> bool:
        """点击页面元素

        Args:
            selector: CSS 选择器

        Returns:
            是否成功
        """
        if self._backend != "playwright" or self._page is None:
            return False
        try:
            await self._page.click(selector)
            return True
        except Exception:
            return False

    async def fill_element(self, selector: str, text: str) -> bool:
        """填充表单元素

        Args:
            selector: CSS 选择器
            text: 要填入的文本

        Returns:
            是否成功
        """
        if self._backend != "playwright" or self._page is None:
            return False
        try:
            await self._page.fill(selector, text)
            return True
        except Exception:
            return False

    async def wait_for(self, selector: str, timeout: int = 5000) -> bool:
        """等待元素出现

        Args:
            selector: CSS 选择器
            timeout: 超时毫秒数

        Returns:
            是否找到
        """
        if self._backend != "playwright" or self._page is None:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    async def screenshot(self) -> Optional[bytes]:
        """页面截图

        Returns:
            PNG bytes，不可用返回 None
        """
        if self._backend != "playwright" or self._page is None:
            return None
        try:
            return await self._page.screenshot()
        except Exception:
            return None

    async def evaluate(self, expression: str) -> Any:
        """执行 JavaScript

        Args:
            expression: JS 表达式

        Returns:
            执行结果
        """
        if self._backend != "playwright" or self._page is None:
            return None
        try:
            return await self._page.evaluate(expression)
        except Exception:
            return None

    async def close(self) -> None:
        """关闭浏览器"""
        try:
            if self._page:
                await self._page.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._pw = None
