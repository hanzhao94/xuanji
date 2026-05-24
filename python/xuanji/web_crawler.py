"""
xuanji 网页爬虫

基于 urllib.request 的网页抓取工具，零外部依赖。
支持自动编码识别、重定向跟随、正文提取、链接提取。

示例:
    crawler = WebCrawler()
    text = crawler.fetch("https://example.com")
    print(text[:500])
"""

import html
import logging
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class FetchResult:
    """抓取结果

    Attributes:
        url: 最终 URL（可能经过重定向）
        status: HTTP 状态码
        encoding: 识别的编码
        content_type: 内容类型
        text: 页面文本
        html: 原始 HTML
        headers: 响应头
        elapsed: 请求耗时（秒）
    """
    url: str = ""
    status: int = 0
    encoding: str = "utf-8"
    content_type: str = ""
    text: str = ""
    html: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


# ─────────────────────────────────────────────
# User-Agent 池
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(USER_AGENTS)


# ─────────────────────────────────────────────
# 编码检测
# ─────────────────────────────────────────────

# HTML meta charset 匹配
_META_CHARSET = re.compile(
    r'<meta[^>]*charset\s*=\s*["\']?([^"\'>\s]+)',
    re.IGNORECASE,
)

# HTML content-type 匹配
_META_CONTENT_TYPE = re.compile(
    r'<meta[^>]*content\s*=\s*["\'][^;]*;\s*charset\s*=\s*([^"\'>\s]+)',
    re.IGNORECASE,
)

