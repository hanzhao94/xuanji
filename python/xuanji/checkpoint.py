"""转发到 evolution/checkpoint (不存在，提供基础检查点)"""
import json, os, time
from typing import Dict, Optional, List

class Checkpoint:
    """简单检查点系统 — 保存/恢复状态"""
    def __init__(self, data_dir: str = "~/.xuanji/checkpoints"):
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)

    def save(self, name: str, state: Dict) -> str:
        path = os.path.join(self.data_dir, f"{name}.json")
        state['_timestamp'] = time.time()
        with open(path, 'w') as f:
            json.dump(state, f, indent=2)
        return path

    def load(self, name: str) -> Optional[Dict]:
        path = os.path.join(self.data_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def list_checkpoints(self) -> List[Dict]:
        result = []
        for f in os.listdir(self.data_dir):
            if f.endswith('.json'):
                path = os.path.join(self.data_dir, f)
                stat = os.stat(path)
                result.append({'name': f[:-5], 'size': stat.st_size, 'mtime': stat.st_mtime})
        return sorted(result, key=lambda x: x['mtime'], reverse=True)

    def delete(self, name: str):
        path = os.path.join(self.data_dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)

__all__ = ['Checkpoint']
