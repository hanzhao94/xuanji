"""
百度文心适配器

支持：
- ERNIE-4.0 / ERNIE-4.0-Turbo / ERNIE-4.0-8K
- ERNIE-3.5 / ERNIE-3.5-8K / ERNIE-3.5-128K
- ERNIE-Speed / ERNIE-Lite
- 流式SSE

文心API特点：
- base_url: https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat
- 认证：access_token（用API Key + Secret Key获取）
"""

import asyncio
import json
import ssl
import time
import urllib.error
import urllib.parse
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

# 模型名到API endpoint的映射
MODEL_ENDPOINTS = {
    "ernie-4.0": "completitions_pro",
    "ernie-4.0-turbo": "ernie-4.0-turbo-8k",
    "ernie-4.0-8k": "completitions_pro",
    "ernie-3.5": "completitions",
    "ernie-3.5-8k": "completitions",
    "ernie-3.5-128k": "ernie-3.5-128k",
    "ernie-speed": "ernie-speed",
    "ernie-lite": "ernie-lite-8k",
    "ernie-4.0-0731": "completitions_pro",
    "ernie-3.5-0725": "completitions",
}


class QianfanAdapter(BaseLLMAdapter):
    """百度文心/千帆适配器
    
    认证方式：API Key + Secret Key → access_token。
    支持ERNIE系列模型。
    """
    
    CHAT_MODELS = set(MODEL_ENDPOINTS.keys())
    EMBED_MODELS = {"embedding-v1", "bge-large-zh", "bge-large-en"}
    
    def __init__(self, name: str, config: Dict[str, Any], **kwargs):
        kwargs.setdefault("timeout", 120.0)
        kwargs.setdefault("max_retries", 3)
        kwargs.setdefault("base_delay", 2.0)
        super().__init__(name, config, **kwargs)
        
        self.api_key = config.get("api_key", "")
        self.secret_key = config.get("secret_key", "")
        self.model = config.get("model") or config.get("default_model", "ernie-4.0")
        self.max_tokens = config.get("max_tokens", 4096)
        
        self._ssl_ctx = ssl.create_default_context()
        self._access_token: Optional[str] = None
        self._token_expire: float = 0
    
    def _get_endpoint(self) -> str:
        """获取模型对应的API endpoint"""
        model = self.model.lower().replace("_", "-")
        return MODEL_ENDPOINTS.get(model, "completitions")
    
    def _get_access_token(self) -> str:
        """获取access_token（用API Key + Secret Key）"""
        now = time.time()
        if self._access_token and now < self._token_expire - 60:
            return self._access_token
        
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        })
        
        req = urllib.request.Request(f"{url}?{params}", method="GET")
        try:
            resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
            self._access_token = data["access_token"]
            self._token_expire = now + data.get("expires_in", 2592000)
            return self._access_token
        except Exception as e:
            raise AuthError(f"[{self.name}] Failed to get access token: {e}")
    
    def _build_body(self, messages: List[Dict], stream: bool = False, **kwargs) -> Dict:
        # 转换为文心消息格式
        wenxin_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "assistant":
                role = "assistant"
            elif role == "system":
                role = "user"  # 文心不支持system，合并到第一条user
            wenxin_messages.append({
                "role": role,
                "content": msg.get("content", ""),
            })
        
        body = {
            "messages": wenxin_messages,
            "stream": stream,
        }
        
        if "max_tokens" in kwargs:
            body["max_output_tokens"] = kwargs["max_tokens"]
        elif self.max_tokens:
            body["max_output_tokens"] = self.max_tokens
        
        for key in ("temperature", "top_p", "top_k", "penalty_score"):
            param_key = key
            if key == "temperature":
                param_key = "temperature"
            elif key == "top_p":
                param_key = "top_p"
            elif key == "top_k":
                param_key = "top_k"
            elif key == "penalty_score":
                param_key = "penalty_score"
            if key in kwargs and kwargs[key] is not None:
                body[param_key] = kwargs[key]
        
        return body
    
    def _do_request(self, url: str, data: bytes, stream: bool = False):
        headers = {"Content-Type": "application/json"}
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
            err_msg = err_json.get("error_msg", "") or err_json.get("message", "") or err_body[:500]
            err_code = err_json.get("error_code", 0)
        except Exception:
            err_msg = str(e)
            err_code = 0
        
        status = e.code
        if status in (401, 403) or err_code == 110:
            # Token过期，清除缓存重试
            self._access_token = None
            raise AuthError(f"[{self.name}] Auth failed ({status}/{err_code}): {err_msg}")
        elif status == 429 or err_code == 18:
            raise RateLimitError(f"[{self.name}] Rate limited: {err_msg}", retry_after=2.0)
        elif status >= 500:
            raise LLMError(f"[{self.name}] Server error ({status}): {err_msg}")
        else:
            raise LLMError(f"[{self.name}] HTTP {status} (code={err_code}): {err_msg}")
    
    def _parse_response_obj(self, result: dict):
        """解析响应，返回ChatResponse"""
        from ._base import ChatResponse, LLMError
        choices = result.get("choices", [])
        if not choices:
            raise LLMError(f"[{self.name}] Empty response")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("reasoning_content", msg.get("thinking", ""))
        usage = result.get("usage", {})
        if usage:
            self._total_tokens += usage.get("total_tokens", 0)
        return ChatResponse(
            content=content,
            thinking=thinking,
            model=result.get("model", ""),
            usage=usage,
            raw=result,
            finish_reason=choices[0].get("finish_reason", ""),
        )
    
    def _parse_text(self, result: dict) -> str:
        """解析响应，返回纯文本"""
        resp = self._parse_response_obj(result)
        if resp.thinking and resp.content:
            return resp.thinking + "\n" + resp.content
        return resp.thinking if resp.thinking else resp.content
    
    async def _do_chat(self, messages, **kwargs) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._do_request, url, data, headers, False)
        return self._parse_text(result)
    
    async def _do_chat_response(self, messages, **kwargs):
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._do_request, url, data, headers, False)
        return self._parse_response_obj(result)

    async def _do_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        token = self._get_access_token()
        endpoint = self._get_endpoint()
        url = f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{endpoint}?access_token={token}"
        
        body = self._build_body(messages, stream=True, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, self._do_request, url, data, True)
        
        try:
            async for chunk in self._iter_stream(resp):
                yield chunk
        finally:
            resp.close()
    
    async def _iter_stream(self, resp) -> AsyncIterator[str]:
        """解析文心流式响应（每行一个JSON）"""
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
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("error_code"):
                        raise LLMError(f"[{self.name}] Stream error: {data.get('error_msg', '')}")
                    content = data.get("result", "")
                    if content:
                        yield content
                    if data.get("is_end", False):
                        return
                except json.JSONDecodeError:
                    continue
    
    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(
                self._do_chat([{"role": "user", "content": "ping"}], max_tokens=5),
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
        token = self._get_access_token()
        embed_model = (model or self.config.get("embed_model", "embedding-v1")).lower()
        endpoint = "embedding_v1" if "v1" in embed_model else "bge-large-zh"
        url = f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/embeddings/{endpoint}?access_token={token}"
        
        body = {"input": text}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._do_request, url, data, False)
            return result["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"[{self.name}] Embedding failed: {e}")
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        return f"<Qianfan [{status}] {self.model}>"
