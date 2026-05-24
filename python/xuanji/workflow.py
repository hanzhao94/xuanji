"""转发到 evolution/workflow (不存在，提供基础工作流)"""
from typing import Callable, Dict, List, Optional, Any

class Workflow:
    """简单工作流引擎 — 步骤链式执行"""
    def __init__(self, name: str = ""):
        self.name = name
        self._steps: List[dict] = []
        self._context: Dict[str, Any] = {}

    def add_step(self, name: str, func: Callable, condition: Optional[Callable] = None):
        self._steps.append({'name': name, 'func': func, 'condition': condition})

    def run(self, **kwargs) -> Dict[str, Any]:
        self._context.update(kwargs)
        results = {}
        for step in self._steps:
            if step['condition'] and not step['condition'](self._context):
                continue
            try:
                result = step['func'](self._context)
                if result:
                    self._context.update(result)
                results[step['name']] = 'OK'
            except Exception as e:
                results[step['name']] = f'FAIL: {e}'
                break
        return results

    def status(self) -> Dict:
        return {'name': self.name, 'steps': len(self._steps), 'context_keys': list(self._context.keys())}

__all__ = ['Workflow']
