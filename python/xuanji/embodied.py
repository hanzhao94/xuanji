"""
xuanji 具身协调器

把感知+决策+操控串成一个完整的循环：
  感知 → 决策 → 操控 → 验证

核心设计：
1. 分层感知（省Token）：
   - L0: 变化检测（0 token）— 像素差异/屏幕哈希
   - L1: OCR识别（0 token）— 本地OCR提取文字
   - L2: LLM理解（花token）— 只在必要时调用LLM
   
2. 操控令牌自动管理：
   - 自动向Arbiter申请屏幕/鼠标/键盘等资源
   - 操控完成后自动释放
   - 支持优先级抢占

3. 状态机：
   IDLE → PERCEIVING → DECIDING → ACTING → VERIFYING

零强制依赖。
"""

import asyncio
import enum
import hashlib
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("xuanji.embodied")


# ============================================================
# 状态机
# ============================================================

class LoopState(enum.Enum):
    """具身循环状态"""
    IDLE = "idle"                # 空闲
    PERCEIVING = "perceiving"    # 感知中
    DECIDING = "deciding"        # 决策中
    ACTING = "acting"            # 操控中
    VERIFYING = "verifying"      # 验证中
    ERROR = "error"              # 异常


class PerceptionLevel(enum.IntEnum):
    """感知层级"""
    L0_CHANGE = 0    # 变化检测（0 token）
    L1_OCR = 1       # OCR识别（0 token）
    L2_LLM = 2       # LLM理解（花 token）


# ============================================================
# 感知结果
# ============================================================

@dataclass
class PerceptionResult:
    """感知结果"""
    level: PerceptionLevel
    changed: bool = False         # 是否检测到变化
    screenshot: bytes = b""       # 屏幕截图（原始bytes）
    screen_hash: str = ""         # 屏幕哈希（用于变化检测）
    ocr_text: str = ""            # OCR识别文本
    llm_description: str = ""     # LLM理解描述
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class Action:
    """操控动作"""
    action_type: str              # "click", "type", "key", "scroll", "wait"
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""         # 人类可读描述
    
    def __repr__(self):
        return f"<Action {self.action_type}: {self.description or self.params}>"


@dataclass
class Decision:
    """决策结果"""
    should_act: bool = False       # 是否需要操控
    actions: List[Action] = field(default_factory=list)
    reasoning: str = ""            # 决策理由
    next_perception_level: PerceptionLevel = PerceptionLevel.L0_CHANGE


# ============================================================
# 具身协调器
# ============================================================

