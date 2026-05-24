"""
xuanji 搜索引擎

基于 urllib.request 的多引擎搜索工具，零外部依赖。
支持百度、必应、Google、DuckDuckGo 搜索，自动 UA 轮换和反爬处理。

示例:
    search = WebSearch()
    results = search.search("Python 编程", engine="baidu", limit=10)
    for r in results:
        print(f"{r['title']} - {r['url']}")
"""

import html
import json
import logging
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class SearchResult:
    """搜索结果

    Attributes:
        title: 标题
        url: 链接
        snippet: 摘要
        position: 排名位置（从1开始）
        engine: 搜索引擎名称
    """
    title: str = ""
    url: str = ""
    snippet: str = ""
    position: int = 0
    engine: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "position": self.position,
            "engine": self.engine,
        }


# ─────────────────────────────────────────────
# User-Agent 池
# ─────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    """获取随机 User-Agent"""
    return random.choice(USER_AGENTS)


# ─────────────────────────────────────────────
# 搜索引擎基类
# ─────────────────────────────────────────────

class SearchEngine:
    """搜索引擎基类"""

    name: str = ""

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """执行搜索，返回结果列表"""
        raise NotImplementedError


# ─────────────────────────────────────────────
# 百度搜索引擎
# ─────────────────────────────────────────────

