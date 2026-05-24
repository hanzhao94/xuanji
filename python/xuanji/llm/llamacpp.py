"""
llama.cpp Server 适配器

支持 llama-server (llama.cpp) 的 OpenAI兼容API。
llama.cpp server 启动方式：
  llama-server -m model.gguf --port 8080
  
它提供 /v1/chat/completions 等 OpenAI 兼容接口，
所以直接复用 openai_compat 适配器，加上 llama.cpp 特有功能。
"""

import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from xuanji.llm._base import BaseLLMAdapter, LLMError


class LlamaCppAdapter(BaseLLMAdapter):
    """llama.cpp Server 适配器
    
    llama.cpp server 提供 OpenAI 兼容接口，但有额外功能：
    - /health 健康检查
    - /slots 查看slot状态
    - /completion 原生补全接口（非chat）
    - /v1/chat/completions OpenAI兼容chat接口
    """
    
    def __init__(self, base_url: str = "http://localhost:8080",
                 model: str = "auto", **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = "llamacpp"
    
    async def chat(self, messages: List[Dict], **kwargs) -> str:
        """通过 OpenAI 兼容接口对话"""
        url = f"{self.base_url}/v1/chat/completions"
        
        body = {
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2048),
            "stream": False,
        }
        
        if self.model and self.model != "auto":
            body["model"] = self.model
        
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 120)) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise LLMError(f"llama.cpp error {e.code}: {body}")
        except urllib.error.URLError as e:
            raise LLMError(f"llama.cpp connection error: {e.reason}")
    
    async def stream(self, messages: List[Dict], **kwargs):
        """流式对话"""
        url = f"{self.base_url}/v1/chat/completions"
        
        body = {
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 2048),
            "stream": True,
        }
        
        if self.model and self.model != "auto":
            body["model"] = self.model
        
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            resp = urllib.request.urlopen(req, timeout=kwargs.get("timeout", 120))
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            raise LLMError(f"llama.cpp stream error: {e}")
    
    async def completion(self, prompt: str, **kwargs) -> str:
        """原生补全接口（非chat，llama.cpp特有）"""
        url = f"{self.base_url}/completion"
        
        body = {
            "prompt": prompt,
            "n_predict": kwargs.get("max_tokens", 512),
            "temperature": kwargs.get("temperature", 0.7),
            "stop": kwargs.get("stop", []),
            "stream": False,
        }
        
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 120)) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("content", "")
        except Exception as e:
            raise LLMError(f"llama.cpp completion error: {e}")
    
    async def health(self) -> Dict:
        """健康检查"""
        url = f"{self.base_url}/health"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"status": "error"}
    
    async def slots(self) -> List[Dict]:
        """查看slot状态（llama.cpp特有）"""
        url = f"{self.base_url}/slots"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
    
    async def models(self) -> List[str]:
        """获取可用模型列表"""
        url = f"{self.base_url}/v1/models"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return [m["id"] for m in result.get("data", [])]
        except Exception:
            return [self.model] if self.model != "auto" else []
    
    async def is_available(self) -> bool:
        """检查是否可用"""
        h = await self.health()
        return h.get("status") == "ok"
