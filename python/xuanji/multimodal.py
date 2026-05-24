"""
xuanji 多模态支持模块

图片描述、语音转文字、图片prompt生成。
支持GPT-4V/Gemini Vision/Ollama视觉模型。
用urllib.request调API，零外部依赖。
"""

import os
import json
import base64
import mimetypes
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import request, error, parse
from dataclasses import dataclass, field, asdict


# ============================================================
# 配置
# ============================================================

@dataclass
class ModelConfig:
    """模型配置"""
    provider: str = "openai"  # openai / gemini / ollama
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    timeout: int = 60

    def __post_init__(self):
        # 从环境变量读取
        if not self.api_key:
            env_map = {
                "openai": "OPENAI_API_KEY",
                "gemini": "GOOGLE_API_KEY",
            }
            env_key = env_map.get(self.provider, "")
            if env_key:
                self.api_key = os.environ.get(env_key, "")

        if not self.base_url:
            url_map = {
                "openai": "https://api.openai.com/v1",
                "gemini": "https://generativelanguage.googleapis.com/v1beta",
                "ollama": "http://localhost:11434",
            }
            self.base_url = url_map.get(self.provider, "")


# ============================================================
# HTTP工具
# ============================================================

def _http_post(
    url: str,
    data: Dict,
    headers: Optional[Dict] = None,
    timeout: int = 60,
) -> Dict:
    """发送HTTP POST请求"""
    body = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    req = request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e
    except error.URLError as e:
        raise RuntimeError(f"请求失败: {e.reason}") from e


# ============================================================
# 图片工具
# ============================================================

