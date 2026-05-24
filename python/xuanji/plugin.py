"""
xuanji 插件协议定义

所有插件类型的基类和接口。
用户通过继承这些类来扩展框架。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class PluginBase(ABC):
    """所有插件的基类"""
    
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    
    def on_load(self, ctx: Any) -> None:
        """插件加载时调用"""
        pass
    
    def on_unload(self) -> None:
        """插件卸载时调用"""
        pass


class AgentPlugin(PluginBase):
    """Agent插件 — 用户写业务逻辑"""
    
    # 声明需要的工具
    tools: List[str] = []
    
    # 声明需要的资源
    resources: Dict[str, Any] = {}
    
    async def on_start(self, ctx: Any) -> None:
        """Agent启动时调用"""
        pass
    
    async def on_message(self, msg: Any, ctx: Any) -> Optional[str]:
        """收到消息时调用
        
        Args:
            msg: 统一消息对象 (Message)
            ctx: 上下文 (包含 llm/memory/tools/channels/perception/hands)
        
        Returns:
            回复文本，或 None 不回复
        """
        pass
    
    async def on_task(self, task: Any, ctx: Any) -> Any:
        """收到任务时调用
        
        Args:
            task: 任务对象
            ctx: 上下文
        
        Returns:
            任务结果
        """
        pass
    
    async def on_stop(self) -> None:
        """Agent停止时调用"""
        pass


class ToolPlugin(PluginBase):
    """工具插件 — 给Agent加新工具"""
    
    @abstractmethod
    def schema(self) -> Dict:
        """返回工具参数描述
        
        Returns:
            参数schema，如 {"city": {"type": "string", "description": "城市名"}}
        """
        ...
    
    @abstractmethod
    async def execute(self, params: Dict, ctx: Any) -> Any:
        """执行工具
        
        Args:
            params: 调用参数
            ctx: 上下文
        
        Returns:
            执行结果
        """
        ...


class ChannelPlugin(PluginBase):
    """通信渠道插件 — 接入新的通信平台"""
    
    _callbacks: Dict[str, list] = {}
    
    @abstractmethod
    async def connect(self, config: Dict) -> None:
        """连接到平台"""
        ...
    
    @abstractmethod
    async def listen(self) -> None:
        """监听消息（长连接/轮询）"""
        ...
    
    @abstractmethod
    async def send_text(self, target: str, text: str) -> None:
        """发送文本消息"""
        ...
    
    async def send_image(self, target: str, image: Any) -> None:
        """发送图片（可选实现）"""
        raise NotImplementedError
    
    async def send_file(self, target: str, path: str) -> None:
        """发送文件（可选实现）"""
        raise NotImplementedError
    
    async def send_voice(self, target: str, audio: Any) -> None:
        """发送语音（可选实现）"""
        raise NotImplementedError
    
    async def emit(self, event: str, data: Any) -> None:
        """触发事件"""
        for cb in self._callbacks.get(event, []):
            await cb(data)
    
    def on(self, event: str, callback) -> None:
        """注册事件回调"""
        self._callbacks.setdefault(event, []).append(callback)
    
    async def disconnect(self) -> None:
        """断开连接"""
        pass


class LLMPlugin(PluginBase):
    """LLM后端插件 — 接入新的模型服务"""
    
    @abstractmethod
    async def chat(self, messages: List[Dict], **kwargs) -> str:
        """对话"""
        ...
    
    async def stream(self, messages: List[Dict], **kwargs):
        """流式对话（可选）"""
        result = await self.chat(messages, **kwargs)
        yield result
    
    async def embed(self, text: str) -> List[float]:
        """文本向量化（可选）"""
        raise NotImplementedError
    
    async def vision(self, image: Any, prompt: str) -> str:
        """视觉理解（可选）"""
        raise NotImplementedError


class MemoryPlugin(PluginBase):
    """记忆后端插件 — 数据存在哪里"""
    
    @abstractmethod
    async def store(self, content: str, **meta) -> str:
        """存储记忆，返回ID"""
        ...
    
    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        """搜索记忆"""
        ...
    
    async def forget(self, key: str) -> bool:
        """删除记忆（可选）"""
        raise NotImplementedError


class SchedulerPlugin(PluginBase):
    """调度策略插件 — 怎么分配任务"""
    
    @abstractmethod
    def assign(self, task: Any, agents: List[str]) -> str:
        """给任务分配Agent，返回Agent名"""
        ...
    
    def prioritize(self, tasks: List[Any]) -> List[Any]:
        """任务排序（可选）"""
        return tasks
