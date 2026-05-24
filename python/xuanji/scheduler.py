"""转发到 evolution/scheduler.py (不存在，提供基础调度器)"""
import time, threading
from typing import Callable, Dict, Optional, List

class Scheduler:
    """简单任务调度器 — 定时/周期执行"""
    def __init__(self):
        self._tasks: Dict[str, dict] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def add(self, name: str, func: Callable, interval: float, immediate: bool = False):
        self._tasks[name] = {'func': func, 'interval': interval, 'next_run': time.time() if immediate else time.time() + interval}

    def remove(self, name: str):
        self._tasks.pop(name, None)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            now = time.time()
            for name, task in list(self._tasks.items()):
                if now >= task['next_run']:
                    try:
                        task['func']()
                    except Exception:
                        pass
                    task['next_run'] = now + task['interval']
            time.sleep(0.1)

    def status(self) -> Dict:
        return {name: {'interval': t['interval'], 'next_in': round(t['next_run'] - time.time(), 1)} for name, t in self._tasks.items()}

__all__ = ['Scheduler']
