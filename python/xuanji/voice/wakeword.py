"""
xuanji 唤醒词检测

两种模式：
1. openwakeword (高精度) — 如果安装了openwakeword
2. 简单能量检测 (零依赖) — 音量阈值 + 状态机

状态机：IDLE → LISTENING → THINKING → SPEAKING

零强制依赖。
"""

import enum
import logging
import struct
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("xuanji.voice.wakeword")


# ============================================================
# 状态机
# ============================================================

class WakeWordState(enum.Enum):
    """唤醒词检测器状态"""
    IDLE = "idle"            # 待机 — 等待唤醒词
    LISTENING = "listening"  # 监听中 — 检测到可能的唤醒词，收集更多音频
    THINKING = "thinking"    # 思考中 — 已唤醒，等待处理
    SPEAKING = "speaking"    # 说话中 — TTS正在播放，暂停监听


# ============================================================
# 唤醒词检测器
# ============================================================

class WakeWordDetector:
    """唤醒词检测器
    
    持续监听麦克风（如果可用），检测关键词。
    两种后端自动选择：openwakeword 或 简单能量检测。
    
    能量检测模式说明：
    由于没有真正的关键词识别，能量检测模式只能检测"有人说话"，
    需要配合STT进一步确认是否说了唤醒词。
    """
    
    # 音频参数
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2       # 16-bit
    CHUNK_SIZE = 1024      # 每次读取的样本数
    CHANNELS = 1           # 单声道
    
    # 能量检测参数
    ENERGY_THRESHOLD = 800.0      # RMS能量阈值
    SILENCE_TIMEOUT = 2.0          # 静音超时（秒）
    MIN_SPEECH_DURATION = 0.3      # 最短语音时长（秒）
    
    def __init__(self):
        self._state = WakeWordState.IDLE
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_wake: Optional[Callable] = None
        self._on_state_change: Optional[Callable] = None
        self._keyword = "你好灵明"
        self._lock = threading.Lock()
        self._backend = self._detect_backend()
    
    @property
    def backend(self) -> str:
        """当前后端"""
        return self._backend
    
    @property
    def state(self) -> WakeWordState:
        """当前状态"""
        return self._state
    
    def _detect_backend(self) -> str:
        """检测可用后端"""
        try:
            import openwakeword  # noqa: F401
            logger.info("唤醒词后端: openwakeword")
            return "openwakeword"
        except ImportError:
            pass
        
        logger.info("唤醒词后端: energy（简单能量检测）")
        return "energy"
    
    def _set_state(self, new_state: WakeWordState):
        """状态转换"""
        old_state = self._state
        self._state = new_state
        if old_state != new_state:
            logger.debug(f"唤醒词状态: {old_state.value} → {new_state.value}")
            if self._on_state_change:
                try:
                    self._on_state_change(old_state, new_state)
                except Exception as e:
                    logger.error(f"状态回调异常: {e}")
    
    # ============================================================
    # 启动/停止
    # ============================================================
    
    def start(self, on_wake: Optional[Callable] = None,
              on_state_change: Optional[Callable] = None,
              keyword: str = "你好灵明"):
        """启动唤醒词检测
        
        Args:
            on_wake: 唤醒回调 — 检测到唤醒词时调用
            on_state_change: 状态变化回调 — (old_state, new_state)
            keyword: 唤醒词
        """
        if self._running:
            logger.warning("唤醒词检测已在运行")
            return
        
        self._on_wake = on_wake
        self._on_state_change = on_state_change
        self._keyword = keyword
        self._running = True
        self._set_state(WakeWordState.IDLE)
        
        self._thread = threading.Thread(
            target=self._detection_loop,
            daemon=True,
            name="wakeword-detector",
        )
        self._thread.start()
        logger.info(f"唤醒词检测启动 (后端={self._backend}, 关键词={keyword})")
    
    def stop(self):
        """停止唤醒词检测"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self._set_state(WakeWordState.IDLE)
        logger.info("唤醒词检测已停止")
    
    def set_state(self, state: WakeWordState):
        """外部设置状态（例如TTS开始播放时设为SPEAKING）"""
        with self._lock:
            self._set_state(state)
    
    # ============================================================
    # 检测循环
    # ============================================================
    
    def _detection_loop(self):
        """主检测循环"""
        if self._backend == "openwakeword":
            self._loop_openwakeword()
        else:
            self._loop_energy()
    
    # ============================================================
    # OpenWakeWord后端
    # ============================================================
    
    def _loop_openwakeword(self):
        """使用openwakeword检测"""
        try:
            import openwakeword
            from openwakeword.model import Model
            
            # 加载模型
            oww_model = Model(
                wakeword_models=["hey_jarvis"],  # 使用内置模型
                inference_framework="onnx",
            )
            
            # 尝试打开麦克风
            stream = self._open_microphone()
            if stream is None:
                logger.error("无法打开麦克风，唤醒词检测退出")
                return
            
            while self._running:
                if self._state == WakeWordState.SPEAKING:
                    time.sleep(0.1)
                    continue
                
                audio_chunk = self._read_microphone(stream)
                if audio_chunk is None:
                    time.sleep(0.05)
                    continue
                
                # 运行检测
                prediction = oww_model.predict(audio_chunk)
                
                for model_name, score in prediction.items():
                    if score > 0.5:
                        logger.info(f"唤醒词检测: {model_name} (分数={score:.2f})")
                        self._set_state(WakeWordState.THINKING)
                        if self._on_wake:
                            self._on_wake()
                        break
            
            self._close_microphone(stream)
        
        except Exception as e:
            logger.error(f"openwakeword检测异常: {e}")
            # 降级到能量检测
            logger.info("降级到能量检测模式")
            self._backend = "energy"
            self._loop_energy()
    
    # ============================================================
    # 能量检测后端（零依赖）
    # ============================================================
    
    def _loop_energy(self):
        """简单能量检测
        
        原理：
        1. IDLE状态持续采样（如果有麦克风）或模拟等待
        2. 检测到高能量 → LISTENING
        3. 持续高能量超过MIN_SPEECH_DURATION → THINKING（触发回调）
        4. 静音超过SILENCE_TIMEOUT → 回到IDLE
        """
        stream = self._open_microphone()
        
        if stream is None:
            # 没有麦克风，进入模拟模式（仅状态机可用）
            logger.warning("无法打开麦克风，唤醒词检测进入模拟模式")
            while self._running:
                time.sleep(0.5)
            return
        
        speech_start: Optional[float] = None
        last_speech: float = 0.0
        
        while self._running:
            # SPEAKING状态暂停监听
            if self._state == WakeWordState.SPEAKING:
                time.sleep(0.1)
                continue
            
            audio_chunk = self._read_microphone(stream)
            if audio_chunk is None:
                time.sleep(0.05)
                continue
            
            rms = self._compute_rms(audio_chunk)
            now = time.monotonic()
            
            if rms >= self.ENERGY_THRESHOLD:
                last_speech = now
                
                if speech_start is None:
                    speech_start = now
                    self._set_state(WakeWordState.LISTENING)
                
                # 持续说话超过阈值 → 唤醒
                if (now - speech_start) >= self.MIN_SPEECH_DURATION:
                    if self._state != WakeWordState.THINKING:
                        self._set_state(WakeWordState.THINKING)
                        logger.info("能量检测: 检测到语音活动")
                        if self._on_wake:
                            self._on_wake()
                        # 唤醒后等待一段时间再重新检测
                        speech_start = None
                        time.sleep(2.0)
            else:
                # 静音
                if speech_start is not None:
                    if (now - last_speech) >= self.SILENCE_TIMEOUT:
                        speech_start = None
                        self._set_state(WakeWordState.IDLE)
        
        self._close_microphone(stream)
    
    # ============================================================
    # 麦克风操作（跨平台）
    # ============================================================
    
    def _open_microphone(self):
        """尝试打开麦克风
        
        优先使用pyaudio，不可用则尝试sounddevice。
        都不可用返回None。
        """
        # 尝试pyaudio
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self.CHANNELS,
                rate=self.SAMPLE_RATE,
                input=True,
                frames_per_buffer=self.CHUNK_SIZE,
            )
            logger.info("麦克风已打开 (pyaudio)")
            return ("pyaudio", pa, stream)
        except Exception:
            pass
        
        # 尝试sounddevice
        try:
            import sounddevice as sd
            import queue
            
            audio_queue = queue.Queue()
            
            def callback(indata, frames, time_info, status):
                audio_queue.put(indata.copy())
            
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="int16",
                blocksize=self.CHUNK_SIZE,
                callback=callback,
            )
            stream.start()
            logger.info("麦克风已打开 (sounddevice)")
            return ("sounddevice", stream, audio_queue)
        except Exception:
            pass
        
        return None
    
    def _read_microphone(self, stream_info) -> Optional[bytes]:
        """从麦克风读取一帧音频"""
        try:
            backend = stream_info[0]
            
            if backend == "pyaudio":
                _, _, stream = stream_info
                data = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                return data
            
            elif backend == "sounddevice":
                _, _, audio_queue = stream_info
                try:
                    data = audio_queue.get(timeout=0.1)
                    return data.tobytes()
                except Exception:
                    return None
        
        except Exception as e:
            logger.debug(f"麦克风读取异常: {e}")
            return None
    
    def _close_microphone(self, stream_info):
        """关闭麦克风"""
        try:
            backend = stream_info[0]
            
            if backend == "pyaudio":
                _, pa, stream = stream_info
                stream.stop_stream()
                stream.close()
                pa.terminate()
            
            elif backend == "sounddevice":
                _, stream, _ = stream_info
                stream.stop()
                stream.close()
        
        except Exception as e:
            logger.debug(f"关闭麦克风异常: {e}")
    
    # ============================================================
    # 音频计算
    # ============================================================
    
    @staticmethod
    def _compute_rms(audio_bytes: bytes) -> float:
        """计算音频RMS能量"""
        if len(audio_bytes) < 2:
            return 0.0
        
        n_samples = len(audio_bytes) // 2
        fmt = f"<{n_samples}h"
        try:
            samples = struct.unpack(fmt, audio_bytes[:n_samples * 2])
        except struct.error:
            return 0.0
        
        if not samples:
            return 0.0
        
        sum_sq = sum(s * s for s in samples)
        return (sum_sq / len(samples)) ** 0.5
    
    # ============================================================
    # 外部注入音频（用于测试或非麦克风场景）
    # ============================================================
    
    def feed_audio(self, audio_bytes: bytes) -> bool:
        """手动注入音频数据进行检测
        
        用于测试或从其他来源获取音频的场景。
        
        Args:
            audio_bytes: 16-bit PCM音频数据
        
        Returns:
            是否触发了唤醒
        """
        if self._state == WakeWordState.SPEAKING:
            return False
        
        rms = self._compute_rms(audio_bytes)
        
        if rms >= self.ENERGY_THRESHOLD:
            self._set_state(WakeWordState.THINKING)
            if self._on_wake:
                self._on_wake()
            return True
        
        return False
    
    def __repr__(self):
        return (
            f"<WakeWordDetector backend={self._backend} "
            f"state={self._state.value} "
            f"running={self._running}>"
        )
