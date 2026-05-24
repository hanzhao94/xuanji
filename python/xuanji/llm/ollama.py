"""
Ollama 本地模型适配器

连接本地 Ollama API，支持：
- 自动扫描可用模型
- chat 和 generate 两种接口
- 流式响应

零外部依赖。
"""

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, AsyncIterator, Dict, List, Optional

from ._base import BaseLLMAdapter, ChatResponse, LLMError, ModelNotFoundError


class OllamaAdapter(BaseLLMAdapter):
    """Ollama本地模型适配器
    
    通过Ollama HTTP API与本地模型交互。
    支持chat和generate两种模式。
    """
    
    def __init__(self, name: str, config: Dict[str, Any], **kwargs):
        # Ollama一般不需要很长超时，但大模型可能慢
        kwargs.setdefault("timeout", 300.0)
        kwargs.setdefault("max_retries", 1)  # 本地服务重试少一些
        super().__init__(name, config, **kwargs)
        
        self.base_url = config.get("base_url", "http://localhost:11434").rstrip("/")
        self.model = config.get("model") or config.get("default_model", "")
        
        # 可用模型列表（ping时填充）
        self._models: List[str] = []
    
    def _do_request(self, url: str, data: Optional[bytes] = None,
                    method: str = "POST", stream: bool = False):
        """同步HTTP请求"""
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            if stream:
                return resp
            body = resp.read().decode("utf-8")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            if status == 404:
                raise ModelNotFoundError(
                    f"[{self.name}] Model not found: {self.model}. "
                    f"Available: {', '.join(self._models) or 'unknown'}. "
                    f"Try: ollama pull <model>"
                )
            raise LLMError(f"[{self.name}] Ollama HTTP {status}: {err_body[:500]}")
        except urllib.error.URLError as e:
            raise LLMError(
                f"[{self.name}] Cannot connect to Ollama at {self.base_url}. "
                f"Is Ollama running? Error: {e.reason}"
            )
    
    async def scan_models(self) -> List[str]:
        """扫描Ollama已安装的模型"""
        loop = asyncio.get_running_loop()
        try:
            url = f"{self.base_url}/api/tags"
            result = await loop.run_in_executor(
                None, self._do_request, url, None, "GET", False
            )
            models = [m["name"] for m in result.get("models", [])]
            self._models = models
            
            # 如果配置是auto，选第一个可用模型
            if self.model == "auto" and models:
                self.model = models[0]
            
            return models
        except Exception as e:
            self._models = []
            raise LLMError(f"[{self.name}] Failed to scan models: {e}")
    
    # === 核心实现 ===
    
    async def _do_chat(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """通过Ollama /api/chat接口对话（纯字符串返回，兼容旧接口）"""
        resp = await self._do_chat_response(messages, **kwargs)
        return resp.content
    
    async def _do_chat_response(self, messages: List[Dict], **kwargs) -> ChatResponse:
        """通过Ollama /api/chat接口对话，返回ChatResponse（含thinking）"""
        url = f"{self.base_url}/api/chat"
        
        body = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": messages,
            "stream": False,
        }
        
        # 可选参数
        options = {}
        for key in ("temperature", "top_p", "top_k", "seed", "num_predict"):
            if key in kwargs and kwargs[key] is not None:
                options[key] = kwargs[key]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs.pop("max_tokens")
        
        # 思考模型（qwen3.6等）需要更大num_predict
        # thinking过程本身消耗大量token，需要给实际输出留空间
        model_name = body.get("model", "")
        thinking_models = ["qwen3", "qwq", "deepseek-r1", "deepseek-r1-distill"]
        is_thinking_model = any(m in model_name for m in thinking_models)
        if is_thinking_model and "num_predict" not in options:
            options["num_predict"] = 8192  # 思考模型默认8192
        
        if options:
            body["options"] = options
        
        # format参数（json模式）
        if "format" in kwargs:
            body["format"] = kwargs["format"]
        
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._do_request, url, data, "POST", False
        )
        
        try:
            msg = result.get("message", {})
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            
            usage = {}
            if "prompt_eval_count" in result:
                usage["prompt_tokens"] = result.get("prompt_eval_count", 0)
                usage["completion_tokens"] = result.get("eval_count", 0)
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            
            # 思考模型：thinking有内容但content为空 → 提取最终答案
            if thinking and not content:
                import re
                code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)```', thinking, re.DOTALL)
                if code_blocks:
                    content = "\n\n".join(code_blocks)
                else:
                    paragraphs = thinking.split("\n\n")
                    content = "\n\n".join(paragraphs[-3:]) if len(paragraphs) > 3 else thinking
            elif thinking and content:
                content = thinking + "\n" + content
            
            return ChatResponse(
                content=content,
                thinking=thinking,
                model=result.get("model", self.model),
                usage=usage,
                raw=result,
                finish_reason=result.get("done_reason", ""),
            )
        except (KeyError, TypeError) as e:
            raise LLMError(f"[{self.name}] Failed to parse Ollama response: {e}")
    
    async def _do_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        url = f"{self.base_url}/api/chat"
        body = {
            "model": kwargs.pop("model", None) or self.model,
            "messages": messages,
            "stream": True,
        }
        
        options = {}
        for key in ("temperature", "top_p", "top_k", "seed", "num_predict"):
            if key in kwargs and kwargs[key] is not None:
                options[key] = kwargs[key]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if options:
            body["options"] = options
        
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, self._do_request, url, data, "POST", True
        )
        
        try:
            async for chunk in self._iter_ndjson(resp):
                yield chunk
        finally:
            resp.close()
    
    async def _iter_ndjson(self, resp) -> AsyncIterator[str]:
        """解析Ollama的NDJSON流（每行一个JSON对象）"""
        loop = asyncio.get_running_loop()
        buffer = b""
        
        while True:
            raw = await loop.run_in_executor(None, resp.read, 4096)
            if not raw:
                break
            buffer += raw
            
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                    # chat接口的流式格式
                    msg = data.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        yield content
                    # 如果done=true，结束
                    if data.get("done", False):
                        return
                except json.JSONDecodeError:
                    continue
    
    async def generate(self, prompt: str, **kwargs) -> str:
        """通过Ollama /api/generate接口生成（非chat模式）
        
        Args:
            prompt: 原始prompt文本
        
        Returns:
            生成的文本
        """
        url = f"{self.base_url}/api/generate"
        body = {
            "model": kwargs.pop("model", None) or self.model,
            "prompt": prompt,
            "stream": False,
        }
        
        options = {}
        for key in ("temperature", "top_p", "top_k", "seed", "num_predict"):
            if key in kwargs and kwargs[key] is not None:
                options[key] = kwargs[key]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if options:
            body["options"] = options
        
        if "system" in kwargs:
            body["system"] = kwargs["system"]
        if "format" in kwargs:
            body["format"] = kwargs["format"]
        
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._do_request, url, data, "POST", False
        )
        
        return result.get("response", "")
    
    async def ping(self) -> bool:
        """探测Ollama可用性 + 扫描模型"""
        try:
            models = await self.scan_models()
            if not models:
                self.available = False
                self.last_error = "No models installed in Ollama"
                return False
            
            # 确认选中的模型存在
            if self.model and self.model not in models:
                # 模型名可能带tag，尝试匹配
                base_name = self.model.split(":")[0]
                matched = [m for m in models if m.startswith(base_name)]
                if matched:
                    self.model = matched[0]
                else:
                    self.available = False
                    self.last_error = (
                        f"Model '{self.model}' not found. "
                        f"Available: {', '.join(models)}"
                    )
                    return False
            
            self.available = True
            self.last_error = None
            return True
        except Exception as e:
            self.available = False
            self.last_error = str(e)
            return False
    
    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        n = len(self._models)
        return f"<Ollama [{status}] {self.name} model={self.model} ({n} models)>"
