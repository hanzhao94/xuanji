"""
智谱GLM适配器

支持：
- GLM-4 / GLM-4-Plus / GLM-4-0520 / GLM-4-Air / GLM-4-Flash
- 思考模式
- 工具调用
- 流式SSE

智谱API特点：
- base_url: https://open.bigmodel.cn/api/paas/v4
- 认证：Bearer token（需先生成token，用API key+timestamp签名）
"""

import asyncio
import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, AsyncIterator, Dict, List, Optional

from ._base import (
    AuthError,
    BaseLLMAdapter,
    ChatResponse,
    LLMError,
    ModelNotFoundError,
    RateLimitError,
)


class ZhipuAdapter(BaseLLMAdapter):
    """智谱GLM适配器
    
    智谱AI的GLM系列模型。
    认证方式：JWT token（用API key签名生成）。
    """
    
    CHAT_MODELS = {
        "glm-4", "glm-4-plus", "glm-4-0520", "glm-4-air", "glm-4-air-0111",
        "glm-4-flash", "glm-4-flashx", "glm-4-long",
    }
    THINKING_MODELS = {"glm-4-plus", "glm-4-0520"}
    VISION_MODELS = {"cogvlm3", "glm-4v"}
    EMBED_MODELS = {"embedding-2", "embedding-3"}
    
    def __init__(self, name: str, config: Dict[str, Any], **kwargs):
        kwargs.setdefault("timeout", 120.0)
        kwargs.setdefault("max_retries", 3)
        kwargs.setdefault("base_delay", 2.0)
        super().__init__(name, config, **kwargs)
        
        self.base_url = config.get("base_url", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model") or config.get("default_model", "glm-4-plus")
        self.max_tokens = config.get("max_tokens", 4096)
        
        self._ssl_ctx = ssl.create_default_context()
        self._token_cache: Optional[str] = None
        self._token_expire: float = 0
    
    def _generate_token(self) -> str:
        """生成JWT token（智谱认证方式）
        
        格式：header.payload.signature
        用HS256签名。
        """
        now = time.time()
        if self._token_cache and now < self._token_expire - 60:
            return self._token_cache
        
        # 解析API key：格式为 "id.secret"
        parts = self.api_key.split(".", 1)
        if len(parts) != 2:
            # 直接当token用
            self._token_cache = self.api_key
            self._token_expire = now + 3600
            return self.api_key
        
        api_id, api_secret = parts
        
        # 构建JWT
        header = json.dumps({"alg": "HS256", "sign_type": "SIGN"}, separators=(',', ':'))
        payload = json.dumps({
            "api_key": api_id,
            "exp": int(now) + 3600,
            "timestamp": int(now * 1000),
        }, separators=(',', ':'))
        
        def b64encode(data: bytes) -> str:
            import base64
            return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')
        
        header_b64 = b64encode(header.encode())
        payload_b64 = b64encode(payload.encode())
        signature = hmac.new(
            api_secret.encode(),
            f"{header_b64}.{payload_b64}".encode(),
            hashlib.sha256,
        ).digest()
        sig_b64 = b64encode(signature)
        
        self._token_cache = f"{header_b64}.{payload_b64}.{sig_b64}"
        self._token_expire = now + 3600
        return self._token_cache
    
    def _build_headers(self) -> Dict[str, str]:
        token = self._generate_token()
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
    
    def _build_body(self, messages: List[Dict], stream: bool = False, **kwargs) -> Dict:
        body = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": messages,
            "stream": stream,
        }
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs["max_tokens"]
        elif self.max_tokens:
            body["max_tokens"] = self.max_tokens
        
        for key in ("temperature", "top_p", "top_k", "stop",
                     "presence_penalty", "frequency_penalty", "seed",
                     "tools", "tool_choice"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        
        return body
    
    def _do_request(self, url: str, data: bytes, headers: Dict, stream: bool = False):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=self.timeout)
            if stream:
                return resp
            body = resp.read().decode("utf-8")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
        except urllib.error.URLError as e:
            raise LLMError(f"[{self.name}] Connection error: {e.reason}")
    
    def _handle_http_error(self, e: urllib.error.HTTPError):
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            err_msg = err_json.get("error", {}).get("message", "") or err_json.get("message", "") or err_body[:500]
        except Exception:
            err_msg = str(e)
        
        status = e.code
        if status in (401, 403):
            raise AuthError(f"[{self.name}] Auth failed ({status}): {err_msg}")
        elif status == 404:
            raise ModelNotFoundError(f"[{self.name}] Model not found: {self.model}")
        elif status == 429:
            retry_after = 0.0
            ra = e.headers.get("Retry-After")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    pass
            raise RateLimitError(f"[{self.name}] Rate limited (429): {err_msg}", retry_after=retry_after)
        elif status >= 500:
            raise LLMError(f"[{self.name}] Server error ({status}): {err_msg}")
        else:
            raise LLMError(f"[{self.name}] HTTP {status}: {err_msg}")
    
    async def _do_chat(self, messages: List[Dict], **kwargs) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._do_request, url, data, headers, False)
        return self._parse_text(result)
    
    async def _do_chat_response(self, messages: List[Dict], **kwargs) -> ChatResponse:
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._do_request, url, data, headers, False)
        return self._parse_response_obj(result)
    
    def _parse_text(self, result: dict) -> str:
        """解析响应，返回纯文本"""
        choices = result.get("choices", [])
        if not choices:
            raise LLMError(f"[{self.name}] Empty response")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("reasoning_content", "")
        usage = result.get("usage", {})
        if usage:
            self._total_tokens += usage.get("total_tokens", 0)
        if thinking and content:
            return thinking + "\n" + content
        return thinking if thinking else content
    
    def _parse_response_obj(self, result: dict) -> ChatResponse:
        """解析响应，返回ChatResponse"""
        choices = result.get("choices", [])
        if not choices:
            raise LLMError(f"[{self.name}] Empty response")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("reasoning_content", "")
        usage = result.get("usage", {})
        if usage:
            self._total_tokens += usage.get("total_tokens", 0)
        return ChatResponse(
            content=content,
            thinking=thinking,
            model=result.get("model", self.model),
            usage=usage,
            raw=result,
            finish_reason=choices[0].get("finish_reason", ""),
        )
    
    async def _do_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        headers["Accept"] = "text/event-stream"
        body = self._build_body(messages, stream=True, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, self._do_request, url, data, headers, True)
        
        try:
            async for chunk in self._iter_sse(resp):
                yield chunk
        finally:
            resp.close()
    
    async def _iter_sse(self, resp) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        buffer = b""
        while True:
            raw = await loop.run_in_executor(None, resp.read, 4096)
            if not raw:
                break
            buffer += raw
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    return
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                except json.JSONDecodeError:
                    continue
    
    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(
                self._do_chat([{"role": "user", "content": "ping"}], max_tokens=5, temperature=0),
                timeout=min(self.timeout, 30),
            )
            self.available = True
            self.last_error = None
            return True
        except Exception as e:
            self.available = False
            self.last_error = str(e)
            return False
    
    async def embed(self, text: str, model: Optional[str] = None) -> List[float]:
        url = f"{self.base_url}/embeddings"
        headers = self._build_headers()
        body = {"model": model or self.config.get("embed_model", "embedding-3"), "input": text}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._do_request, url, data, headers, False)
            return result["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"[{self.name}] Embedding failed: {e}")
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        return f"<Zhipu [{status}] {self.model}>"
