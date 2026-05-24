"""
xuanji 文字转语音 (Text-to-Speech)

多后端降级策略：
1. piper-tts (最佳离线质量) — 如果安装了piper-tts
2. 系统TTS:
   - Windows: ctypes调SAPI COM接口 (SpVoice) 或 subprocess调PowerShell
   - Linux: subprocess调espeak/festival
   - macOS: subprocess调say命令
3. 回退：返回空bytes + 警告

零强制依赖。
"""

import io
import logging
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Optional

logger = logging.getLogger("xuanji.voice.tts")


class TextToSpeech:
    """文字转语音引擎
    
    自动检测最佳后端。
    speak() 返回WAV格式bytes，某些后端可能直接播放并返回空bytes。
    """
    
    def __init__(self, voice: Optional[str] = None, rate: int = 0):
        """
        Args:
            voice: 指定语音名称（后端特定）
            rate: 语速调整（-10到10，0=默认）
        """
        self._voice = voice
        self._rate = rate
        self._backend = self._detect_backend()
    
    @property
    def backend(self) -> str:
        """当前使用的后端"""
        return self._backend
    
    def _detect_backend(self) -> str:
        """检测可用后端"""
        # 1. 尝试piper-tts
        try:
            import piper  # noqa: F401
            logger.info("TTS后端: piper")
            return "piper"
        except ImportError:
            pass
        
        # 2. 系统TTS
        if sys.platform == "win32":
            logger.info("TTS后端: windows_sapi")
            return "windows_sapi"
        elif sys.platform == "darwin":
            if shutil.which("say"):
                logger.info("TTS后端: macos_say")
                return "macos_say"
        elif sys.platform.startswith("linux"):
            if shutil.which("espeak") or shutil.which("espeak-ng"):
                logger.info("TTS后端: espeak")
                return "espeak"
            if shutil.which("festival"):
                logger.info("TTS后端: festival")
                return "festival"
        
        logger.warning("TTS: 无可用后端")
        return "none"
    
    def speak(self, text: str) -> bytes:
        """文字转语音
        
        Args:
            text: 要朗读的文本
        
        Returns:
            音频数据（WAV格式）。
            某些后端直接播放并返回空bytes。
        """
        if not text or not text.strip():
            return b""
        
        if self._backend == "piper":
            return self._speak_piper(text)
        elif self._backend == "windows_sapi":
            return self._speak_windows(text)
        elif self._backend == "macos_say":
            return self._speak_macos(text)
        elif self._backend == "espeak":
            return self._speak_espeak(text)
        elif self._backend == "festival":
            return self._speak_festival(text)
        else:
            warnings.warn(
                "没有可用的TTS后端。"
                "Windows/macOS自带TTS; Linux安装espeak: apt install espeak",
                RuntimeWarning,
                stacklevel=2,
            )
            return b""
    
    # ============================================================
    # Piper后端（高质量离线TTS）
    # ============================================================
    
    def _speak_piper(self, text: str) -> bytes:
        """使用piper-tts"""
        try:
            import piper
            
            # piper需要模型文件，这里尝试使用默认模型
            # 实际使用时需要下载模型
            voice = piper.PiperVoice.load(
                self._voice or "zh_CN-huayan-medium"
            )
            
            audio_stream = io.BytesIO()
            voice.synthesize(text, audio_stream, sentence_silence=0.3)
            return audio_stream.getvalue()
        
        except Exception as e:
            logger.error(f"piper TTS失败: {e}")
            # 降级到系统TTS
            if sys.platform == "win32":
                return self._speak_windows(text)
            elif sys.platform == "darwin":
                return self._speak_macos(text)
            elif sys.platform.startswith("linux"):
                return self._speak_espeak(text)
            return b""
    
    # ============================================================
    # Windows SAPI后端
    # ============================================================
    
    def _speak_windows(self, text: str) -> bytes:
        """使用Windows SAPI进行语音合成
        
        策略1: ctypes调COM接口（直接播放）
        策略2: PowerShell生成WAV文件
        """
        # 先尝试生成WAV文件（更有用）
        try:
            return self._speak_windows_wav(text)
        except Exception:
            pass
        
        # 回退到直接播放
        try:
            return self._speak_windows_direct(text)
        except Exception as e:
            logger.error(f"Windows TTS失败: {e}")
            return b""
    
    def _speak_windows_wav(self, text: str) -> bytes:
        """Windows SAPI生成WAV文件"""
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False
        ) as f:
            tmp_path = f.name
        
        # 转义文本中的特殊字符
        safe_text = text.replace("'", "''").replace('"', '`"')
        
        ps_script = f'''
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile("{tmp_path}")
$synth.Speak("{safe_text}")
$synth.Dispose()
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=30,
        )
        
        tmp = Path(tmp_path)
        if tmp.exists() and tmp.stat().st_size > 44:  # WAV头至少44字节
            try:
                return tmp.read_bytes()
            finally:
                tmp.unlink(missing_ok=True)
        
        tmp.unlink(missing_ok=True)
        raise RuntimeError("WAV文件生成失败")
    
    def _speak_windows_direct(self, text: str) -> bytes:
        """Windows SAPI直接播放（通过PowerShell）"""
        safe_text = text.replace("'", "''")
        
        ps_script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Speak('{safe_text}')
$synth.Dispose()
"""
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=30,
        )
        return b""  # 直接播放，无返回数据
    
    # ============================================================
    # macOS后端
    # ============================================================
    
    def _speak_macos(self, text: str) -> bytes:
        """使用macOS say命令"""
        try:
            # 生成AIFF文件
            with tempfile.NamedTemporaryFile(
                suffix=".aiff", delete=False
            ) as f:
                tmp_path = f.name
            
            cmd = ["say"]
            if self._voice:
                cmd.extend(["-v", self._voice])
            if self._rate:
                cmd.extend(["-r", str(150 + self._rate * 15)])
            cmd.extend(["-o", tmp_path, text])
            
            subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            
            tmp = Path(tmp_path)
            if tmp.exists() and tmp.stat().st_size > 0:
                try:
                    return tmp.read_bytes()
                finally:
                    tmp.unlink(missing_ok=True)
            
            tmp.unlink(missing_ok=True)
            
            # 回退到直接播放
            subprocess.run(
                ["say", text], capture_output=True, timeout=30,
            )
            return b""
        
        except Exception as e:
            logger.error(f"macOS TTS失败: {e}")
            return b""
    
    # ============================================================
    # Linux espeak后端
    # ============================================================
    
    def _speak_espeak(self, text: str) -> bytes:
        """使用espeak/espeak-ng"""
        try:
            # 确定可执行文件
            cmd_name = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
            
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as f:
                tmp_path = f.name
            
            cmd = [cmd_name]
            if self._voice:
                cmd.extend(["-v", self._voice])
            else:
                cmd.extend(["-v", "zh"])  # 默认中文
            if self._rate:
                cmd.extend(["-s", str(175 + self._rate * 15)])
            cmd.extend(["-w", tmp_path, text])
            
            subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            
            tmp = Path(tmp_path)
            if tmp.exists() and tmp.stat().st_size > 44:
                try:
                    return tmp.read_bytes()
                finally:
                    tmp.unlink(missing_ok=True)
            
            tmp.unlink(missing_ok=True)
            return b""
        
        except Exception as e:
            logger.error(f"espeak TTS失败: {e}")
            return b""
    
    # ============================================================
    # Linux festival后端
    # ============================================================
    
    def _speak_festival(self, text: str) -> bytes:
        """使用festival"""
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as f:
                tmp_path = f.name
            
            # festival使用Scheme语法
            safe_text = text.replace('"', '\\"')
            festival_cmd = f'(utt.save.wave (SayText "{safe_text}") "{tmp_path}")'
            
            subprocess.run(
                ["festival", "--batch", f"(begin {festival_cmd})"],
                capture_output=True, timeout=30,
            )
            
            tmp = Path(tmp_path)
            if tmp.exists() and tmp.stat().st_size > 44:
                try:
                    return tmp.read_bytes()
                finally:
                    tmp.unlink(missing_ok=True)
            
            tmp.unlink(missing_ok=True)
            return b""
        
        except Exception as e:
            logger.error(f"festival TTS失败: {e}")
            return b""