class BaiduSearch(SearchEngine):
    """百度搜索引擎"""

    name = "baidu"
    _URL = "https://www.baidu.com/s"

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        params = {"wd": query, "rn": str(limit)}
        url = f"{self._URL}?{urllib.parse.urlencode(params)}"
        body = self._fetch(url)
        if not body:
            return []
        return self._parse(body, limit)

    def _fetch(self, url: str) -> str:
        """获取页面内容"""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                logger.warning("百度搜索被反爬拦截，尝试备用方案")
                return self._fetch_backup(query=url)
            logger.error("百度搜索失败: %s", e)
            return ""
        except Exception as e:
            logger.error("百度搜索请求失败: %s", e)
            return ""

    def _fetch_backup(self, query: str) -> str:
        """备用请求方式"""
        try:
            time.sleep(1)
            req = urllib.request.Request(
                query,
                headers={
                    "User-Agent": _random_ua(),
                    "Accept": "text/html",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": "https://www.baidu.com/",
                    "Cookie": f"BAIDUID=BDC{random.randint(100000, 999999)}:{random.randint(100000, 999999)}",
                },
            )
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
        except Exception as e:
            logger.error("百度备用请求失败: %s", e)
            return ""

    def _parse(self, html_text: str, limit: int) -> List[SearchResult]:
        """解析百度搜索结果"""
        results: List[SearchResult] = []

        # 匹配百度搜索结果: <h3 class="t">...</h3> 或 <a class="c-show-url" ...>
        # 百度新版结构: <div class="result c-container ..."> 包含 <h3><a ...>标题</a></h3>
        # 提取所有搜索结果块
        pattern = re.compile(
            r'<div[^>]*class="[^"]*result[^"]*c-container[^"]*"[^>]*>.*?</div>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = pattern.findall(html_text)

        if not blocks:
            # 备用：匹配 h3 标签
            h3_pattern = re.compile(
                r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h3>',
                re.DOTALL | re.IGNORECASE,
            )
            h3_blocks = h3_pattern.findall(html_text)
            for url, title in h3_blocks:
                title = re.sub(r"<[^>]+>", "", title).strip()
                title = html.unescape(title)
                # 百度链接需要解跳转
                real_url = self._resolve_baidu(url)
                if real_url and title:
                    results.append(SearchResult(
                        title=title,
                        url=real_url,
                        snippet="",
                        position=len(results) + 1,
                        engine=self.name,
                    ))
            return results[:limit]

        for block in blocks:
            # 提取标题和链接
            title_match = re.search(
                r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if not title_match:
                continue

            url = title_match.group(1)
            title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()
            title = html.unescape(title)

            # 提取摘要
            snippet = ""
            snippet_match = re.search(
                r'<span[^>]*class="[^"]*content-right[^"]*"[^>]*>(.*?)</span>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                snippet = html.unescape(snippet)

            real_url = self._resolve_baidu(url)
            if real_url and title:
                results.append(SearchResult(
                    title=title,
                    url=real_url,
                    snippet=snippet[:200],
                    position=len(results) + 1,
                    engine=self.name,
                ))

        return results[:limit]

    def _resolve_baidu(self, url: str) -> str:
        """解析百度跳转链接"""
        if not url:
            return ""
        if url.startswith("http://www.baidu.com") or url.startswith("https://www.baidu.com"):
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if "url" in params:
                return params["url"][0]
            if "qd" in params:
                return params["qd"][0]
            if "wd" in params:
                return params["wd"][0]
        # 直接链接
        if url.startswith(("http://", "https://")):
            return url
        return ""


# ─────────────────────────────────────────────
# 必应搜索引擎
# ─────────────────────────────────────────────

class BingSearch(SearchEngine):
    """必应搜索引擎"""

    name = "bing"
    _URL = "https://www.bing.com/search"

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        params = {"q": query, "count": str(limit)}
        url = f"{self._URL}?{urllib.parse.urlencode(params)}"
        body = self._fetch(url)
        if not body:
            return []
        return self._parse(body, limit)

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
        except Exception as e:
            logger.error("必应搜索请求失败: %s", e)
            return ""

    def _parse(self, html_text: str, limit: int) -> List[SearchResult]:
        """解析必应搜索结果"""
        results: List[SearchResult] = []

        # 必应结果在 <li class="b_algo"> 中
        li_pattern = re.compile(
            r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = li_pattern.findall(html_text)

        if not blocks:
            # 备用：匹配 h2 标签
            h2_pattern = re.compile(
                r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?</h2>',
                re.DOTALL | re.IGNORECASE,
            )
            h2_blocks = h2_pattern.findall(html_text)
            for url, title in h2_blocks:
                title = re.sub(r"<[^>]+>", "", title).strip()
                title = html.unescape(title)
                if url and title:
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet="",
                        position=len(results) + 1,
                        engine=self.name,
                    ))
            return results[:limit]

        for block in blocks:
            # 标题和链接
            link_match = re.search(
                r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if not link_match:
                continue

            url = link_match.group(1)
            title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
            title = html.unescape(title)

            # 摘要
            snippet = ""
            snippet_match = re.search(
                r'<p[^>]*>(.*?)</p>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                snippet = html.unescape(snippet)

            if url and title:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet[:200],
                    position=len(results) + 1,
                    engine=self.name,
                ))

        return results[:limit]


# ─────────────────────────────────────────────
# Google 搜索引擎
# ─────────────────────────────────────────────

class GoogleSearch(SearchEngine):
    """Google 搜索引擎"""

    name = "google"
    _URL = "https://www.google.com/search"

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        params = {
            "q": query,
            "num": str(limit),
            "hl": "zh-CN",
            "gl": "cn",
        }
        url = f"{self._URL}?{urllib.parse.urlencode(params)}"
        body = self._fetch(url)
        if not body:
            return []
        return self._parse(body, limit)

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            data = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
        except Exception as e:
            logger.error("Google 搜索请求失败: %s", e)
            return ""

    def _parse(self, html_text: str, limit: int) -> List[SearchResult]:
        """解析 Google 搜索结果"""
        results: List[SearchResult] = []

        # Google 结果在 <div class="g"> 或 <div data-hveid="..."> 中
        div_pattern = re.compile(
            r'<div[^>]*class="[^"]*g[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = div_pattern.findall(html_text)

        if not blocks:
            # 备用：匹配 <a> 标签中的 /url?q= 格式
            link_pattern = re.compile(
                r'/url\?q=([^"&]+)',
                re.IGNORECASE,
            )
            urls = link_pattern.findall(html_text)
            for url_raw in urls[:limit]:
                url = urllib.parse.unquote(url_raw)
                if url.startswith("http") and "google" not in url:
                    results.append(SearchResult(
                        title="",
                        url=url,
                        snippet="",
                        position=len(results) + 1,
                        engine=self.name,
                    ))
            return results[:limit]

        for block in blocks:
            # 标题和链接
            link_match = re.search(
                r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if not link_match:
                continue

            url = link_match.group(1)
            title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
            title = html.unescape(title)

            # 跳过 Google 自身链接
            if url.startswith("/url") or "google" in url:
                continue

            # 摘要
            snippet = ""
            snippet_match = re.search(
                r'<span[^>]*>.*?</span>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(0)).strip()
                snippet = html.unescape(snippet)

            if url and title:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet[:200],
                    position=len(results) + 1,
                    engine=self.name,
                ))

        return results[:limit]


# ─────────────────────────────────────────────
# DuckDuckGo 搜索引擎
# ─────────────────────────────────────────────

class DuckDuckGoSearch(SearchEngine):
    """DuckDuckGo 搜索引擎"""

    name = "duckduckgo"
    _URL = "https://html.duckduckgo.com/html/"

    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            self._URL,
            data=data,
            headers={
                "User-Agent": _random_ua(),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html_text = body.decode(charset, errors="replace")
        except Exception as e:
            logger.error("DuckDuckGo 搜索请求失败: %s", e)
            return []

        return self._parse(html_text, limit)

    def _parse(self, html_text: str, limit: int) -> List[SearchResult]:
        """解析 DuckDuckGo 搜索结果"""
        results: List[SearchResult] = []

        # DuckDuckGo HTML 版结果在 <div class="result"> 中
        div_pattern = re.compile(
            r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = div_pattern.findall(html_text)

        if not blocks:
            # 备用：匹配 .result__url 和 .result__snippet
            return results[:limit]

        for block in blocks:
            # 标题和链接
            link_match = re.search(
                r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if not link_match:
                # 尝试通用链接
                link_match = re.search(
                    r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    block, re.DOTALL | re.IGNORECASE,
                )

            if not link_match:
                continue

            url = link_match.group(1)
            title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
            title = html.unescape(title)

            # 摘要
            snippet = ""
            snippet_match = re.search(
                r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()
                snippet = html.unescape(snippet)

            if url and title:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet[:200],
                    position=len(results) + 1,
                    engine=self.name,
                ))

        return results[:limit]


# ─────────────────────────────────────────────
# 搜索引擎管理器
# ─────────────────────────────────────────────

class WebSearch:
    """搜索引擎管理器

    支持多引擎搜索，自动 UA 轮换，零外部依赖。

    Args:
        timeout: 默认超时秒数
        default_engine: 默认搜索引擎
        auto_retry: 失败时自动尝试其他引擎

    示例:
        search = WebSearch()
        results = search.search("Python 编程")
        results = search.search("Python 编程", engine="bing")
        results = search.search("Python 编程", engine="google", limit=5)
    """

    ENGINES = {
        "baidu": BaiduSearch,
        "bing": BingSearch,
        "google": GoogleSearch,
        "duckduckgo": DuckDuckGoSearch,
    }

    def __init__(
        self,
        timeout: float = 15.0,
        default_engine: str = "baidu",
        auto_retry: bool = True,
    ) -> None:
        self._timeout = timeout
        self._default_engine = default_engine
        self._auto_retry = auto_retry
        self._engines: Dict[str, SearchEngine] = {}
        for name, cls in self.ENGINES.items():
            self._engines[name] = cls()

    def search(
        self,
        query: str,
        engine: str = "baidu",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """执行搜索

        Args:
            query: 搜索关键词
            engine: 搜索引擎 (baidu/bing/google/duckduckgo)
            limit: 返回结果数量

        Returns:
            搜索结果列表 [{title, url, snippet, position}]
        """
        engine = engine.lower()
        if engine not in self._engines:
            logger.error("不支持的搜索引擎: %s，使用默认引擎 %s", engine, self._default_engine)
            engine = self._default_engine

        eng = self._engines[engine]
        try:
            results = eng.search(query, limit)
            if results:
                return [r.to_dict() for r in results]
        except Exception as e:
            logger.error("引擎 %s 搜索失败: %s", engine, e)

        # 自动重试：尝试其他引擎
        if self._auto_retry:
            for name, eng in self._engines.items():
                if name == engine:
                    continue
                try:
                    results = eng.search(query, limit)
                    if results:
                        logger.info("引擎 %s 失败，回退到 %s 成功", engine, name)
                        return [r.to_dict() for r in results]
                except Exception as e:
                    logger.warning("回退引擎 %s 也失败: %s", name, e)

        logger.warning("所有搜索引擎均未能返回结果")
        return []

    def search_multi(
        self,
        query: str,
        engines: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """多引擎综合搜索

        Args:
            query: 搜索关键词
            engines: 搜索引擎列表，默认全部
            limit: 每个引擎返回数量

        Returns:
            合并后的搜索结果列表
        """
        if engines is None:
            engines = list(self._engines.keys())

        all_results: List[Dict[str, Any]] = []
        seen_urls: set = set()

        for engine in engines:
            if engine not in self._engines:
                continue
            try:
                results = self.search(query, engine=engine, limit=limit)
                for r in results:
                    if r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        all_results.append(r)
            except Exception as e:
                logger.warning("引擎 %s 搜索失败: %s", engine, e)

        return all_results

    def list_engines(self) -> List[str]:
        """列出所有可用引擎"""
        return list(self._engines.keys())


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

_default_search: Optional[WebSearch] = None


def search(
    query: str,
    engine: str = "baidu",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """便捷搜索函数（使用全局实例）

    Args:
        query: 搜索关键词
        engine: 搜索引擎
        limit: 返回结果数量

    Returns:
        搜索结果列表
    """
    global _default_search
    if _default_search is None:
        _default_search = WebSearch()
    return _default_search.search(query, engine=engine, limit=limit)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    s = WebSearch()
    print("可用引擎:", s.list_engines())
    print()

    for engine in s.list_engines():
        print(f"\n=== {engine.upper()} ===")
        results = s.search("Python 教程", engine=engine, limit=5)
        for r in results:
            print(f"  [{r['position']}] {r['title']}")
            print(f"      {r['url']}")
            if r["snippet"]:
                print(f"      {r['snippet'][:80]}...")
            print()
