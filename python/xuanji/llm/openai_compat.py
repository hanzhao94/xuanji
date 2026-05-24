"""
OpenAI兼容适配器

一个适配器覆盖所有OpenAI兼容API：
DeepSeek / 通义千问 / 智谱 / 月之暗面 / Groq / OpenRouter / OpenAI 等

零外部依赖：用urllib.request发HTTP请求。
支持流式响应（SSE解析）。
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


class OpenAICompatAdapter(BaseLLMAdapter):
    """OpenAI兼容API的统一适配器
    
    支持所有遵循OpenAI Chat Completions API格式的服务。
    通过config中的auth_header/auth_prefix自动处理不同认证方式。
    """
    
    def __init__(self, name: str, config: Dict[str, Any], **kwargs):
        super().__init__(name, config, **kwargs)
        
        self.base_url = config.get("base_url", "").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model") or config.get("default_model", "")
        self.max_tokens = config.get("max_tokens", 4096)
        
        # 认证
        self.auth_header = config.get("auth_header", "Authorization")
        self.auth_prefix = config.get("auth_prefix", "Bearer")
        
        # SSL上下文（跳过某些环境的证书问题）
        self._ssl_ctx = ssl.create_default_context()
    
    def _build_headers(self) -> Dict[str, str]:
        """构建HTTP请求头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_header and self.api_key:
            if self.auth_prefix:
                headers[self.auth_header] = f"{self.auth_prefix} {self.api_key}"
            else:
                headers[self.auth_header] = self.api_key
        return headers
    
    def _build_body(self, messages: List[Dict], stream: bool = False, **kwargs) -> Dict:
        """构建请求体"""
        body = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": messages,
            "stream": stream,
        }
        # 可选参数
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs["max_tokens"]
        elif self.max_tokens:
            body["max_tokens"] = self.max_tokens
        
        for key in ("temperature", "top_p", "top_k", "stop",
                     "presence_penalty", "frequency_penalty", "seed",
                     "response_format", "tools", "tool_choice"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        
        return body
    
    def _do_request(self, url: str, data: bytes, headers: Dict, stream: bool = False):
        """同步HTTP请求（在线程池中运行）
        
        Returns:
            非流式: 解析后的JSON dict
            流式: HTTPResponse对象（调用方负责迭代和关闭）
        """
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=self.timeout)
            if stream:
                return resp  # 调用方处理流式
            body = resp.read().decode("utf-8")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_msg = (
                    err_json.get("error", {}).get("message", "")
                    or err_json.get("message", "")
                    or err_body[:500]
                )
            except Exception:
                err_msg = str(e)
            
            if status == 401 or status == 403:
                raise AuthError(f"[{self.name}] Auth failed ({status}): {err_msg}")
            elif status == 404:
                raise ModelNotFoundError(
                    f"[{self.name}] Model not found: {self.model}. Error: {err_msg}"
                )
            elif status == 429:
                # 尝试从header中获取retry-after
                retry_after = 0.0
                ra = e.headers.get("Retry-After") or e.headers.get("retry-after")
                if ra:
                    try:
                        retry_after = float(ra)
                    except ValueError:
                        pass
                raise RateLimitError(
                    f"[{self.name}] Rate limited (429): {err_msg}",
                    retry_after=retry_after,
                )
            elif status >= 500:
                raise LLMError(f"[{self.name}] Server error ({status}): {err_msg}")
            else:
                raise LLMError(f"[{self.name}] HTTP {status}: {err_msg}")
        except urllib.error.URLError as e:
            raise LLMError(f"[{self.name}] Connection error: {e.reason}")
    
    # === 核心实现 ===
    
    def _extract_thinking(self, content: str, msg: dict):
        """提取思考内容（兼容多提供商格式）"""
        import re
        if msg.get("reasoning_content"):
            return msg["reasoning_content"]  # DeepSeek-R1
        if msg.get("thinking"):
            return msg["thinking"]  # 部分提供商
        if "<think>" in content and "</think>" in content:
            m = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
            if m:
                return m.group(1).strip()
        return ""
    
    def _clean_thinking_tags(self, content: str) -> str:
        """清理content中的思考标签"""
        import re
        if "<think>" in content:
            content = re.sub(r'<think>.*?</think>\s*', '', content, flags=re.DOTALL).strip()
        return content
    
    def _parse_response_obj(self, result: dict) -> ChatResponse:
        """解析响应，返回ChatResponse（含thinking）"""
        choices = result.get("choices", [])
        if not choices:
            raise LLMError(f"[{self.name}] Empty response: {json.dumps(result, ensure_ascii=False)[:300]}")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        thinking = self._extract_thinking(content, msg)
        if thinking:
            content = self._clean_thinking_tags(content)
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
    
    async def _do_chat(self, messages: List[Dict], **kwargs) -> str:
        """非流式对话"""
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._do_request, url, data, headers, False
        )
        return self._parse_text(result)
    
    async def _do_chat_response(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """非流式对话，返回ChatResponse"""
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        body = self._build_body(messages, stream=False, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._do_request, url, data, headers, False
        )
        return self._parse_response_obj(result)
    
    async def _do_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话（SSE解析）"""
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        # 流式需要接受SSE
        headers["Accept"] = "text/event-stream"
        body = self._build_body(messages, stream=True, **kwargs)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, self._do_request, url, data, headers, True
        )
        
        try:
            # 在线程池中读SSE行，通过队列传给async
            async for chunk in self._iter_sse(resp):
                yield chunk
        finally:
            resp.close()
    
    async def _iter_sse(self, resp) -> AsyncIterator[str]:
        """解析SSE流"""
        loop = asyncio.get_running_loop()
        buffer = b""
        
        while True:
            # 逐块读取
            raw = await loop.run_in_executor(None, resp.read, 4096)
            if not raw:
                break
            buffer += raw
            
            # 按行处理
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.decode("utf-8", errors="replace").strip()
                
                if not line:
                    continue
                if line.startswith("data: "):
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
                        # 跳过无法解析的行
                        continue
    
    async def ping(self) -> bool:
        """探测模型可用性
        
        发一个最小请求测试连通性。
        """
        try:
            result = await asyncio.wait_for(
                self._do_chat(
                    [{"role": "user", "content": "ping"}],
                    max_tokens=5,
                    temperature=0,
                ),
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
        """文本向量化
        
        调用 /embeddings 接口。
        """
        url = f"{self.base_url}/embeddings"
        headers = self._build_headers()
        body = {
            "model": model or self.config.get("embed_model", self.model),
            "input": text,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, self._do_request, url, data, headers, False
            )
            return result["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"[{self.name}] Embedding failed: {e}")
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        return f"<OpenAICompat [{status}] {self.name} model={self.model}>"
