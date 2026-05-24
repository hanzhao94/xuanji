"""
xuanji 网页阅读器

基于 urllib.request 的网页深度阅读工具，零外部依赖。
将网页内容转化为结构化数据（标题/正文/图片/链接/元数据），
支持新闻/博客/文档/论坛等不同类型页面。

示例:
    reader = WebReader()
    article = reader.read("https://example.com/article")
    print(article.title)
    print(article.content)
"""

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xuanji.web_crawler import WebCrawler

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class Article:
    """文章/网页结构化数据

    Attributes:
        title: 页面标题
        content: 正文文本（已清理 HTML）
        summary: 内容摘要
        images: 图片列表
        links: 链接列表
        metadata: 元数据（作者/时间/来源/类型等）
        url: 原始 URL
        html: 原始 HTML（可选）
    """
    title: str = ""
    content: str = ""
    summary: str = ""
    images: List[Dict[str, str]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    html: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "images": self.images,
            "links": self.links,
            "metadata": self.metadata,
            "url": self.url,
        }


# ─────────────────────────────────────────────
# 元数据提取器
# ─────────────────────────────────────────────

class MetaExtractor:
    """网页元数据提取器

    从 HTML 中提取作者、发布时间、来源、标签等元数据。
    """

    # OpenGraph 标签
    _OG_PATTERNS = {
        "og:title": r'<meta[^>]*property\s*=\s*["\']og:title["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "og:description": r'<meta[^>]*property\s*=\s*["\']og:description["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "og:image": r'<meta[^>]*property\s*=\s*["\']og:image["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "og:url": r'<meta[^>]*property\s*=\s*["\']og:url["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "og:type": r'<meta[^>]*property\s*=\s*["\']og:type["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
    }

    # Twitter Card 标签
    _TWITTER_PATTERNS = {
        "twitter:title": r'<meta[^>]*name\s*=\s*["\']twitter:title["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "twitter:description": r'<meta[^>]*name\s*=\s*["\']twitter:description["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "twitter:image": r'<meta[^>]*name\s*=\s*["\']twitter:image["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
    }

    # 通用 meta 标签
    _META_PATTERNS = {
        "description": r'<meta[^>]*name\s*=\s*["\']description["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "keywords": r'<meta[^>]*name\s*=\s*["\']keywords["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "author": r'<meta[^>]*name\s*=\s*["\']author["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "publisher": r'<meta[^>]*name\s*=\s*["\']publisher["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        "copyright": r'<meta[^>]*name\s*=\s*["\']copyright["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
    }

    # 时间模式
    _TIME_PATTERNS = [
        r'<meta[^>]*name\s*=\s*["\']publishdate["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        r'<meta[^>]*name\s*=\s*["\']date["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        r'<meta[^>]*name\s*=\s*["\']article:published_time["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        r'<time[^>]*datetime\s*=\s*["\']([^"\']*)["\']',
        r'class=["\'][^"\']*publish[^"\']*["\'][^>]*>([^<]+)<',
        r'class=["\'][^"\']*date[^"\']*["\'][^>]*>([^<]+)<',
        r'class=["\'][^"\']*time[^"\']*["\'][^>]*>([^<]+)<',
        r'class=["\'][^"\']*created[^"\']*["\'][^>]*>([^<]+)<',
    ]

    # 作者模式
    _AUTHOR_PATTERNS = [
        r'<meta[^>]*name\s*=\s*["\']author["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
        r'class=["\'][^"\']*author[^"\']*["\'][^>]*>([^<]+)<',
        r'rel\s*=\s*["\']author["\'][^>]*>([^<]+)<',
        r'class=["\'][^"\']*writer[^"\']*["\'][^>]*>([^<]+)<',
    ]

    def extract(self, html_text: str) -> Dict[str, str]:
        """提取元数据

        Args:
            html_text: HTML 文本

        Returns:
            元数据字典
        """
        meta: Dict[str, str] = {}

        # OpenGraph
        for key, pattern in self._OG_PATTERNS.items():
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                meta[key] = html.unescape(match.group(1).strip())

        # Twitter Card
        for key, pattern in self._TWITTER_PATTERNS.items():
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                meta[key] = html.unescape(match.group(1).strip())

        # 通用 meta
        for key, pattern in self._META_PATTERNS.items():
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                meta[key] = html.unescape(match.group(1).strip())

        # 时间
        for pattern in self._TIME_PATTERNS:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                meta["publish_time"] = html.unescape(match.group(1).strip())
                break

        # 作者
        for pattern in self._AUTHOR_PATTERNS:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                author = html.unescape(match.group(1).strip())
                if author and author not in meta.get("author", ""):
                    meta["author"] = author
                    break

        # 检测页面类型
        meta["page_type"] = self._detect_type(html_text)

        return meta

    def _detect_type(self, html_text: str) -> str:
        """检测页面类型

        Args:
            html_text: HTML 文本

        Returns:
            页面类型 (news/blog/document/forum/article/page)
        """
        text_lower = html_text.lower()

        # 检查 og:type
        og_match = re.search(
            r'<meta[^>]*property\s*=\s*["\']og:type["\'][^>]*content\s*=\s*["\']([^"\']*)["\']',
            html_text, re.IGNORECASE,
        )
        if og_match:
            og_type = og_match.group(1).strip().lower()
            if og_type in ("article", "news", "blog"):
                return og_type

        # 基于 class/id 模式检测
        if re.search(r'class=["\'][^"\']*(?:article|post|entry|content)[^"\']*["\']', text_lower):
            return "article"
        if re.search(r'class=["\'][^"\']*(?:news|article-list|news-list)[^"\']*["\']', text_lower):
            return "news"
        if re.search(r'class=["\'][^"\']*(?:forum|thread|topic|post)[^"\']*["\']', text_lower):
            return "forum"
        if re.search(r'class=["\'][^"\']*(?:doc|document|wiki|manual)[^"\']*["\']', text_lower):
            return "document"
        if re.search(r'class=["\'][^"\']*(?:blog|post)[^"\']*["\']', text_lower):
            return "blog"

        return "page"


# ─────────────────────────────────────────────
# 内容摘要器
# ─────────────────────────────────────────────

class Summarizer:
    """内容摘要器

    从正文中提取关键句子生成摘要。
    """

    # 重要关键词
    _IMPORTANT_KEYWORDS = [
        "重要", "关键", "核心", "总结", "结论", "主要",
        "首先", "其次", "最后", "总之", "综上所述",
        "因此", "所以", "然而", "但是", "值得注意的是",
        "研究表明", "数据显示", "专家指出", "据报道",
    ]

    def summarize(self, content: str, max_length: int = 200) -> str:
        """生成内容摘要

        Args:
            content: 正文文本
            max_length: 摘要最大长度

        Returns:
            摘要文本
        """
        if not content or len(content) <= max_length:
            return content

        # 按句子分割
        sentences = re.split(r"([。！？\n.!?]+)", content)
        # 合并句子和分隔符
        full_sentences: List[str] = []
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i].strip()
            separator = sentences[i + 1].strip() if i + 1 < len(sentences) else ""
            if sentence:
                full_sentences.append(sentence + separator)

        if not full_sentences:
            return content[:max_length]

        # 评分并排序
        scored: List[tuple] = []
        for i, sentence in enumerate(full_sentences):
            score = self._score_sentence(sentence, i, len(full_sentences))
            scored.append((score, i, sentence))

        # 取最高分的句子
        scored.sort(key=lambda x: x[0], reverse=True)
        top_sentences = scored[:5]
        # 按原始顺序排列
        top_sentences.sort(key=lambda x: x[1])

        # 合并摘要
        summary = " ".join(s[2] for s in top_sentences).strip()
        if len(summary) > max_length:
            summary = summary[:max_length] + "..."

        return summary

    def _score_sentence(
        self,
        sentence: str,
        position: int,
        total: int,
    ) -> float:
        """给句子打分

        Args:
            sentence: 句子文本
            position: 句子位置
            total: 总句子数

        Returns:
            分数
        """
        score = 0.0

        # 位置分：开头和结尾的句子更重要
        if position == 0:
            score += 3.0
        elif position == 1:
            score += 2.0
        elif position == total - 1:
            score += 2.0
        elif position < total * 0.2:
            score += 1.0

        # 关键词分
        for keyword in self._IMPORTANT_KEYWORDS:
            if keyword in sentence:
                score += 1.5

        # 长度分：适中长度的句子更好
        length = len(sentence)
        if 20 <= length <= 100:
            score += 1.0
        elif length > 100:
            score += 0.5

        # 信息密度分（中文字符比例）
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", sentence))
        if chinese_chars > length * 0.5:
            score += 0.5

        return score


# ─────────────────────────────────────────────
# 网页阅读器
# ─────────────────────────────────────────────

class WebReader:
    """网页阅读器

    将网页内容转化为结构化数据，支持深度阅读。
    基于 WebCrawler，零外部依赖。

    Args:
        timeout: 默认超时秒数
        max_content_length: 正文最大长度
        summary_length: 摘要最大长度

    示例:
        reader = WebReader()
        article = reader.read("https://example.com/article")
        print(f"标题: {article.title}")
        print(f"正文: {article.content}")
        print(f"摘要: {article.summary}")
        print(f"作者: {article.metadata.get('author', '')}")
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_content_length: int = 50000,
        summary_length: int = 200,
    ) -> None:
        self._crawler = WebCrawler(timeout=timeout)
        self._meta_extractor = MetaExtractor()
        self._summarizer = Summarizer()
        self._max_content_length = max_content_length
        self._summary_length = summary_length

    def read(
        self,
        url: str,
        timeout: Optional[float] = None,
    ) -> Article:
        """深度阅读网页

        Args:
            url: 目标 URL
            timeout: 超时秒数

        Returns:
            Article 结构化数据
        """
        # 1. 抓取 HTML
        html_text = self._crawler.fetch(url, timeout=timeout)
        if not html_text:
            return Article(url=url)

        # 2. 提取标题
        title = self._crawler.get_title(html_text)

        # 3. 提取正文
        content = self._crawler.extract_text(html_text)
        if len(content) > self._max_content_length:
            content = content[:self._max_content_length] + "\n...(已截断)"

        # 4. 提取摘要
        summary = self._summarizer.summarize(content, self._summary_length)

        # 5. 提取图片
        images = self._crawler.extract_images(html_text, url)

        # 6. 提取链接
        links = self._crawler.extract_links(html_text, url)

        # 7. 提取元数据
        metadata = self._meta_extractor.extract(html_text)

        return Article(
            title=title,
            content=content,
            summary=summary,
            images=images,
            links=links,
            metadata=metadata,
            url=url,
            html=html_text,
        )

    def read_batch(
        self,
        urls: List[str],
        timeout: Optional[float] = None,
    ) -> List[Article]:
        """批量阅读网页

        Args:
            urls: URL 列表
            timeout: 超时秒数

        Returns:
            Article 列表
        """
        articles: List[Article] = []
        for url in urls:
            try:
                article = self.read(url, timeout=timeout)
                articles.append(article)
            except Exception as e:
                logger.error("阅读失败 %s: %s", url, e)
                articles.append(Article(url=url))
        return articles

    def search_and_read(
        self,
        query: str,
        engine: str = "baidu",
        limit: int = 3,
        timeout: Optional[float] = None,
    ) -> List[Article]:
        """搜索并阅读

        先搜索获取结果，然后阅读前 N 个页面。

        Args:
            query: 搜索关键词
            engine: 搜索引擎
            limit: 阅读数量
            timeout: 超时秒数

        Returns:
            Article 列表
        """
        from xuanji.web_search import WebSearch

        search = WebSearch()
        results = search.search(query, engine=engine, limit=limit)

        articles: List[Article] = []
        for result in results:
            try:
                article = self.read(result["url"], timeout=timeout)
                article.metadata["search_query"] = query
                article.metadata["search_engine"] = engine
                article.metadata["search_position"] = str(result.get("position", ""))
                articles.append(article)
            except Exception as e:
                logger.error("阅读搜索结果失败 %s: %s", result["url"], e)

        return articles

    def get_metadata(self, url: str, timeout: Optional[float] = None) -> Dict[str, str]:
        """快速获取网页元数据

        Args:
            url: 目标 URL
            timeout: 超时秒数

        Returns:
            元数据字典
        """
        html_text = self._crawler.fetch(url, timeout=timeout)
        if not html_text:
            return {}
        return self._meta_extractor.extract(html_text)


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

_default_reader: Optional[WebReader] = None


def read(url: str, timeout: float = 30.0) -> Article:
    """便捷阅读函数（使用全局实例）

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        Article 结构化数据
    """
    global _default_reader
    if _default_reader is None:
        _default_reader = WebReader()
    return _default_reader.read(url, timeout=timeout)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    reader = WebReader()

    print("=== 阅读测试 ===")
    article = reader.read("https://www.baidu.com")
    print(f"标题: {article.title}")
    print(f"类型: {article.metadata.get('page_type', 'unknown')}")
    print(f"正文长度: {len(article.content)}")
    print(f"摘要: {article.summary[:100]}")
    print(f"图片数: {len(article.images)}")
    print(f"链接数: {len(article.links)}")
    print(f"元数据: {article.metadata}")
    print()
    print("正文前 300 字符:")
    print(article.content[:300])