# XML 声明
_XML_DECLARATION = re.compile(
    r'<\?xml[^>]*encoding\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# 常见编码别名映射
_ENCODING_ALIASES = {
    "gb2312": "gbk",
    "gb_2312-80": "gbk",
    "gb_2312": "gbk",
    "gb18030-2000": "gb18030",
    "ascii": "utf-8",
    "iso-8859-1": "latin-1",
}


def _detect_encoding(html_bytes: bytes, content_type: str = "") -> str:
    """自动检测网页编码

    优先级: HTTP Content-Type > XML 声明 > HTML meta charset > HTML meta content-type > 默认 UTF-8

    Args:
        html_bytes: 原始字节
        content_type: HTTP Content-Type 头

    Returns:
        编码名称
    """
    # 1. HTTP Content-Type
    if content_type:
        match = re.search(r"charset\s*=\s*(\S+)", content_type, re.IGNORECASE)
        if match:
            enc = match.group(1).strip(" \t\"';")
            return _normalize_encoding(enc)

    # 2. 尝试解码前1024字节来查找 meta
    try:
        preview = html_bytes[:2048].decode("ascii", errors="ignore")
    except Exception:
        preview = ""

    # XML 声明
    match = _XML_DECLARATION.search(preview)
    if match:
        return _normalize_encoding(match.group(1))

    # meta charset
    match = _META_CHARSET.search(preview)
    if match:
        return _normalize_encoding(match.group(1))

    # meta content-type
    match = _META_CONTENT_TYPE.search(preview)
    if match:
        return _normalize_encoding(match.group(1))

    return "utf-8"


def _normalize_encoding(enc: str) -> str:
    """标准化编码名称"""
    enc = enc.lower().strip()
    return _ENCODING_ALIASES.get(enc, enc)


def _decode_bytes(html_bytes: bytes, content_type: str = "") -> str:
    """解码 HTML 字节为字符串

    Args:
        html_bytes: 原始字节
        content_type: HTTP Content-Type

    Returns:
        解码后的文本
    """
    encoding = _detect_encoding(html_bytes, content_type)

    # 尝试检测到的编码
    try:
        return html_bytes.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        pass

    # 回退到常见编码
    for fallback in ["utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1"]:
        try:
            return html_bytes.decode(fallback, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue

    # 终极回退
    return html_bytes.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────
# 重定向处理器
# ─────────────────────────────────────────────

class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """自定义重定向处理器，记录最终 URL"""

    def __init__(self) -> None:
        super().__init__()
        self.final_url: str = ""
        self.redirect_count: int = 0

    def redirect_request(
        self, req: urllib.request.Request, fp, code, msg, headers, newurl
    ) -> Optional[urllib.request.Request]:
        self.final_url = newurl
        self.redirect_count += 1
        if self.redirect_count > 10:
            return None  # 停止重定向
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ─────────────────────────────────────────────
# 网页爬虫
# ─────────────────────────────────────────────

class WebCrawler:
    """网页爬虫

    基于 urllib.request，支持自动编码识别、重定向跟随、正文提取。

    Args:
        timeout: 默认超时秒数
        max_redirects: 最大重定向次数
        verify_ssl: 是否验证 SSL 证书
        delay: 请求间隔秒数（礼貌爬取）

    示例:
        crawler = WebCrawler()
        text = crawler.fetch("https://example.com")
        body = crawler.extract_text(text)
        links = crawler.extract_links(text, "https://example.com")
    """

    # 需要移除的 HTML 标签
    _REMOVE_TAGS = [
        "script", "style", "noscript", "iframe", "object",
        "embed", "applet", "form", "nav", "header", "footer",
        "aside", "menu", "menuitem",
    ]

    # 需要移除的 class/id 模式（广告/导航相关）
    _REMOVE_PATTERNS = [
        r'class=["\'][^"\']*(?:ad|advert|banner|sidebar|nav|menu|header|footer|comment|share|social|recommend|related|hot|top|side|widget|popup|modal|overlay|cookie|consent)[^"\']*["\']',
        r'id=["\'][^"\']*(?:ad|advert|banner|sidebar|nav|menu|header|footer|comment|share|social|recommend|related|hot|top|side|widget|popup|modal|overlay|cookie|consent)[^"\']*["\']',
    ]

    def __init__(
        self,
        timeout: float = 30.0,
        max_redirects: int = 10,
        verify_ssl: bool = True,
        delay: float = 0.0,
    ) -> None:
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._verify_ssl = verify_ssl
        self._delay = delay
        self._last_request_time: float = 0.0

    def fetch(
        self,
        url: str,
        timeout: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET",
        data: Optional[bytes] = None,
    ) -> str:
        """抓取网页内容

        Args:
            url: 目标 URL
            timeout: 超时秒数
            headers: 额外请求头
            method: HTTP 方法
            data: POST 数据

        Returns:
            网页文本（自动编码识别）
        """
        # 礼貌爬取
        if self._delay > 0:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._delay:
                time.sleep(self._delay - elapsed)
        self._last_request_time = time.time()

        req_timeout = timeout or self._timeout

        # 构建请求头
        req_headers = {
            "User-Agent": _random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",  # 不压缩，方便处理
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

        # 重定向处理器
        redirect_handler = _RedirectHandler()

        # SSL 上下文
        ctx = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            opener = urllib.request.build_opener(redirect_handler)
            if ctx:
                opener.add_handler(urllib.request.HTTPSHandler(context=ctx))

            resp = opener.open(req, timeout=req_timeout)
            html_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            final_url = resp.url or redirect_handler.final_url or url

            text = _decode_bytes(html_bytes, content_type)

            return text

        except urllib.error.HTTPError as e:
            logger.error("HTTP 错误 %d: %s", e.code, url)
            try:
                body = e.read()
                return _decode_bytes(body, e.headers.get("Content-Type", ""))
            except Exception:
                return ""
        except urllib.error.URLError as e:
            logger.error("URL 错误: %s - %s", url, e)
            return ""
        except Exception as e:
            logger.error("抓取失败: %s - %s", url, e)
            return ""

    def fetch_binary(
        self,
        url: str,
        timeout: Optional[float] = None,
    ) -> bytes:
        """抓取原始二进制内容

        Args:
            url: 目标 URL
            timeout: 超时秒数

        Returns:
            原始字节
        """
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _random_ua()},
        )
        try:
            ctx = None
            if not self._verify_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            resp = urllib.request.urlopen(req, timeout=timeout or self._timeout, context=ctx)
            return resp.read()
        except Exception as e:
            logger.error("二进制抓取失败: %s - %s", url, e)
            return b""

    def extract_text(self, html_text: str) -> str:
        """从 HTML 中提取正文文本

        去除导航、广告、脚本等无关内容，保留可读正文。

        Args:
            html_text: HTML 文本

        Returns:
            清理后的正文文本
        """
        if not html_text:
            return ""

        text = html_text

        # 1. 移除注释
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

        # 2. 移除指定标签及其内容
        for tag in self._REMOVE_TAGS:
            text = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>",
                "", text, flags=re.DOTALL | re.IGNORECASE,
            )
            # 自闭合标签
            text = re.sub(rf"<{tag}[^>]*/?>", "", text, flags=re.IGNORECASE)

        # 3. 移除广告/导航相关的 div/section/span
        for pattern in self._REMOVE_PATTERNS:
            # 匹配包含这些 class/id 的标签块
            text = re.sub(
                r"<(?:div|section|aside|nav|ul|ol|li|span|p)[^>]*" + pattern + r"[^>]*>.*?</(?:div|section|aside|nav|ul|ol|li|span|p)>",
                "", text, flags=re.DOTALL | re.IGNORECASE,
            )

        # 4. 移除所有 HTML 标签
        text = re.sub(r"<[^>]+>", " ", text)

        # 5. 解码 HTML 实体
        text = html.unescape(text)

        # 6. 清理空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.strip() for line in text.split("\n") if line.strip())

        return text.strip()

    def extract_links(
        self,
        html_text: str,
        base_url: str = "",
    ) -> List[Dict[str, str]]:
        """从 HTML 中提取所有链接

        Args:
            html_text: HTML 文本
            base_url: 基础 URL（用于解析相对链接）

        Returns:
            链接列表 [{url, text, title}]
        """
        if not html_text:
            return []

        links: List[Dict[str, str]] = []
        seen: Set[str] = set()

        # 匹配 <a> 标签
        pattern = re.compile(
            r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in pattern.finditer(html_text):
            href = match.group(1).strip()
            link_text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            link_text = html.unescape(link_text)

            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # 解析相对链接
            if base_url:
                href = urllib.parse.urljoin(base_url, href)

            # 去重
            normalized = href.rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)

            links.append({
                "url": href,
                "text": link_text,
                "title": "",
            })

        return links

    def extract_images(
        self,
        html_text: str,
        base_url: str = "",
    ) -> List[Dict[str, str]]:
        """从 HTML 中提取所有图片

        Args:
            html_text: HTML 文本
            base_url: 基础 URL

        Returns:
            图片列表 [{url, alt, width, height}]
        """
        if not html_text:
            return []

        images: List[Dict[str, str]] = []
        seen: Set[str] = set()

        # <img> 标签
        img_pattern = re.compile(
            r'<img[^>]*src=["\']([^"\']*)["\'][^>]*>',
            re.IGNORECASE,
        )

        for match in img_pattern.finditer(html_text):
            src = match.group(1).strip()
            if not src or src.startswith("data:"):
                continue

            if base_url:
                src = urllib.parse.urljoin(base_url, src)

            if src.rstrip("/") in seen:
                continue
            seen.add(src.rstrip("/"))

            # 提取 alt
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', match.group(0))
            alt = alt_match.group(1) if alt_match else ""

            images.append({
                "url": src,
                "alt": html.unescape(alt),
            })

        return images

    def get_title(self, html_text: str) -> str:
        """提取页面标题

        Args:
            html_text: HTML 文本

        Returns:
            页面标题
        """
        if not html_text:
            return ""

        # <title> 标签
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.DOTALL | re.IGNORECASE)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            return html.unescape(title)

        # <h1> 标签
        match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.DOTALL | re.IGNORECASE)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            return html.unescape(title)

        return ""

    def download(
        self,
        url: str,
        save_path: str,
        timeout: Optional[float] = None,
    ) -> str:
        """下载文件

        Args:
            url: 下载 URL
            save_path: 保存路径
            timeout: 超时

        Returns:
            保存的文件路径
        """
        import os

        req = urllib.request.Request(
            url,
            headers={"User-Agent": _random_ua()},
        )

        ctx = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        resp = urllib.request.urlopen(req, timeout=timeout or self._timeout, context=ctx)
        with open(save_path, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)

        return save_path


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

_default_crawler: Optional[WebCrawler] = None


def fetch(url: str, timeout: float = 30.0) -> str:
    """便捷抓取函数（使用全局实例）

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        网页文本
    """
    global _default_crawler
    if _default_crawler is None:
        _default_crawler = WebCrawler()
    return _default_crawler.fetch(url, timeout=timeout)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    crawler = WebCrawler()

    print("=== 抓取测试 ===")
    text = crawler.fetch("https://www.baidu.com")
    print(f"长度: {len(text)}")
    print(f"标题: {crawler.get_title(text)}")
    print()

    print("=== 正文提取 ===")
    body = crawler.extract_text(text)
    print(body[:500])
    print()

    print("=== 链接提取 ===")
    links = crawler.extract_links(text, "https://www.baidu.com")
    for link in links[:10]:
        print(f"  {link['text']}: {link['url']}")
