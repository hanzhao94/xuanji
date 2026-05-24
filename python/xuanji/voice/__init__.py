"""
xuanji 语音系统

提供语音转文字(STT)、文字转语音(TTS)、唤醒词检测三大能力。
零强制依赖，用subprocess/ctypes调系统能力，可选依赖自动增强。

用法:
    from xuanji.voice import VoiceEngine
    
    engine = VoiceEngine()
    
    # 语音转文字
    text = engine.transcribe(audio_bytes)
    
    # 文字转语音
    audio = engine.speak("你好世界")
    
    # 唤醒词检测
    engine.start_wakeword(on_wake=lambda: print("唤醒!"))
"""

from xuanji.voice.stt import SpeechToText, detect_speech
from xuanji.voice.tts import TextToSpeech
from xuanji.voice.wakeword import WakeWordDetector, WakeWordState

__all__ = [
    "VoiceEngine",
    "SpeechToText",
    "TextToSpeech",
    "WakeWordDetector",
    "WakeWordState",
    "detect_speech",
]


class VoiceEngine:
    """语音引擎 — 统一封装STT/TTS/唤醒词
    
    自动检测可用后端，零配置即可使用。
    """
    
    def __init__(self):
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self.wakeword = WakeWordDetector()
    
    def transcribe(self, audio_bytes: bytes) -> str:
        """语音转文字
        
        Args:
            audio_bytes: 音频数据（WAV/PCM格式）
        
        Returns:
            识别出的文本
        """
        return self.stt.transcribe(audio_bytes)
    
    def speak(self, text: str) -> bytes:
        """文字转语音
        
        Args:
            text: 要朗读的文本
        
        Returns:
            音频数据（WAV格式），某些后端可能直接播放并返回空bytes
        """
        return self.tts.speak(text)
    
    def has_speech(self, audio_bytes: bytes) -> bool:
        """检测音频中是否有人声
        
        Args:
            audio_bytes: 音频数据
        
        Returns:
            是否检测到人声
        """
        return detect_speech(audio_bytes)
    
    def start_wakeword(self, on_wake=None, keyword: str = "你好灵明"):
        """启动唤醒词检测
        
        Args:
            on_wake: 唤醒回调函数
            keyword: 唤醒词（默认"你好灵明"）
        """
        self.wakeword.start(on_wake=on_wake, keyword=keyword)
    
    def stop_wakeword(self):
        """停止唤醒词检测"""
        self.wakeword.stop()
    
    @property
    def backends(self) -> dict:
        """返回各子系统使用的后端"""
        return {
            "stt": self.stt.backend,
            "tts": self.tts.backend,
            "wakeword": self.wakeword.backend,
        }
    
    def __repr__(self):
        b = self.backends
        return (
            f"<VoiceEngine stt={b['stt']} "
            f"tts={b['tts']} wakeword={b['wakeword']}>"
        )