def _image_to_base64(image_path: str) -> Tuple[str, str]:
    """图片转base64

    Returns:
        (base64_data, mime_type)
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"图片不存在: {image_path}")

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/png"

    with open(image_path, "rb") as f:
        data = f.read()

    return base64.b64encode(data).decode("ascii"), mime_type


def _is_url(path: str) -> bool:
    """判断是否为URL"""
    return path.startswith("http://") or path.startswith("https://")


# ============================================================
# 各Provider实现
# ============================================================

def _describe_openai(
    image_path: str,
    prompt: str,
    config: ModelConfig,
) -> str:
    """用OpenAI Vision API描述图片"""
    # 构造image_content
    if _is_url(image_path):
        image_content = {
            "type": "image_url",
            "image_url": {"url": image_path},
        }
    else:
        b64, mime = _image_to_base64(image_path)
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        }

    data = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    image_content,
                ],
            }
        ],
        "max_tokens": 1024,
    }

    url = f"{config.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {config.api_key}"}

    resp = _http_post(url, data, headers=headers, timeout=config.timeout)
    return resp["choices"][0]["message"]["content"]


def _describe_gemini(
    image_path: str,
    prompt: str,
    config: ModelConfig,
) -> str:
    """用Gemini Vision API描述图片"""
    if _is_url(image_path):
        # Gemini需要base64，先下载
        req = request.Request(image_path)
        with request.urlopen(req, timeout=30) as resp:
            img_data = resp.read()
            content_type = resp.headers.get("Content-Type", "image/png")
        b64 = base64.b64encode(img_data).decode("ascii")
        mime = content_type.split(";")[0].strip()
    else:
        b64, mime = _image_to_base64(image_path)

    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": b64,
                        }
                    },
                ]
            }
        ],
    }

    model = config.model or "gemini-1.5-flash"
    url = (
        f"{config.base_url}/models/{model}:generateContent"
        f"?key={config.api_key}"
    )

    resp = _http_post(url, data, timeout=config.timeout)
    return resp["candidates"][0]["content"]["parts"][0]["text"]


def _describe_ollama(
    image_path: str,
    prompt: str,
    config: ModelConfig,
) -> str:
    """用Ollama Vision模型描述图片"""
    if _is_url(image_path):
        req = request.Request(image_path)
        with request.urlopen(req, timeout=30) as resp:
            img_data = resp.read()
        b64 = base64.b64encode(img_data).decode("ascii")
    else:
        b64, _ = _image_to_base64(image_path)

    data = {
        "model": config.model or "llava",
        "prompt": prompt,
        "images": [b64],
        "stream": False,
    }

    url = f"{config.base_url}/api/generate"
    resp = _http_post(url, data, timeout=config.timeout)
    return resp.get("response", "")


# ============================================================
# 多模态引擎
# ============================================================

@dataclass
class MultimodalResult:
    """多模态操作结果"""
    success: bool
    content: str
    provider: str = ""
    model: str = ""
    duration_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


class MultimodalEngine:
    """多模态引擎 — 图片/音频/多模态处理

    用法:
        engine = MultimodalEngine(provider="openai", api_key="<your-api-key>")

        # 描述图片
        desc = engine.describe_image("photo.jpg")

        # 语音转文字
        text = engine.transcribe_audio("speech.wav")

        # 生成图片prompt
        prompt = engine.generate_image_prompt("一只在雪地里的猫")
    """

    # Provider -> 描述函数映射
    _DESCRIBE_FN = {
        "openai": _describe_openai,
        "gemini": _describe_gemini,
        "ollama": _describe_ollama,
    }

    def __init__(
        self,
        provider: str = "openai",
        model: str = "",
        api_key: str = "",
        base_url: str = "",
        timeout: int = 60,
    ):
        default_models = {
            "openai": "gpt-4o",
            "gemini": "gemini-1.5-flash",
            "ollama": "llava",
        }
        self.config = ModelConfig(
            provider=provider,
            model=model or default_models.get(provider, ""),
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._stats = {"describe": 0, "transcribe": 0, "prompt": 0, "errors": 0}

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def describe_image(
        self,
        image_path: str,
        prompt: str = "请详细描述这张图片的内容。",
        language: str = "zh",
    ) -> MultimodalResult:
        """描述图片内容

        Args:
            image_path: 图片路径或URL
            prompt: 描述提示
            language: 输出语言 zh/en

        Returns:
            MultimodalResult
        """
        if language == "en" and "描述" in prompt:
            prompt = "Please describe this image in detail."

        fn = self._DESCRIBE_FN.get(self.config.provider)
        if not fn:
            return MultimodalResult(
                success=False,
                content="",
                error=f"不支持的provider: {self.config.provider}",
            )

        start = time.monotonic()
        try:
            content = fn(image_path, prompt, self.config)
            self._stats["describe"] += 1
            return MultimodalResult(
                success=True,
                content=content,
                provider=self.config.provider,
                model=self.config.model,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            self._stats["errors"] += 1
            return MultimodalResult(
                success=False,
                content="",
                provider=self.config.provider,
                model=self.config.model,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    def transcribe_audio(
        self,
        audio_path: str,
        language: str = "zh",
        model: str = "",
    ) -> MultimodalResult:
        """语音转文字

        目前仅支持OpenAI Whisper API。

        Args:
            audio_path: 音频文件路径
            language: 语言代码
            model: 模型名（默认whisper-1）

        Returns:
            MultimodalResult
        """
        if self.config.provider != "openai":
            return MultimodalResult(
                success=False,
                content="",
                error=f"语音转文字暂只支持OpenAI (当前: {self.config.provider})",
            )

        if not os.path.isfile(audio_path):
            return MultimodalResult(
                success=False,
                content="",
                error=f"音频文件不存在: {audio_path}",
            )

        start = time.monotonic()
        try:
            content = self._whisper_transcribe(audio_path, language, model)
            self._stats["transcribe"] += 1
            return MultimodalResult(
                success=True,
                content=content,
                provider="openai",
                model=model or "whisper-1",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            self._stats["errors"] += 1
            return MultimodalResult(
                success=False,
                content="",
                provider="openai",
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    def generate_image_prompt(
        self,
        description: str,
        style: str = "realistic",
        detail_level: str = "high",
    ) -> MultimodalResult:
        """根据描述生成高质量图片prompt

        Args:
            description: 图片内容描述
            style: 风格（realistic/anime/watercolor/oil_painting/sketch等）
            detail_level: 细节程度（low/medium/high）

        Returns:
            MultimodalResult，content为生成的prompt
        """
        start = time.monotonic()

        # 构造系统prompt
        system_prompt = self._build_prompt_generator_system(style, detail_level)

        try:
            if self.config.provider == "openai":
                content = self._chat_completion(system_prompt, description)
            elif self.config.provider == "gemini":
                content = self._gemini_generate(system_prompt + "\n\n" + description)
            elif self.config.provider == "ollama":
                content = self._ollama_generate(system_prompt, description)
            else:
                return MultimodalResult(
                    success=False, content="",
                    error=f"不支持的provider: {self.config.provider}",
                )

            self._stats["prompt"] += 1
            return MultimodalResult(
                success=True,
                content=content.strip(),
                provider=self.config.provider,
                model=self.config.model,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as e:
            self._stats["errors"] += 1
            return MultimodalResult(
                success=False, content="",
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    # ----------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------

    def _whisper_transcribe(
        self,
        audio_path: str,
        language: str,
        model: str,
    ) -> str:
        """调用Whisper API"""
        import io

        url = f"{self.config.base_url}/audio/transcriptions"
        boundary = "----xuanjiBoundary"

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        filename = os.path.basename(audio_path)
        mime_type, _ = mimetypes.guess_type(audio_path)
        if not mime_type:
            mime_type = "audio/wav"

        # 构造multipart form data
        body = io.BytesIO()

        def write_field(name: str, value: str):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write(f"{value}\r\n".encode())

        def write_file(name: str, fname: str, data: bytes, content_type: str):
            body.write(f"--{boundary}\r\n".encode())
            body.write(
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{fname}"\r\n'.encode()
            )
            body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.write(data)
            body.write(b"\r\n")

        write_file("file", filename, audio_data, mime_type)
        write_field("model", model or "whisper-1")
        if language:
            write_field("language", language)
        body.write(f"--{boundary}--\r\n".encode())

        body_bytes = body.getvalue()
        req = request.Request(url, data=body_bytes, method="POST")
        req.add_header("Authorization", f"Bearer {self.config.api_key}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        with request.urlopen(req, timeout=self.config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("text", "")

    def _chat_completion(self, system: str, user: str) -> str:
        """OpenAI chat completion"""
        data = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 512,
        }
        url = f"{self.config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        resp = _http_post(url, data, headers=headers, timeout=self.config.timeout)
        return resp["choices"][0]["message"]["content"]

    def _gemini_generate(self, text: str) -> str:
        """Gemini text generation"""
        data = {
            "contents": [{"parts": [{"text": text}]}],
        }
        model = self.config.model or "gemini-1.5-flash"
        url = (
            f"{self.config.base_url}/models/{model}:generateContent"
            f"?key={self.config.api_key}"
        )
        resp = _http_post(url, data, timeout=self.config.timeout)
        return resp["candidates"][0]["content"]["parts"][0]["text"]

    def _ollama_generate(self, system: str, user: str) -> str:
        """Ollama generation"""
        data = {
            "model": self.config.model or "llama3",
            "system": system,
            "prompt": user,
            "stream": False,
        }
        url = f"{self.config.base_url}/api/generate"
        resp = _http_post(url, data, timeout=self.config.timeout)
        return resp.get("response", "")

    def _build_prompt_generator_system(self, style: str, detail_level: str) -> str:
        """构建图片prompt生成器的系统提示"""
        style_hints = {
            "realistic": "photorealistic, high detail, natural lighting",
            "anime": "anime style, cel shading, vibrant colors",
            "watercolor": "watercolor painting, soft edges, flowing colors",
            "oil_painting": "oil painting, textured brushstrokes, rich colors",
            "sketch": "pencil sketch, line art, hatching",
            "pixel_art": "pixel art, retro style, 16-bit",
            "3d_render": "3D render, ray tracing, physically based rendering",
            "ink_wash": "Chinese ink wash painting, minimalist, elegant",
        }
        style_hint = style_hints.get(style, style)

        detail_hints = {
            "low": "简洁，重点突出主体",
            "medium": "适中细节，包含环境和氛围",
            "high": "丰富细节，包含光影/材质/氛围/构图",
        }
        detail_hint = detail_hints.get(detail_level, "适中细节")

        return f"""你是一个专业的AI图片prompt工程师。
