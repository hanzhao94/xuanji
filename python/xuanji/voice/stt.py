"""
xuanji 语音转文字 (Speech-to-Text)

三层降级策略：
1. whisper (最佳) — 如果安装了openai-whisper
2. 系统语音识别 — Windows SAPI / macOS say+dictation
3. 回退空字符串 + 警告

另提供 detect_speech() 纯算法人声检测（音量阈值+过零率）。

零强制依赖。
"""

import logging
import struct
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Optional

logger = logging.getLogger("xuanji.voice.stt")


# ============================================================
# 人声检测（纯算法，零依赖）
# ============================================================

def detect_speech(audio_bytes: bytes,
                  sample_width: int = 2,
                  energy_threshold: float = 500.0,
                  zcr_threshold: float = 0.1) -> bool:
    """检测音频中是否有人声
    
    使用两个指标：
    1. RMS能量 — 超过阈值说明有声音
    2. 过零率 — 人声的过零率在一定范围内
    
    Args:
        audio_bytes: 原始PCM数据（16-bit little-endian）
        sample_width: 每样本字节数（默认2=16bit）
        energy_threshold: 能量阈值（默认500）
        zcr_threshold: 过零率阈值（默认0.1）
    
    Returns:
        是否检测到人声
    """
    if len(audio_bytes) < sample_width * 10:
        return False
    
    # 解析PCM样本
    fmt = f"<{len(audio_bytes) // sample_width}h"
    try:
        samples = struct.unpack(fmt, audio_bytes[:len(audio_bytes) // sample_width * sample_width])
    except struct.error:
        return False
    
    if not samples:
        return False
    
    # 计算RMS能量
    sum_sq = sum(s * s for s in samples)
    rms = (sum_sq / len(samples)) ** 0.5
    
    if rms < energy_threshold:
        return False
    
    # 计算过零率 (Zero Crossing Rate)
    crossings = 0
    for i in range(1, len(samples)):
        if (samples[i] >= 0) != (samples[i - 1] >= 0):
            crossings += 1
    
    zcr = crossings / len(samples)
    
    # 人声过零率一般在0.02-0.2之间
    return zcr >= zcr_threshold * 0.2 and zcr <= 0.5


# ============================================================
# STT引擎
# ============================================================

class SpeechToText:
    """语音转文字引擎
    
    自动检测最佳后端，三层降级。
    """
    
    def __init__(self, model_size: str = "base"):
        """
        Args:
            model_size: whisper模型大小 (tiny/base/small/medium/large)
        """
        self._model_size = model_size
        self._whisper_model = None
        self._backend = self._detect_backend()
    
    @property
    def backend(self) -> str:
        """当前使用的后端"""
        return self._backend
    
    def _detect_backend(self) -> str:
        """检测可用后端"""
        # 尝试whisper
        try:
            import whisper  # noqa: F401
            logger.info("STT后端: whisper")
            return "whisper"
        except ImportError:
            pass
        
        # 尝试系统语音识别
        if sys.platform == "win32":
            logger.info("STT后端: windows_sapi")
            return "windows_sapi"
        elif sys.platform == "darwin":
            logger.info("STT后端: macos_speech")
            return "macos_speech"
        
        # 最终回退
        logger.warning("STT: 无可用后端，将返回空字符串")
        return "none"
    
    def transcribe(self, audio_bytes: bytes) -> str:
        """语音转文字
        
        Args:
            audio_bytes: 音频数据（WAV格式或原始PCM）
        
        Returns:
            识别出的文本，失败返回空字符串
        """
        if self._backend == "whisper":
            return self._transcribe_whisper(audio_bytes)
        elif self._backend == "windows_sapi":
            return self._transcribe_windows(audio_bytes)
        elif self._backend == "macos_speech":
            return self._transcribe_macos(audio_bytes)
        else:
            warnings.warn(
                "没有可用的语音识别后端。"
                "安装 openai-whisper: pip install openai-whisper",
                RuntimeWarning,
                stacklevel=2,
            )
            return ""
    
    # ============================================================
    # Whisper后端
    # ============================================================
    
    def _transcribe_whisper(self, audio_bytes: bytes) -> str:
        """使用whisper进行语音识别"""
        try:
            import whisper
            
            # 懒加载模型
            if self._whisper_model is None:
                logger.info(f"加载whisper模型: {self._model_size}")
                self._whisper_model = whisper.load_model(self._model_size)
            
            # whisper需要文件路径，写入临时文件
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            
            try:
                result = self._whisper_model.transcribe(tmp_path)
                return result.get("text", "").strip()
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        
        except Exception as e:
            logger.error(f"whisper转写失败: {e}")
            # 降级到系统后端
            if sys.platform == "win32":
                return self._transcribe_windows(audio_bytes)
            elif sys.platform == "darwin":
                return self._transcribe_macos(audio_bytes)
            return ""
    
    # ============================================================
    # Windows SAPI后端
    # ============================================================
    
    def _transcribe_windows(self, audio_bytes: bytes) -> str:
        """使用Windows SAPI进行语音识别
        
        通过PowerShell调用System.Speech.Recognition。
        注意：Windows桌面版语音识别质量有限。
        """
        try:
            # 写入临时WAV文件
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            
            # PowerShell脚本调用SAPI
            ps_script = f'''
Add-Type -AssemblyName System.Speech
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$recognizer.SetInputToWaveFile("{tmp_path}")
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$recognizer.LoadGrammar($grammar)
try {{
    $result = $recognizer.Recognize()
    if ($result) {{ Write-Output $result.Text }}
}} catch {{
    Write-Output ""
}} finally {{
    $recognizer.Dispose()
}}
'''
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=30,
            )
            
            Path(tmp_path).unlink(missing_ok=True)
            return result.stdout.strip()
        
        except Exception as e:
            logger.error(f"Windows SAPI转写失败: {e}")
            return ""
    
    # ============================================================
    # macOS后端
    # ============================================================
    
    def _transcribe_macos(self, audio_bytes: bytes) -> str:
        """使用macOS语音识别
        
        macOS没有命令行STT工具，这里用Python调NSSpeechRecognizer。
        实际上macOS命令行STT比较困难，降级警告。
        """
        warnings.warn(
            "macOS命令行语音识别支持有限。"
            "建议安装whisper: pip install openai-whisper",
            RuntimeWarning,
            stacklevel=2,
        )
        return ""
