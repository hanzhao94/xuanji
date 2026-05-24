"""
阶跃星辰适配器

支持：
- step-1（1T参数大模型）
- step-1-8k / step-1-32k / step-1-128k
- step-2（新一代模型）
- 流式SSE

阶跃星辰API特点：
- base_url: https://api.stepfun.com/v1
- OpenAI兼容格式
"""

import asyncio
import json
import ssl
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


class StepfunAdapter(BaseLLMAdapter):
    """阶跃星辰适配器"""
    
    CHAT_MODELS = {"step-1", "step-1-8k", "step-1-32k", "step-1-128k", "step-2-16k", "step-2-32k"}
    
    def __init__(self, name: str, config: Dict[str, Any], **kwargs):
        kwargs.setdefault("timeout", 120.0)
        kwargs.setdefault("max_retries", 3)
        kwargs.setdefault("base_delay", 2.0)
        super().__init__(name, config, **kwargs)
        
        self.base_url = config.get("base_url", "https://api.stepfun.com/v1").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model") or config.get("default_model", "step-2-16k")
        self.max_tokens = config.get("max_tokens", 4096)
        
        self._ssl_ctx = ssl.create_default_context()
    
    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
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
                     "presence_penalty", "frequency_penalty", "seed"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        
        return body
    
    def _do_request(self, url: str, data: bytes, headers: Dict, stream: bool = False):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=self.timeout)
            if stream:
                return resp
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
        except urllib.error.URLError as e:
            raise LLMError(f"[{self.name}] Connection error: {e.reason}")
    
    def _handle_http_error(self, e: urllib.error.HTTPError):
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            err_msg = err_json.get("error", {}).get("message", "") or err_body[:500]
        except Exception:
            err_msg = str(e)
        
        status = e.code
        if status in (401, 403):
            raise AuthError(f"[{self.name}] Auth failed ({status}): {err_msg}")
        elif status == 404:
            raise ModelNotFoundError(f"[{self.name}] Model not found: {self.model}")
        elif status == 429:
            raise RateLimitError(f"[{self.name}] Rate limited (429): {err_msg}", retry_after=2.0)
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
        
        choices = result.get("choices", [])
        if not choices:
            raise LLMError(f"[{self.name}] Empty response")
        
        content = choices[0].get("message", {}).get("content", "")
        usage = result.get("usage", {})
        if usage:
            self._total_tokens += usage.get("total_tokens", 0)
        
        return content
    
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
        raise NotImplementedError(f"[{self.name}] Stepfun does not support embedding")
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        return f"<Stepfun [{status}] {self.model}>"
