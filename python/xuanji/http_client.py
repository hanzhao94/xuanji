"""
xuanji HTTP 客户端

基于 urllib.request 的 HTTP 工具，零外部依赖。
支持自动重试、指数退避、Bearer/Basic 认证、令牌桶限速。

示例:
    client = HttpClient(base_url="https://api.example.com")
    client.set_auth(bearer="sk-xxx")

    # GET
    data = client.get("/users", params={"page": "1"})

    # POST JSON
    result = client.post("/users", json_body={"name": "Alice"})

    # 带限速
    client.set_rate_limit(requests_per_second=5)
"""

import base64
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────

@dataclass
class HttpResponse:
    """HTTP 响应

    Attributes:
        status: 状态码
        headers: 响应头
        body: 原始响应体（bytes）
        text: 文本响应体
        json_data: JSON 解析结果（None 如果不是 JSON）
        url: 最终 URL（可能经过重定向）
        elapsed: 请求耗时（秒）
    """
    status: int = 0
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    text: str = ""
    json_data: Any = None
    url: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        """状态码是否表示成功 (2xx)"""
        return 200 <= self.status < 300

    def json(self) -> Any:
        """获取 JSON 数据"""
        if self.json_data is not None:
            return self.json_data
        try:
            self.json_data = json.loads(self.text)
            return self.json_data
        except (json.JSONDecodeError, ValueError):
            return None


@dataclass
class RetryConfig:
    """重试配置

    Attributes:
        max_retries: 最大重试次数
        backoff_factor: 退避因子（秒）
        backoff_max: 最大退避时间（秒）
        retry_on_status: 需要重试的状态码
        retry_on_timeout: 超时是否重试
    """
    max_retries: int = 3
    backoff_factor: float = 1.0
    backoff_max: float = 30.0
    retry_on_status: List[int] = field(default_factory=lambda: [429, 500, 502, 503, 504])
    retry_on_timeout: bool = True


# ─────────────────────────────────────────────
# 令牌桶限速器
# ─────────────────────────────────────────────

class TokenBucket:
    """令牌桶速率限制器

    Args:
        rate: 每秒令牌数
        capacity: 桶容量（突发上限）
    """

    def __init__(self, rate: float = 10.0, capacity: float = 10.0) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """获取令牌，返回需要等待的秒数

        Args:
            tokens: 需要的令牌数

        Returns:
            等待时间（秒），0 表示无需等待
        """
        with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity, self._tokens + elapsed * self._rate
            )
            self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            else:
                deficit = tokens - self._tokens
                wait = deficit / self._rate
                self._tokens = 0
                return wait

    def wait(self, tokens: float = 1.0) -> None:
        """获取令牌，必要时阻塞等待"""
        wait_time = self.acquire(tokens)
        if wait_time > 0:
            time.sleep(wait_time)


# ─────────────────────────────────────────────
# HTTP 客户端
# ─────────────────────────────────────────────

