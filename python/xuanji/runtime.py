"""xuanji 运行时主控

负责：
- 加载配置
- 发现+加载插件
- 启动Agent为独立进程（进程隔离）
- 消息路由
- 资源仲裁（独占令牌/GPU配额/端口分区）
- 心跳监控与崩溃自动重启
- 优雅关闭
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from xuanji.plugin import AgentPlugin
from xuanji.isolation import AgentProcess, AgentStatus
from xuanji.arbiter import (
    ResourceArbiter,
    ResourcePriority,
    ResourceRequest,
    ResourceType,
)

logger = logging.getLogger("xuanji.runtime")


class Runtime:
    """xuanji运行时 — 框架核心"""
    
    def __init__(self, config: str = "config.toml"):
        self.config_path = config
        self.config: Dict = {}
        self.agents: Dict[str, AgentPlugin] = {}
        self.running = False

        # ── 核心引擎 ──
        self._bus = None                  # 消息总线
        self._arbiter: Optional[ResourceArbiter] = None   # 资源仲裁
        self._process_mgr: Optional[AgentProcess] = None  # 进程管理
        self._governor = None             # Token治理（后续实现）
        self._guard = None                # 记忆守护（后续实现）
        self._evolution = None            # 进化系统（可选）

        # Agent注册表: name → {id, module, class, config}
        self._agent_registry: Dict[str, Dict] = {}
        self._next_agent_id = 1
    
    def load_config(self) -> Dict:
        """加载配置文件
        
        优先级: 环境变量 > config.toml > 自动探测 > 内置默认值
        """
        config = {}
        
        # 1. 内置默认值
        config = self._default_config()
        
        # 2. 配置文件
        if os.path.exists(self.config_path):
            file_config = self._load_toml(self.config_path)
            config = self._merge(config, file_config)
        
        # 3. 环境变量
        env_config = self._load_env()
        config = self._merge(config, env_config)
        
        # 4. 自动探测
        auto_config = self._auto_detect()
        # 自动探测不覆盖已有配置
        for key, val in auto_config.items():
            if key not in config:
                config[key] = val
        
        self.config = config
        return config
    
    def register(self, agent: AgentPlugin,
                 plugin_module: str = "",
                 plugin_class: str = "",
                 priority: int = ResourcePriority.P4_BACKGROUND,
                 gpu_quota_mb: int = 0,
                 config: Optional[Dict] = None) -> int:
        """注册Agent

        Args:
            agent: AgentPlugin实例
            plugin_module: 模块路径 (e.g. "agents.my_agent")
            plugin_class: 类名 (e.g. "MyAgent")
            priority: 默认资源优先级
            gpu_quota_mb: GPU显存配额（MB），0=不分配
            config: 额外配置

        Returns:
            分配的agent_id
        """
        self.agents[agent.name] = agent

        agent_id = self._next_agent_id
        self._next_agent_id += 1

        self._agent_registry[agent.name] = {
            "id": agent_id,
            "module": plugin_module or agent.__class__.__module__,
            "class": plugin_class or agent.__class__.__name__,
            "priority": priority,
            "gpu_quota_mb": gpu_quota_mb,
            "config": config or {},
        }

        return agent_id
    
    def enable_evolution(self, data_dir: str = None, auto_extract: bool = True, auto_adjust: bool = True) -> None:
        """启用进化系统
        
        进化系统会自动挂载到HookManager，在任务启动/完成/出错时自动触发：
        - 启动前: 加载预防策略 + 成功模板 + 自适应安全策略
        - 完成后: 提取成功模式 + 记录跨任务经验
        - 出错时: 记录失败教训 + 调整沙盒策略
        
        Args:
            data_dir: 数据目录（pitfalls/patterns/cross_index 的父目录）
            auto_extract: 是否自动提取成功模式
            auto_adjust: 是否自动调整沙盒策略
        """
        try:
            from xuanji.evolution_hook import EvolutionHook
            from xuanji.hooks import HookManager
            
            # 创建或获取HookManager
            if not hasattr(self, '_hook_manager'):
                self._hook_manager = HookManager()
            
            # 创建并挂载EvolutionHook
            self._evolution = EvolutionHook(
                data_dir=data_dir,
                auto_extract=auto_extract,
                auto_adjust=auto_adjust,
            )
            self._evolution.register_to(self._hook_manager)
            
            logger.info("Evolution system enabled")
            print(f"   [OK] 进化系统已启用")
        except Exception as e:
            logger.warning(f"Failed to enable evolution system: {e}")
            print(f"   [WARN] 进化系统启用失败: {e}")
    
    def run(self, agents: Optional[List[AgentPlugin]] = None) -> None:
        """启动运行时

        完整启动流程：
        1. 注册Agent
        2. 加载配置
        3. 初始化资源仲裁器
        4. 初始化进程管理器
        5. 为每个Agent启动独立进程
        6. 启动心跳监控
        7. 进入主循环

        Args:
            agents: Agent列表，也可以预先用register注册
        """
        # 注册
        if agents:
            for agent in agents:
                self.register(agent)

        # 加载配置
        self.load_config()

        # 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # ── 初始化资源仲裁器 ──
        self._arbiter = ResourceArbiter()
        self._arbiter.start_cleanup(interval=30.0)

        # ── 初始化进程管理器 ──
        workdir = self.config.get("runtime", {}).get(
            "workdir", ".xuanji_work"
        )
        self._process_mgr = AgentProcess(
            base_workdir=workdir,
            on_message=self._on_agent_message,
        )

        # 启动
        self.running = True
        print(f"🚀 xuanji v{self._version()} 启动")
        print(f"   Agent数量: {len(self.agents)}")
        print(f"   配置文件: {self.config_path}")
        print(f"   工作目录: {os.path.abspath(workdir)}")

        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()
    
    async def _main_loop(self):
        """主循环

        1. 为每个注册的Agent启动独立子进程
        2. 启动心跳监控
        3. 主循环：定期健康检查 + 僵尸进程清理
        """
        # ── 启动所有Agent为独立进程 ──
        for name, reg in self._agent_registry.items():
            agent_id = reg["id"]

            # 设置GPU配额
            if reg.get("gpu_quota_mb", 0) > 0:
                self._arbiter.set_gpu_quota(agent_id, reg["gpu_quota_mb"])

            # 启动进程
            try:
                self._process_mgr.start_agent(
                    name=name,
                    agent_id=agent_id,
                    plugin_module=reg["module"],
                    plugin_class=reg["class"],
                    config=reg.get("config", {}),
                )
                print(f"   ✅ Agent [{name}] 已启动 (id={agent_id})")
            except Exception as e:
                print(f"   ❌ Agent [{name}] 启动失败: {e}")
                logger.error(f"Agent [{name}] 启动失败: {e}")

        # 分配端口段
        agent_ids = [r["id"] for r in self._agent_registry.values()]
        if agent_ids:
            self._arbiter.allocate_port_ranges(agent_ids)

        # 启动心跳监控
        self._process_mgr.start_monitor(interval=10.0)

        print(f"   🔄 主循环运行中...")

        # ── 主循环 ──
        loop_count = 0
        while self.running:
            await asyncio.sleep(5)
            loop_count += 1

            # 每30秒做一次深度健康检查
            if loop_count % 6 == 0:
                health = self._process_mgr.check_health()
                for agent_name, status in health.items():
                    if status.get("status") == "DEAD":
                        agent_reg = self._agent_registry.get(agent_name)
                        if agent_reg:
                            # Agent死亡 → 回收其资源
                            self._arbiter.revoke_agent(agent_reg["id"])
                            logger.warning(
                                f"Agent [{agent_name}] 已死亡，"
                                f"资源已回收"
                            )

            # 每60秒清理僵尸进程
            if loop_count % 12 == 0:
                self._process_mgr.cleanup_zombies()

            # 每60秒清理过期租约
            if loop_count % 12 == 0:
                expired = self._arbiter.cleanup_expired()
                if expired > 0:
                    logger.info(f"清理 {expired} 个过期租约")
    
    def _signal_handler(self, signum, frame):
        """信号处理"""
        print("\n🛑 收到停止信号，优雅关闭...")
        self.running = False

    def _cleanup(self):
        """优雅关闭所有子系统

        顺序：
        1. 停止所有Agent进程（等待当前任务完成）
        2. 停止仲裁器清理线程
        3. 关闭消息总线
        """
        print("🛑 正在优雅关闭...")

        # 1. 停止所有Agent进程
        if self._process_mgr:
            print("   ⏳ 等待Agent完成当前任务...")
            self._process_mgr.stop_all(timeout=15.0)

        # 2. 停止仲裁器
        if self._arbiter:
            self._arbiter.stop()

        # 3. 关闭消息总线
        if self._bus:
            try:
                self._bus.close()
            except Exception:
                pass

        print("🧹 清理完成")

    # ============================================================
    # Agent消息回调
    # ============================================================

    def _on_agent_message(self, agent_name: str, msg: dict):
        """处理来自Agent子进程的消息

        这是AgentProcess的on_message回调，当Agent通过stdout
        发送非系统消息（result/message/error/log等）时触发。

        Args:
            agent_name: 发送消息的Agent名称
            msg: 消息字典
        """
        msg_type = msg.get("type", "")

        if msg_type == "error":
            logger.error(
                f"Agent [{agent_name}] 错误: {msg.get('msg', '')}"
            )
            # 触发进化系统错误钩子
            self._trigger_evolution_error(agent_name, msg)
        elif msg_type == "result":
            logger.info(
                f"Agent [{agent_name}] 任务完成"
            )
            # 触发进化系统完成钩子
            self._trigger_evolution_success(agent_name, msg)
            # 将结果路由到消息总线或请求方
            self._route_result(agent_name, msg)
        elif msg_type == "message":
            # Agent间消息路由
            logger.debug(
                f"Agent [{agent_name}] 消息: {str(msg.get('data', ''))[:100]}"
            )
            # 通过消息总线路由到目标Agent
            self._route_message(agent_name, msg)
        else:
            logger.debug(
                f"Agent [{agent_name}] 未知消息: {msg_type}"
            )
    
    def _trigger_evolution_success(self, agent_name: str, msg: dict) -> None:
        """触发进化系统成功钩子"""
        if not self._evolution:
            return
        try:
            context = {
                "task_type": msg.get("task_type", "unknown"),
                "domain": msg.get("domain", "development"),
                "elapsed_time": msg.get("elapsed_time", 0),
                "tokens_used": msg.get("tokens_used", 0),
                "subtasks": msg.get("subtasks", []),
                "llm_calls": msg.get("llm_calls", 0),
                "memory_retrieved": msg.get("memory_retrieved", 0),
                "iterations": msg.get("iterations", 1),
                "sandbox_verified": msg.get("sandbox_verified", False),
                "quality_score": msg.get("quality_score", 0.8),
            }
            result = msg.get("data", {})
            self._evolution._after_task(context, result)
        except Exception as e:
            logger.warning(f"Evolution success hook failed: {e}")
    
    def _trigger_evolution_error(self, agent_name: str, msg: dict) -> None:
        """触发进化系统错误钩子"""
        if not self._evolution:
            return
        try:
            context = {
                "task_type": msg.get("task_type", "unknown"),
                "task_features": msg.get("task_features", {}),
            }
            error_msg = msg.get("msg", "Unknown error")
            self._evolution._on_error(context, Exception(error_msg))
        except Exception as e:
            logger.warning(f"Evolution error hook failed: {e}")

    def _route_result(self, agent_name: str, msg: dict) -> None:
        """将Agent结果路由到消息总线或请求方

        Args:
            agent_name: 发送结果的Agent名称
            msg: 包含result数据的消息字典
        """
        result_data = msg.get("data", {})
        if self._bus:
            self._bus.publish(
                f"xuanji.result.{agent_name}",
                {"agent": agent_name, "result": result_data, "ts": time.time()},
            )
            logger.debug(f"Result published to bus: xuanji.result.{agent_name}")
        else:
            logger.debug(f"Result from [{agent_name}]: {json.dumps(result_data, ensure_ascii=False)}")

    def _route_message(self, agent_name: str, msg: dict) -> None:
        """通过消息总线路由Agent间消息到目标Agent

        支持msg中target字段指定目标Agent，无target则广播。

        Args:
            agent_name: 发送消息的Agent名称
            msg: 包含data和可选target的消息字典
        """
        target = msg.get("target", "")
        data = msg.get("data", {})
        if target:
            topic = f"xuanji.message.{target}"
        else:
            topic = "xuanji.message.broadcast"
        if self._bus:
            self._bus.publish(
                topic,
                {"from": agent_name, "to": target or "broadcast", "data": data, "ts": time.time()},
            )
            logger.debug(f"Message routed via bus: {agent_name} → {target or 'broadcast'}")
        else:
            logger.debug(f"Message from [{agent_name}]: no bus, topic={topic}")

    # ============================================================
    # 资源仲裁便捷方法
    # ============================================================

    def request_resource(
        self,
        agent_name: str,
        resource_type: ResourceType,
        resource_name: str = "default",
        priority: Optional[ResourcePriority] = None,
        timeout_sec: float = 30.0,
        **kwargs,
    ):
        """为Agent申请资源（便捷方法）

        Args:
            agent_name: Agent名称
            resource_type: 资源类型
            resource_name: 资源名称
            priority: 优先级（None=使用Agent注册时的默认优先级）
            timeout_sec: 排队超时
            **kwargs: 扩展参数（如 vram_mb, port）

        Returns:
            ResourceLease 或 None（排队中）
        """
        if not self._arbiter:
            return None

        reg = self._agent_registry.get(agent_name)
        if not reg:
            return None

        if priority is None:
            priority = ResourcePriority(
                reg.get("priority", ResourcePriority.P4_BACKGROUND)
            )

        req = ResourceRequest(
            agent_id=reg["id"],
            agent_name=agent_name,
            resource_type=resource_type,
            resource_name=resource_name,
            priority=priority,
            timeout_sec=timeout_sec,
            data=kwargs,
        )

        return self._arbiter.request(req)

    def release_resource(self, lease_id: int) -> bool:
        """释放资源（便捷方法）"""
        if self._arbiter:
            return self._arbiter.release(lease_id)
        return False
    
    def run_task(
        self,
        task_type: str,
        task_func: callable,
        task_features: Dict = None,
        **kwargs,
    ) -> Dict:
        """运行任务（带进化系统）
        
        这是运行任务的推荐方式，会自动触发进化系统钩子：
        - 启动前: 加载预防策略 + 模板 + 安全策略
        - 完成后: 提取模式 + 记录经验
        - 出错时: 记录教训 + 调整策略
        
        Args:
            task_type: 任务类型（如 "cli_app", "web_scraper"）
            task_func: 任务函数（接收context和**kwargs，返回result）
            task_features: 任务特征（用于风险评估）
            **kwargs: 传递给task_func的额外参数
        
        Returns:
            {"status": "success"/"error", "result": ..., "evolution": ...}
        """
        import time
        
        start_time = time.time()
        context = {
            "task_type": task_type,
            "task_features": task_features or {},
            "start_time": start_time,
        }
        
        # 进化系统: 启动前钩子
        if self._evolution:
            try:
                context = self._evolution._before_task(context)
            except Exception as e:
                logger.warning(f"Evolution before_task hook failed: {e}")
        
        # 执行任务
        try:
            result = task_func(context, **kwargs)
            elapsed = time.time() - start_time
            
            # 进化系统: 完成后钩子
            if self._evolution:
                try:
                    context["elapsed_time"] = elapsed
                    context["sandbox_verified"] = True
                    result = self._evolution._after_task(context, result)
                except Exception as e:
                    logger.warning(f"Evolution after_task hook failed: {e}")
            
            return {
                "status": "success",
                "result": result,
                "elapsed_time": elapsed,
                "evolution": context.get("_evolution_stats", {}),
            }
        
        except Exception as e:
            elapsed = time.time() - start_time
            
            # 进化系统: 错误钩子
            if self._evolution:
                try:
                    self._evolution._on_error(context, e)
                except Exception as hook_err:
                    logger.warning(f"Evolution on_error hook failed: {hook_err}")
            
            return {
                "status": "error",
                "error": str(e),
                "elapsed_time": elapsed,
            }

    # ============================================================
    # 状态查询
    # ============================================================

    def get_status(self) -> Dict:
        """获取运行时完整状态

        Returns:
            包含Agent状态、资源状态的综合报告
        """
        status = {
            "version": self._version(),
            "running": self.running,
            "agents_registered": len(self._agent_registry),
        }

        if self._process_mgr:
            status["processes"] = self._process_mgr.get_status()

        if self._arbiter:
            status["resources"] = self._arbiter.get_status()

        return status
    
    def _version(self) -> str:
        try:
            import importlib.metadata
            return importlib.metadata.version('xuanji')
        except Exception:
            return "1.0.3"
    
    def _default_config(self) -> Dict:
        """内置默认配置"""
        return {
            "runtime": {"name": "xuanji"},
            "security": {"mode": "standard"},
        }
    
    def _load_toml(self, path: str) -> Dict:
        """加载TOML配置"""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                # 降级：手动解析简单TOML
                return self._simple_toml_parse(path)
        
        with open(path, "rb") as f:
            return tomllib.load(f)
    
    def _simple_toml_parse(self, path: str) -> Dict:
        """简单TOML解析（不依赖第三方库）"""
        config = {}
        current_section = config
        
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    section = line.strip("[]").strip()
                    parts = section.split(".")
                    current_section = config
                    for part in parts:
                        current_section = current_section.setdefault(part, {})
                elif "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    current_section[key] = val
        
        return config
    
    def _load_env(self) -> Dict:
        """从环境变量加载配置
        
        xuanji_LLM_DEEPSEEK=sk-xxx → llm.deepseek = sk-xxx
        """
        config = {}
        prefix = "xuanji_"
        
        for key, val in os.environ.items():
            if key.startswith(prefix):
                parts = key[len(prefix):].lower().split("_")
                d = config
                for part in parts[:-1]:
                    d = d.setdefault(part, {})
                d[parts[-1]] = val
        
        return config
    
    def _auto_detect(self) -> Dict:
        """自动探测本地环境"""
        config = {}
        
        # 检测Ollama
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    config.setdefault("llm", {})["ollama"] = "localhost"
        except Exception:
            pass
        
        # 检测常见环境变量
        for env_key, provider in [
            ("DEEPSEEK_API_KEY", "deepseek"),
            ("OPENAI_API_KEY", "openai"),
            ("DASHSCOPE_API_KEY", "dashscope"),
            ("ANTHROPIC_API_KEY", "anthropic"),
        ]:
            val = os.environ.get(env_key)
            if val:
                config.setdefault("llm", {})[provider] = val
        
        return config
    
    def _merge(self, base: Dict, override: Dict) -> Dict:
        """深度合并配置"""
        result = base.copy()
        for key, val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = self._merge(result[key], val)
            else:
                result[key] = val
        return result