class EmbodiedLoop:
    """具身协调器 — 感知→决策→操控→验证 循环
    
    设计原则：
    1. 分层感知省Token — 只在检测到变化时升级感知层级
    2. 操控令牌自动管理 — 透明地与Arbiter交互
    3. 可插拔 — 感知/决策/操控函数都可替换
    
    用法:
        loop = EmbodiedLoop(
            agent_id=1,
            agent_name="agent",
            arbiter=arbiter,
        )
        
        # 注册自定义感知/决策/操控
        loop.set_perceiver(my_perceiver)
        loop.set_decider(my_decider)
        loop.set_actuator(my_actuator)
        
        # 启动循环
        await loop.run()
    """
    
    # 默认循环间隔（秒）
    DEFAULT_INTERVAL = 1.0
    
    # 最大连续空闲次数（超过后降低检查频率）
    MAX_IDLE_COUNT = 10
    
    def __init__(
        self,
        agent_id: int = 1,
        agent_name: str = "agent",
        arbiter: Any = None,
        bus: Any = None,
        interval: float = 0.0,
    ):
        """
        Args:
            agent_id: Agent ID（用于Arbiter资源申请）
            agent_name: Agent名称
            arbiter: ResourceArbiter实例（可选）
            bus: MessageBus实例（可选）
            interval: 循环间隔秒数（0=默认）
        """
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._arbiter = arbiter
        self._bus = bus
        self._interval = interval or self.DEFAULT_INTERVAL
        
        # 状态
        self._state = LoopState.IDLE
        self._running = False
        self._idle_count = 0
        self._last_screen_hash = ""
        self._active_leases: Dict[str, Any] = {}  # resource_name → lease
        
        # 可插拔函数
        self._perceiver: Optional[Callable] = None
        self._decider: Optional[Callable] = None
        self._actuator: Optional[Callable] = None
        self._verifier: Optional[Callable] = None
        
        # 回调
        self._on_state_change: Optional[Callable] = None
        self._on_error: Optional[Callable] = None
        
        # 统计
        self._stats = {
            "cycles": 0,
            "perceptions": {0: 0, 1: 0, 2: 0},  # 各层级感知次数
            "actions": 0,
            "errors": 0,
            "tokens_saved": 0,   # 通过分层感知节省的LLM调用次数
        }
    
    # ============================================================
    # 状态管理
    # ============================================================
    
    @property
    def state(self) -> LoopState:
        return self._state
    
    def _set_state(self, new_state: LoopState):
        old = self._state
        self._state = new_state
        if old != new_state:
            logger.debug(f"具身状态: {old.value} → {new_state.value}")
            if self._on_state_change:
                try:
                    self._on_state_change(old, new_state)
                except Exception as e:
                    logger.error(f"状态回调异常: {e}")
    
    # ============================================================
    # 可插拔函数设置
    # ============================================================
    
    def set_perceiver(self, fn: Callable) -> None:
        """设置感知函数
        
        签名: async (level: PerceptionLevel) -> PerceptionResult
        """
        self._perceiver = fn
    
    def set_decider(self, fn: Callable) -> None:
        """设置决策函数
        
        签名: async (perception: PerceptionResult) -> Decision
        """
        self._decider = fn
    
    def set_actuator(self, fn: Callable) -> None:
        """设置操控函数
        
        签名: async (action: Action) -> bool
        """
        self._actuator = fn
    
    def set_verifier(self, fn: Callable) -> None:
        """设置验证函数
        
        签名: async (actions: List[Action], perception: PerceptionResult) -> bool
        """
        self._verifier = fn
    
    def on_state_change(self, fn: Callable) -> Callable:
        """注册状态变化回调（可作装饰器）"""
        self._on_state_change = fn
        return fn
    
    def on_error(self, fn: Callable) -> Callable:
        """注册错误回调（可作装饰器）"""
        self._on_error = fn
        return fn
    
    # ============================================================
    # 主循环
    # ============================================================
    
    async def run(self, max_cycles: int = 0) -> None:
        """启动具身循环
        
        Args:
            max_cycles: 最大循环次数（0=无限）
        """
        self._running = True
        logger.info(f"具身循环启动: agent={self.agent_name}, interval={self._interval}s")
        
        cycle = 0
        perception_level = PerceptionLevel.L0_CHANGE
        
        while self._running:
            if max_cycles > 0 and cycle >= max_cycles:
                break
            
            try:
                cycle += 1
                self._stats["cycles"] = cycle
                
                # ---- 1. 感知 ----
                self._set_state(LoopState.PERCEIVING)
                perception = await self._perceive(perception_level)
                
                if not perception.changed and perception_level == PerceptionLevel.L0_CHANGE:
                    # 无变化，保持IDLE
                    self._set_state(LoopState.IDLE)
                    self._idle_count += 1
                    self._stats["tokens_saved"] += 1
                    
                    # 自适应间隔 — 连续空闲时降低频率
                    wait = self._interval
                    if self._idle_count > self.MAX_IDLE_COUNT:
                        wait = min(self._interval * 3, 10.0)
                    
                    await asyncio.sleep(wait)
                    continue
                
                self._idle_count = 0
                
                # ---- 2. 决策 ----
                self._set_state(LoopState.DECIDING)
                decision = await self._decide(perception)
                
                if not decision.should_act:
                    # 不需要操控
                    perception_level = decision.next_perception_level
                    self._set_state(LoopState.IDLE)
                    await asyncio.sleep(self._interval)
                    continue
                
                # ---- 3. 操控（含令牌申请） ----
                self._set_state(LoopState.ACTING)
                
                # 申请操控资源
                lease_ok = await self._acquire_control_resources()
                if not lease_ok:
                    logger.warning("无法获取操控资源，跳过此轮")
                    self._set_state(LoopState.IDLE)
                    await asyncio.sleep(self._interval)
                    continue
                
                try:
                    for action in decision.actions:
                        success = await self._act(action)
                        if not success:
                            logger.warning(f"动作执行失败: {action}")
                            break
                        self._stats["actions"] += 1
                finally:
                    # 释放操控资源
                    await self._release_control_resources()
                
                # ---- 4. 验证 ----
                self._set_state(LoopState.VERIFYING)
                await self._verify(decision.actions, perception)
                
                # 更新下轮感知层级
                perception_level = decision.next_perception_level
                
                self._set_state(LoopState.IDLE)
                await asyncio.sleep(self._interval)
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["errors"] += 1
                self._set_state(LoopState.ERROR)
                logger.error(f"具身循环异常 (cycle={cycle}): {e}")
                
                if self._on_error:
                    try:
                        self._on_error(e)
                    except Exception:
                        pass
                
                # 异常后等待更长时间
                await asyncio.sleep(self._interval * 2)
                self._set_state(LoopState.IDLE)
        
        self._running = False
        logger.info(f"具身循环结束: cycles={cycle}")
    
    def stop(self):
        """停止循环"""
        self._running = False
    
    # ============================================================
    # 感知
    # ============================================================
    
    async def _perceive(self, level: PerceptionLevel) -> PerceptionResult:
        """执行感知
        
        分层策略：
        L0: 变化检测 — 截图+哈希比较（0 token）
        L1: OCR — 在L0基础上提取文字（0 token）
        L2: LLM — 在L1基础上调用LLM理解（花 token）
        """
        self._stats["perceptions"][int(level)] += 1
        
        # 自定义感知器
        if self._perceiver:
            return await self._perceiver(level)
        
        # 默认感知（基于屏幕截图）
        result = PerceptionResult(level=level)
        
        # L0: 变化检测
        screenshot = await self._capture_screen()
        if screenshot:
            result.screenshot = screenshot
            screen_hash = hashlib.md5(screenshot).hexdigest()
            result.screen_hash = screen_hash
            result.changed = (screen_hash != self._last_screen_hash)
            self._last_screen_hash = screen_hash
        else:
            # 无法截图，假设有变化
            result.changed = True
        
        if not result.changed:
            return result
        
        # L1: OCR（如果需要）
        if level >= PerceptionLevel.L1_OCR and result.screenshot:
            result.ocr_text = await self._ocr(result.screenshot)
        
        # L2: LLM（如果需要）
        if level >= PerceptionLevel.L2_LLM:
            # LLM理解需要外部注入
            result.llm_description = "(需要LLM理解，请注入decider)"
        
        return result
    
    async def _capture_screen(self) -> bytes:
        """截取屏幕
        
        尝试多种方式：
        1. mss库（跨平台截图）
        2. subprocess调系统截图工具
        3. 返回空bytes
        """
        # 尝试mss
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # 主显示器
                img = sct.grab(monitor)
                return bytes(img.rgb)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"mss截图失败: {e}")
        
        # 尝试Pillow + Windows API
        try:
            from PIL import ImageGrab
            import io
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"PIL截图失败: {e}")
        
        return b""
    
    async def _ocr(self, screenshot: bytes) -> str:
        """OCR识别
        
        尝试多种方式：
        1. pytesseract
        2. Windows OCR API
        3. 返回空字符串
        """
        # 尝试pytesseract
        try:
            import pytesseract
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(screenshot))
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            return text.strip()
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"pytesseract OCR失败: {e}")
        
        return ""
    
    # ============================================================
    # 决策
    # ============================================================
    
    async def _decide(self, perception: PerceptionResult) -> Decision:
        """执行决策"""
        if self._decider:
            return await self._decider(perception)
        
        # 默认决策：不做任何操控
        return Decision(
            should_act=False,
            reasoning="默认决策器 — 不操控",
            next_perception_level=PerceptionLevel.L0_CHANGE,
        )
    
    # ============================================================
    # 操控
    # ============================================================
    
    async def _act(self, action: Action) -> bool:
        """执行操控动作"""
        if self._actuator:
            return await self._actuator(action)
        
        logger.warning(f"没有注册操控器，跳过: {action}")
        return False
    
    # ============================================================
    # 验证
    # ============================================================
    
    async def _verify(self, actions: List[Action],
                      perception: PerceptionResult) -> bool:
        """验证操控结果"""
        if self._verifier:
            return await self._verifier(actions, perception)
        
        # 默认：不验证，直接通过
        return True
    
    # ============================================================
    # 操控资源管理（与Arbiter交互）
    # ============================================================
    
    async def _acquire_control_resources(self) -> bool:
        """申请操控所需的资源（屏幕/鼠标/键盘）
        
        Returns:
            是否成功获取所有资源
        """
        if not self._arbiter:
            return True  # 没有Arbiter，直接放行
        
        from xuanji.arbiter import (
            ResourceRequest, ResourceType, ResourcePriority
        )
        
        resources = [
            ("screen", ResourceType.SCREEN),
            ("mouse", ResourceType.MOUSE),
            ("keyboard", ResourceType.KEYBOARD),
        ]
        
        acquired = []
        for res_name, res_type in resources:
            req = ResourceRequest(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                resource_type=res_type,
                resource_name=res_name,
                priority=ResourcePriority.P2_ENGINEERING,
                timeout_sec=30.0,
            )
            
            lease = self._arbiter.request(req, lease_sec=60.0)
            if lease:
                self._active_leases[res_name] = lease
                acquired.append(res_name)
            else:
                logger.warning(f"资源申请排队中: {res_name}")
                # 释放已获取的资源
                for name in acquired:
                    old_lease = self._active_leases.pop(name, None)
                    if old_lease:
                        self._arbiter.release(old_lease.lease_id)
                return False
        
        return True
    
    async def _release_control_resources(self) -> None:
        """释放所有操控资源"""
        if not self._arbiter:
            return
        
        for name, lease in list(self._active_leases.items()):
            try:
                self._arbiter.release(lease.lease_id)
            except Exception as e:
                logger.error(f"释放资源异常 [{name}]: {e}")
        
        self._active_leases.clear()
    
    # ============================================================
    # 状态查询
    # ============================================================
    
    def get_status(self) -> Dict[str, Any]:
        """获取协调器状态"""
        return {
            "state": self._state.value,
            "running": self._running,
            "agent": self.agent_name,
            "agent_id": self.agent_id,
            "idle_count": self._idle_count,
            "active_leases": list(self._active_leases.keys()),
            "stats": dict(self._stats),
            "has_perceiver": self._perceiver is not None,
            "has_decider": self._decider is not None,
            "has_actuator": self._actuator is not None,
            "has_verifier": self._verifier is not None,
        }
    
    @property
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        return dict(self._stats)
    
    def __repr__(self):
        return (
            f"<EmbodiedLoop agent={self.agent_name} "
            f"state={self._state.value} "
            f"cycles={self._stats['cycles']}>"
        )