class HttpClient:
    """HTTP 客户端

    基于 urllib.request，支持自动重试、认证、限速。

    Args:
        base_url: 基础 URL（可选）
        timeout: 默认超时秒数
        retry: 重试配置
        headers: 默认请求头
        verify_ssl: 是否验证 SSL 证书
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 30.0,
        retry: Optional[RetryConfig] = None,
        headers: Optional[Dict[str, str]] = None,
        verify_ssl: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry or RetryConfig()
        self._headers: Dict[str, str] = {
            "User-Agent": "xuanji/1.0",
            "Accept": "application/json",
        }
        if headers:
            self._headers.update(headers)
        self._verify_ssl = verify_ssl
        self._rate_limiter: Optional[TokenBucket] = None
        self._auth_header: Optional[str] = None

    # ── 认证 ──

    def set_auth(
        self,
        bearer: Optional[str] = None,
        basic: Optional[Tuple[str, str]] = None,
    ) -> None:
        """设置认证

        Args:
            bearer: Bearer Token
            basic: (用户名, 密码) 元组
        """
        if bearer:
            self._auth_header = f"Bearer {bearer}"
        elif basic:
            username, password = basic
            encoded = base64.b64encode(
                f"{username}:{password}".encode()
            ).decode()
            self._auth_header = f"Basic {encoded}"
        else:
            self._auth_header = None

    def set_rate_limit(
        self,
        requests_per_second: float = 10.0,
        burst: Optional[float] = None,
    ) -> None:
        """设置速率限制

        Args:
            requests_per_second: 每秒最大请求数
            burst: 突发上限（默认等于 rps）
        """
        capacity = burst or requests_per_second
        self._rate_limiter = TokenBucket(requests_per_second, capacity)

    # ── HTTP 方法 ──

    def get(
        self,
        path: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """GET 请求

        Args:
            path: URL 路径（相对于 base_url）或完整 URL
            params: 查询参数
            headers: 额外请求头
            timeout: 超时秒数

        Returns:
            HttpResponse
        """
        return self._request("GET", path, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        path: str,
        json_body: Any = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """POST 请求

        Args:
            path: URL 路径
            json_body: JSON 请求体（自动序列化）
            data: 原始请求体
            params: 查询参数
            headers: 额外请求头
            timeout: 超时秒数

        Returns:
            HttpResponse
        """
        return self._request(
            "POST", path,
            json_body=json_body, data=data,
            params=params, headers=headers, timeout=timeout,
        )

    def put(
        self,
        path: str,
        json_body: Any = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """PUT 请求"""
        return self._request(
            "PUT", path,
            json_body=json_body, data=data,
            params=params, headers=headers, timeout=timeout,
        )

    def delete(
        self,
        path: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """DELETE 请求"""
        return self._request("DELETE", path, params=params, headers=headers, timeout=timeout)

    def patch(
        self,
        path: str,
        json_body: Any = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """PATCH 请求"""
        return self._request(
            "PATCH", path,
            json_body=json_body, data=data,
            params=params, headers=headers, timeout=timeout,
        )

    def head(
        self,
        path: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """HEAD 请求"""
        return self._request("HEAD", path, params=params, headers=headers, timeout=timeout)

    # ── 核心请求方法 ──

    def _request(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        data: Optional[bytes] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """执行 HTTP 请求（含重试逻辑）"""
        # 限速
        if self._rate_limiter:
            self._rate_limiter.wait()

        # 构建 URL
        url = self._build_url(path, params)

        # 构建请求体
        body = None
        content_type = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif data is not None:
            body = data

        # 构建请求头
        req_headers = dict(self._headers)
        if self._auth_header:
            req_headers["Authorization"] = self._auth_header
        if content_type:
            req_headers["Content-Type"] = content_type
        if headers:
            req_headers.update(headers)

        # 超时
        req_timeout = timeout or self._timeout

        # 重试循环
        last_error: Optional[Exception] = None
        for attempt in range(self._retry.max_retries + 1):
            try:
                response = self._do_request(method, url, body, req_headers, req_timeout)

                # 检查是否需要重试
                if response.status in self._retry.retry_on_status and attempt < self._retry.max_retries:
                    # 尊重 Retry-After 头
                    retry_after = response.headers.get("Retry-After", "")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = self._backoff_time(attempt)
                    else:
                        wait = self._backoff_time(attempt)

                    logger.warning(
                        "%s %s → %d，%0.1f秒后重试 (%d/%d)",
                        method, url, response.status, wait, attempt + 1, self._retry.max_retries,
                    )
                    time.sleep(wait)
                    continue

                return response

            except urllib.error.URLError as e:
                last_error = e
                if self._retry.retry_on_timeout and attempt < self._retry.max_retries:
                    wait = self._backoff_time(attempt)
                    logger.warning(
                        "%s %s 失败: %s，%0.1f秒后重试 (%d/%d)",
                        method, url, e, wait, attempt + 1, self._retry.max_retries,
                    )
                    time.sleep(wait)
                else:
                    break

        # 所有重试都失败
        return HttpResponse(
            status=0,
            text=str(last_error) if last_error else "Unknown error",
            url=url,
        )

    def _do_request(
        self,
        method: str,
        url: str,
        body: Optional[bytes],
        headers: Dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        """执行单次 HTTP 请求"""
        start = time.time()

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        # SSL 上下文
        ctx = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            resp_body = resp.read()
            resp_headers = {k: v for k, v in resp.getheaders()}

            elapsed = time.time() - start

            # 解码文本
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = resp_body.decode(charset)
            except (UnicodeDecodeError, LookupError):
                text = resp_body.decode("utf-8", errors="replace")

            # 尝试解析 JSON
            json_data = None
            content_type = resp_headers.get("Content-Type", "")
            if "json" in content_type or "javascript" in content_type:
                try:
                    json_data = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    pass

            return HttpResponse(
                status=resp.status,
                headers=resp_headers,
                body=resp_body,
                text=text,
                json_data=json_data,
                url=resp.url,
                elapsed=elapsed,
            )

        except urllib.error.HTTPError as e:
            elapsed = time.time() - start
            resp_body = b""
            try:
                resp_body = e.read()
            except Exception:
                pass

            text = resp_body.decode("utf-8", errors="replace")

            # 尝试解析 JSON
            json_data = None
            try:
                json_data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass

            resp_headers = {k: v for k, v in e.headers.items()} if e.headers else {}

            return HttpResponse(
                status=e.code,
                headers=resp_headers,
                body=resp_body,
                text=text,
                json_data=json_data,
                url=url,
                elapsed=elapsed,
            )

    def _build_url(self, path: str, params: Optional[Dict[str, str]] = None) -> str:
        """构建完整 URL"""
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = f"{self._base_url}/{path.lstrip('/')}"

        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"

        return url

    def _backoff_time(self, attempt: int) -> float:
        """计算指数退避时间"""
        wait = self._retry.backoff_factor * (2 ** attempt)
        return min(wait, self._retry.backoff_max)

    # ── 便捷方法 ──

    def download(
        self,
        url: str,
        save_path: str,
        chunk_size: int = 8192,
        timeout: Optional[float] = None,
    ) -> str:
        """下载文件

        Args:
            url: 下载 URL
            save_path: 保存路径
            chunk_size: 块大小
            timeout: 超时

        Returns:
            保存的文件路径
        """
        import os

        req = urllib.request.Request(url, headers=dict(self._headers))
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)

        ctx = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        resp = urllib.request.urlopen(req, timeout=timeout or self._timeout, context=ctx)
        with open(save_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)

        return save_path
