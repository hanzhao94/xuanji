"""xuanji 时间旅行调试

记录执行过程的事件流，支持回放、检查点、分叉执行、
自动错误定位。

零外部依赖，纯Python标准库。
"""

import json
import os
import uuid
import time
import copy
from collections import defaultdict
from typing import Optional, List, Dict, Any

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return time.strftime("%Y-%m-%d")
class TimeTravel:
    """执行过程的时间旅行调试
    
    核心功能:
      - 记录所有执行步骤为事件流
      - 回到任何时间点查看状态
      - 从检查点分叉重新执行
      - 自动定位第一个错误
    
    事件类型:
      - task_started: 任务开始
      - llm_called:   LLM调用（含prompt和response）
      - tool_used:    工具使用
      - state_changed:状态变更
      - error:        错误
      - checkpoint:   检查点（可从此恢复）
      - decision:     决策点
      - output:       输出结果
    
    用法::
    
        tt = TimeTravel()
        
        # 记录事件
        tt.record_event("session-1", "task_started", {"task": "写第126章"})
        tt.record_checkpoint("session-1", "章节开始", {"chapter": 126, "words": 0})
        tt.record_event("session-1", "error", {"error": "风格偏移"})
        
        # 查询
        timeline = tt.get_timeline("session-1")
        error = tt.find_error("session-1")
        
        # 回放
        state = tt.replay_to("session-1", 3)
        
        # 分叉
        new_session = tt.fork_from("session-1", 2)
    """
    
    def __init__(self, data_dir: str = ""):
        """
        Args:
            data_dir: 数据持久化目录，空字符串则纯内存模式
        """
        self.data_dir = data_dir
        self._events: Dict[str, List[Dict]] = defaultdict(list)
        self._snapshots: Dict[str, Dict[int, Dict]] = defaultdict(dict)
        
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self._load_events()
    
    # ============================
    # 持久化
    # ============================
    
    def _load_events(self):
        """加载事件"""
        event_file = os.path.join(self.data_dir, "timetravel_events.jsonl")
        if not os.path.exists(event_file):
            return
        try:
            with open(event_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    sid = event.get("session_id", "")
                    if sid:
                        self._events[sid].append(event)
                        if event.get("event_type") == "checkpoint":
                            seq = event.get("seq", 0)
                            self._snapshots[sid][seq] = event.get("data", {}).get("state", {})
        except (json.JSONDecodeError, IOError):
            pass
    
    def _save_event(self, event: Dict):
        """追加事件"""
        if not self.data_dir:
            return
        event_file = os.path.join(self.data_dir, "timetravel_events.jsonl")
        with open(event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    
    # ============================
    # 记录
    # ============================
    
    def record_event(self, session_id: str, event_type: str,
                     data: Dict, state: Optional[Dict] = None) -> int:
        """记录一个事件
        
        Args:
            session_id: 会话ID
            event_type: 事件类型
            data: 事件数据
            state: 当前状态快照（checkpoint时建议提供）
            
        Returns:
            事件序号
        """
        events = self._events[session_id]
        seq = len(events) + 1
        
        event = {
            "session_id": session_id,
            "seq": seq,
            "event_type": event_type,
            "data": data,
            "timestamp": _now(),
            "ts": time.time(),
        }
        
        if state is not None:
            event["data"]["state"] = state
            self._snapshots[session_id][seq] = copy.deepcopy(state)
        
        events.append(event)
        self._save_event(event)
        return seq
    
    def record_checkpoint(self, session_id: str, label: str,
                          state: Dict) -> int:
        """记录检查点（可从此恢复）
        
        Args:
            session_id: 会话ID
            label: 检查点标签
            state: 完整状态快照
        """
        return self.record_event(session_id, "checkpoint", {
            "label": label,
            "state": copy.deepcopy(state),
        }, state=state)
    
    # ============================
    # 查询
    # ============================
    
    def get_timeline(self, session_id: str) -> List[Dict]:
        """获取完整时间线（轻量版，不含完整state）"""
        events = self._events.get(session_id, [])
        return [
            {
                "seq": e["seq"],
                "event_type": e["event_type"],
                "timestamp": e["timestamp"],
                "summary": self._event_summary(e),
                "has_state": "state" in e.get("data", {}),
            }
            for e in events
        ]
    
    def get_event(self, session_id: str, seq: int) -> Optional[Dict]:
        """获取单个事件的完整数据"""
        for e in self._events.get(session_id, []):
            if e["seq"] == seq:
                return e
        return None
    
    def replay_to(self, session_id: str, event_seq: int) -> Dict:
        """回放到指定事件，重建当时的状态
        
        Returns:
            {
                "state": dict,
                "event": dict,
                "checkpoint_seq": int,
                "events_replayed": int,
            }
        """
        events = self._events.get(session_id, [])
        if not events:
            raise ValueError(f"No events for session: {session_id}")
        
        target_event = None
        for e in events:
            if e["seq"] == event_seq:
                target_event = e
                break
        if not target_event:
            raise ValueError(f"Event seq {event_seq} not found")
        
        # 找最近的checkpoint
        snapshots = self._snapshots.get(session_id, {})
        checkpoint_seq = 0
        state = {}
        for seq in sorted(snapshots.keys()):
            if seq <= event_seq:
                checkpoint_seq = seq
                state = copy.deepcopy(snapshots[seq])
            else:
                break
        
        # 应用checkpoint后的事件
        events_replayed = 0
        for e in events:
            if e["seq"] <= checkpoint_seq:
                continue
            if e["seq"] > event_seq:
                break
            self._apply_event_to_state(state, e)
            events_replayed += 1
        
        return {
            "state": state,
            "event": target_event,
            "checkpoint_seq": checkpoint_seq,
            "events_replayed": events_replayed,
        }
    
    def find_error(self, session_id: str) -> Optional[Dict]:
        """自动找到第一个错误事件
        
        Returns:
            {"seq": int, "event": dict, "context_before": [...], "context_after": [...]}
        """
        events = self._events.get(session_id, [])
        for i, e in enumerate(events):
            if e["event_type"] == "error":
                context_before = events[max(0, i - 2):i]
                context_after = events[i + 1:i + 3]
                return {
                    "seq": e["seq"],
                    "event": e,
                    "context_before": [self._event_summary(c) for c in context_before],
                    "context_after": [self._event_summary(c) for c in context_after],
                    "timestamp": e["timestamp"],
                }
        return None
    
    def find_all_errors(self, session_id: str) -> List[Dict]:
        """找到所有错误事件"""
        return [
            {"seq": e["seq"], "timestamp": e["timestamp"],
             "summary": self._event_summary(e)}
            for e in self._events.get(session_id, [])
            if e["event_type"] == "error"
        ]
    
    def fork_from(self, session_id: str, event_seq: int) -> str:
        """从某个事件点分叉，创建新的执行路径
        
        Returns:
            新会话ID
        """
        replay_result = self.replay_to(session_id, event_seq)
        new_session_id = f"{session_id}:fork-{uuid.uuid4().hex[:4]}"
        
        # 复制分叉点之前的事件
        for e in self._events.get(session_id, []):
            if e["seq"] > event_seq:
                break
            new_event = copy.deepcopy(e)
            new_event["session_id"] = new_session_id
            self._events[new_session_id].append(new_event)
            self._save_event(new_event)
            if "state" in new_event.get("data", {}):
                self._snapshots[new_session_id][new_event["seq"]] = \
                    copy.deepcopy(new_event["data"]["state"])
        
        # 记录fork事件
        self.record_event(new_session_id, "fork", {
            "forked_from": session_id,
            "fork_point": event_seq,
            "state": replay_result["state"],
        }, state=replay_result["state"])
        
        return new_session_id
    
    def summary(self, session_id: str) -> str:
        """执行过程摘要"""
        events = self._events.get(session_id, [])
        if not events:
            return f"会话 {session_id}: 无事件记录"
        
        parts = [f"# 执行摘要 — {session_id}\n"]
        
        first = events[0]
        last = events[-1]
        parts.append(f"- 开始: {first['timestamp']}")
        parts.append(f"- 结束: {last['timestamp']}")
        parts.append(f"- 总事件: {len(events)}")
        
        type_counts: Dict[str, int] = {}
        for e in events:
            t = e["event_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        
        parts.append(f"\n## 事件统计")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            parts.append(f"- {t}: {c}")
        
        errors = [e for e in events if e["event_type"] == "error"]
        if errors:
            parts.append(f"\n## 错误 ({len(errors)})")
            for err in errors:
                parts.append(f"- [seq={err['seq']}] {self._event_summary(err)}")
        
        checkpoints = [e for e in events if e["event_type"] == "checkpoint"]
        if checkpoints:
            parts.append(f"\n## 检查点 ({len(checkpoints)})")
            for cp in checkpoints:
                label = cp.get("data", {}).get("label", "unnamed")
                parts.append(f"- [seq={cp['seq']}] {label} @ {cp['timestamp']}")
        
        return "\n".join(parts)
    
    def list_sessions(self, last_n: int = 10) -> List[Dict]:
        """列出所有会话"""
        result = []
        for sid, events in self._events.items():
            if not events:
                continue
            result.append({
                "session_id": sid,
                "event_count": len(events),
                "first_event": events[0]["timestamp"],
                "last_event": events[-1]["timestamp"],
                "has_errors": any(e["event_type"] == "error" for e in events),
                "checkpoints": sum(1 for e in events if e["event_type"] == "checkpoint"),
            })
        result.sort(key=lambda x: x["last_event"], reverse=True)
        return result[:last_n]
    
    # ============================
    # 内部方法
    # ============================
    
    def _event_summary(self, event: Dict) -> str:
        """生成事件摘要"""
        et = event.get("event_type", "unknown")
        data = event.get("data", {})
        
        if et == "checkpoint":
            return data.get("label", "unnamed checkpoint")
        elif et == "error":
            return data.get("error", data.get("message", "unknown error"))[:100]
        elif et == "task_started":
            return data.get("task", "")[:100]
        elif et == "llm_called":
            return f"model={data.get('model', '?')} tokens={data.get('tokens', 0)}"
        elif et == "tool_used":
            return f"tool={data.get('tool', '?')}"
        elif et == "state_changed":
            field_name = data.get("field", "?")
            return f"{field_name}: {str(data.get('old', ''))[:30]} → {str(data.get('new', ''))[:30]}"
        elif et == "decision":
            return data.get("decision", "")[:100]
        elif et == "output":
            return data.get("output", "")[:100]
        elif et == "fork":
            return f"forked from {data.get('forked_from', '?')} @ seq {data.get('fork_point', '?')}"
        else:
            return str(data)[:100]
    
    def _apply_event_to_state(self, state: Dict, event: Dict):
        """将事件应用到状态（用于replay重建）"""
        et = event.get("event_type", "")
        data = event.get("data", {})
        
        if et == "state_changed":
            field_name = data.get("field", "")
            new_value = data.get("new")
            if field_name and new_value is not None:
                state[field_name] = new_value
        
        if "_event_log" not in state:
            state["_event_log"] = []
        state["_event_log"].append({
            "seq": event.get("seq"),
            "type": et,
            "ts": event.get("timestamp"),
        })