根据用户的描述，生成高质量的英文图片生成prompt。

风格要求: {style_hint}
细节程度: {detail_hint}

规则:
1. 输出纯英文prompt，不要解释
2. 包含主体描述、环境、光影、构图、风格关键词
3. 使用逗号分隔的关键词格式
4. 按重要性排序（最重要的在前）
5. 适当添加质量提升词（如 masterpiece, best quality 等）"""

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)


# ============================================================
# 便捷函数
# ============================================================

_default_engine: Optional[MultimodalEngine] = None


def get_engine(**kwargs) -> MultimodalEngine:
    """获取/创建默认引擎"""
    global _default_engine
    if _default_engine is None:
        _default_engine = MultimodalEngine(**kwargs)
    return _default_engine


def describe_image(image_path: str, **kwargs) -> str:
    """快速描述图片"""
    result = get_engine().describe_image(image_path, **kwargs)
    if result.success:
        return result.content
    raise RuntimeError(result.error)


def generate_prompt(description: str, **kwargs) -> str:
    """快速生成图片prompt"""
    result = get_engine().generate_image_prompt(description, **kwargs)
    if result.success:
        return result.content
    raise RuntimeError(result.error)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=== MultimodalEngine ===")
    print("支持的provider: openai, gemini, ollama")
    print()

    # 测试prompt生成（不需要API key）
    engine = MultimodalEngine(provider="openai")

    print("=== 测试图片prompt生成（离线模式） ===")
    # 这里只展示构建逻辑，实际调用需要API key
    system = engine._build_prompt_generator_system("anime", "high")
    print(f"  系统prompt预览:\n  {system[:200]}...")

    print("\n=== 测试base64编码 ===")
    # 创建一个小测试图片
    import tempfile
    test_img = os.path.join(tempfile.gettempdir(), "test_mm.png")
    # 最小PNG: 1x1像素
    png_data = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
        b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
        b'\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00'
        b'\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    with open(test_img, "wb") as f:
        f.write(png_data)

    b64, mime = _image_to_base64(test_img)
    print(f"  base64长度: {len(b64)}, mime: {mime}")
    os.unlink(test_img)

    print(f"\n=== 统计 ===\n  {engine.stats}")
