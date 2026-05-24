"""
xuanji 国际化模块

语言检测、翻译key管理、locale设置。
内置中英双语prompt模板，支持从JSON加载翻译。
零外部依赖。
"""

import os
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field, asdict


# ============================================================
# 语言检测
# ============================================================

# Unicode范围
_RANGES = {
    "zh": [
        (0x4E00, 0x9FFF),   # CJK统一汉字
        (0x3400, 0x4DBF),   # CJK扩展A
        (0x2E80, 0x2EFF),   # CJK部首
        (0xF900, 0xFAFF),   # CJK兼容
    ],
    "ja": [
        (0x3040, 0x309F),   # 平假名
        (0x30A0, 0x30FF),   # 片假名
        (0x31F0, 0x31FF),   # 片假名扩展
    ],
    "ko": [
        (0xAC00, 0xD7AF),   # 韩文音节
        (0x1100, 0x11FF),   # 韩文字母
        (0x3130, 0x318F),   # 韩文兼容字母
    ],
}


def _char_lang(ch: str) -> Optional[str]:
    """判断单个字符属于哪种语言"""
    code = ord(ch)
    for lang, ranges in _RANGES.items():
        for start, end in ranges:
            if start <= code <= end:
                return lang
    if ch.isascii() and ch.isalpha():
        return "en"
    return None


def detect_language(text: str) -> str:
    """检测文本语言

    简单检测：统计各语言字符数量，返回占比最高的。
    支持: zh(中文), en(英文), ja(日文), ko(韩文)

    Args:
        text: 输入文本

    Returns:
        语言代码 (zh/en/ja/ko/unknown)
    """
    if not text or not text.strip():
        return "unknown"

    counts: Dict[str, int] = {"zh": 0, "en": 0, "ja": 0, "ko": 0}
    total = 0

    for ch in text:
        lang = _char_lang(ch)
        if lang:
            counts[lang] += 1
            total += 1

    if total == 0:
        return "unknown"

    # 特殊处理：日文中也有汉字，但有假名就优先判日文
    if counts["ja"] > 0:
        # 有假名 → 日文
        return "ja"

    # 按比例判断
    best_lang = max(counts, key=lambda k: counts[k])
    ratio = counts[best_lang] / total

    if ratio < 0.3:
        return "unknown"

    return best_lang


def detect_language_detailed(text: str) -> Dict[str, Any]:
    """详细的语言检测结果"""
    counts: Dict[str, int] = {"zh": 0, "en": 0, "ja": 0, "ko": 0, "other": 0}
    total = len(text)

    for ch in text:
        lang = _char_lang(ch)
        if lang:
            counts[lang] += 1
        elif not ch.isspace():
            counts["other"] += 1

    primary = detect_language(text)
    ratios = {k: v / total if total > 0 else 0 for k, v in counts.items()}

    return {
        "primary": primary,
        "counts": counts,
        "ratios": ratios,
        "total_chars": total,
    }


# ============================================================
# 内置翻译
# ============================================================

# 内置中英双语prompt模板
_BUILTIN_TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # 系统提示
    "system.welcome": {
        "zh": "你好！我是你的AI助手。有什么我能帮你的吗？",
        "en": "Hello! I'm your AI assistant. How can I help you?",
        "ja": "こんにちは！AIアシスタントです。何かお手伝いできることはありますか？",
        "ko": "안녕하세요! AI 어시스턴트입니다. 무엇을 도와드릴까요?",
    },
    "system.thinking": {
        "zh": "让我想想...",
        "en": "Let me think...",
        "ja": "考えさせてください...",
        "ko": "생각해 볼게요...",
    },
    "system.error": {
        "zh": "抱歉，出了点问题。请稍后再试。",
        "en": "Sorry, something went wrong. Please try again later.",
        "ja": "申し訳ございません。問題が発生しました。後でもう一度お試しください。",
        "ko": "죄송합니다. 문제가 발생했습니다. 나중에 다시 시도해 주세요.",
    },
    "system.no_result": {
        "zh": "没有找到相关结果。",
        "en": "No relevant results found.",
        "ja": "関連する結果が見つかりませんでした。",
        "ko": "관련 결과를 찾을 수 없습니다.",
    },
    "system.confirm": {
        "zh": "确定要执行此操作吗？",
        "en": "Are you sure you want to proceed?",
        "ja": "この操作を実行してもよろしいですか？",
        "ko": "이 작업을 진행하시겠습니까?",
    },
    "system.success": {
        "zh": "操作成功！",
        "en": "Operation successful!",
        "ja": "操作が成功しました！",
        "ko": "작업이 성공했습니다!",
    },
    "system.cancelled": {
        "zh": "操作已取消。",
        "en": "Operation cancelled.",
        "ja": "操作がキャンセルされました。",
        "ko": "작업이 취소되었습니다.",
    },

    # Agent提示
    "agent.task_received": {
        "zh": "收到任务：{task}",
        "en": "Task received: {task}",
        "ja": "タスクを受け取りました：{task}",
        "ko": "작업 수신: {task}",
    },
    "agent.task_completed": {
        "zh": "任务完成！耗时 {duration}",
        "en": "Task completed! Duration: {duration}",
        "ja": "タスク完了！所要時間：{duration}",
        "ko": "작업 완료! 소요 시간: {duration}",
    },
    "agent.task_failed": {
        "zh": "任务失败：{error}",
        "en": "Task failed: {error}",
        "ja": "タスク失敗：{error}",
        "ko": "작업 실패: {error}",
    },
    "agent.tool_calling": {
        "zh": "正在调用工具：{tool}",
        "en": "Calling tool: {tool}",
        "ja": "ツールを呼び出し中：{tool}",
        "ko": "도구 호출 중: {tool}",
    },
    "agent.memory_saved": {
        "zh": "已保存到记忆。",
        "en": "Saved to memory.",
        "ja": "メモリに保存しました。",
        "ko": "메모리에 저장되었습니다.",
    },

    # 时间
    "time.seconds": {
        "zh": "{n}秒",
        "en": "{n} seconds",
        "ja": "{n}秒",
        "ko": "{n}초",
    },
    "time.minutes": {
        "zh": "{n}分钟",
        "en": "{n} minutes",
        "ja": "{n}分",
        "ko": "{n}분",
    },
    "time.hours": {
        "zh": "{n}小时",
        "en": "{n} hours",
        "ja": "{n}時間",
        "ko": "{n}시간",
    },
}


# ============================================================
# I18n 类
# ============================================================

class I18n:
    """国际化引擎

    用法:
        i18n = I18n()

        # 检测语言
        lang = i18n.detect("你好世界")  # → "zh"

        # 翻译
        text = i18n.t("system.welcome")  # 按当前locale
        text = i18n.t("system.welcome", lang="en")  # 指定语言

        # 带参数
        text = i18n.t("agent.task_received", task="写代码")

        # 设置默认语言
        i18n.set_locale("en")

        # 加载外部翻译
        i18n.load("translations.json")
    """

    SUPPORTED_LANGUAGES = {"zh", "en", "ja", "ko"}
    LANGUAGE_NAMES = {
        "zh": "中文",
        "en": "English",
        "ja": "日本語",
        "ko": "한국어",
    }

    def __init__(self, default_locale: str = "zh"):
        """
        Args:
            default_locale: 默认语言
        """
        self._locale = default_locale
        self._fallback = "en"
        self._translations: Dict[str, Dict[str, str]] = {}

        # 加载内置翻译
        self._translations.update(_BUILTIN_TRANSLATIONS)

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def detect(self, text: str) -> str:
        """检测文本语言"""
        return detect_language(text)

    def detect_detailed(self, text: str) -> Dict[str, Any]:
        """详细语言检测"""
        return detect_language_detailed(text)

    def set_locale(self, lang: str):
        """设置默认语言

        Args:
            lang: 语言代码 (zh/en/ja/ko)
        """
        if lang not in self.SUPPORTED_LANGUAGES:
            raise ValueError(
                f"不支持的语言: {lang}. 支持: {self.SUPPORTED_LANGUAGES}"
            )
        self._locale = lang

    def get_locale(self) -> str:
        """获取当前locale"""
        return self._locale

    def t(
        self,
        key: str,
        lang: Optional[str] = None,
        **kwargs,
    ) -> str:
        """翻译key

        Args:
            key: 翻译key（如 "system.welcome"）
            lang: 目标语言（None=使用当前locale）
            **kwargs: 模板参数

        Returns:
            翻译后的文本
        """
        lang = lang or self._locale

        # 查找翻译
        translations = self._translations.get(key, {})
        text = translations.get(lang)

        # 降级到fallback语言
        if text is None:
            text = translations.get(self._fallback)

        # 还是没有 → 返回key本身
        if text is None:
            return key

        # 模板替换
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass  # 参数不匹配就保留原文

        return text

    def translate_key(self, key: str, lang: str, **kwargs) -> str:
        """翻译key（t的别名，符合接口要求）"""
        return self.t(key, lang=lang, **kwargs)

    # ----------------------------------------------------------
    # 翻译管理
    # ----------------------------------------------------------

    def add(self, key: str, translations: Dict[str, str]):
        """添加翻译条目

        Args:
            key: 翻译key
            translations: {lang_code: text} 映射
        """
        if key not in self._translations:
            self._translations[key] = {}
        self._translations[key].update(translations)

    def add_batch(self, entries: Dict[str, Dict[str, str]]):
        """批量添加翻译"""
        for key, trans in entries.items():
            self.add(key, trans)

    def load(self, file_path: str):
        """从JSON文件加载翻译

        JSON格式:
        {
            "key1": {"zh": "...", "en": "..."},
            "key2": {"zh": "...", "en": "..."}
        }

        或平铺格式:
        {
            "zh": {"key1": "...", "key2": "..."},
            "en": {"key1": "...", "key2": "..."}
        }

        Args:
            file_path: JSON文件路径
        """
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"翻译文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("翻译文件格式错误")

        # 自动检测格式
        first_value = next(iter(data.values()), None)

        if isinstance(first_value, dict):
            # 检查是 key-first 还是 lang-first
            first_key = next(iter(data.keys()))
            if first_key in self.SUPPORTED_LANGUAGES:
                # 平铺格式: {"zh": {"key": "text"}, "en": {"key": "text"}}
                self._load_flat_format(data)
            else:
                # key-first格式: {"key": {"zh": "text", "en": "text"}}
                self.add_batch(data)
        elif isinstance(first_value, str):
            # 简单格式（单语言）: {"key": "text"}
            for key, text in data.items():
                self.add(key, {self._locale: text})

    def _load_flat_format(self, data: Dict[str, Dict[str, str]]):
        """加载平铺格式翻译"""
        for lang, entries in data.items():
            if not isinstance(entries, dict):
                continue
            for key, text in entries.items():
                self.add(key, {lang: text})

    def save(self, file_path: str, format: str = "key_first"):
        """保存翻译到JSON文件

        Args:
            file_path: 输出路径
            format: key_first 或 lang_first
        """
        if format == "lang_first":
            data: Dict[str, Dict[str, str]] = {}
            for key, trans in self._translations.items():
                for lang, text in trans.items():
                    data.setdefault(lang, {})[key] = text
        else:
            data = dict(self._translations)

        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------

    def has_key(self, key: str) -> bool:
        """是否存在某个翻译key"""
        return key in self._translations

    def keys(self, prefix: Optional[str] = None) -> List[str]:
        """列出所有key"""
        all_keys = list(self._translations.keys())
        if prefix:
            all_keys = [k for k in all_keys if k.startswith(prefix)]
        return sorted(all_keys)

    def languages_for_key(self, key: str) -> List[str]:
        """某个key支持的语言列表"""
        trans = self._translations.get(key, {})
        return sorted(trans.keys())

    def missing_translations(self, lang: str) -> List[str]:
        """找出某语言缺失的翻译"""
        missing = []
        for key, trans in self._translations.items():
            if lang not in trans:
                missing.append(key)
        return missing

    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        total_keys = len(self._translations)
        lang_counts: Dict[str, int] = {}
        for trans in self._translations.values():
            for lang in trans:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

        return {
            "total_keys": total_keys,
            "current_locale": self._locale,
            "languages": lang_counts,
            "supported": list(self.SUPPORTED_LANGUAGES),
        }

    # ----------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------

    def auto_translate(self, text: str, target_lang: Optional[str] = None) -> str:
        """自动翻译提示

        注意：这不是真正的翻译，只是根据检测到的语言
        返回对应的预设翻译。真正翻译需要LLM。

        Args:
            text: 输入文本
            target_lang: 目标语言

        Returns:
            如果找到匹配的key翻译则返回，否则返回原文
        """
        target = target_lang or self._locale
        # 尝试在已有翻译中查找匹配
        for key, trans in self._translations.items():
            for lang, t in trans.items():
                if t == text and target in trans:
                    return trans[target]
        return text

    def format_number(self, n: float, lang: Optional[str] = None) -> str:
        """格式化数字"""
        lang = lang or self._locale
        if lang == "zh":
            if n >= 100000000:
                return f"{n/100000000:.1f}亿"
            elif n >= 10000:
                return f"{n/10000:.1f}万"
        elif lang in ("en", "ja", "ko"):
            if n >= 1000000:
                return f"{n/1000000:.1f}M"
            elif n >= 1000:
                return f"{n/1000:.1f}K"
        return str(int(n)) if n == int(n) else f"{n:.2f}"


# ============================================================
# 便捷函数
# ============================================================

_default_i18n: Optional[I18n] = None


def get_i18n(**kwargs) -> I18n:
    """获取/创建默认I18n实例"""
    global _default_i18n
    if _default_i18n is None:
        _default_i18n = I18n(**kwargs)
    return _default_i18n


def t(key: str, **kwargs) -> str:
    """快速翻译"""
    return get_i18n().t(key, **kwargs)


def detect(text: str) -> str:
    """快速语言检测"""
    return detect_language(text)


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    # Fix Windows console encoding
    import sys
    if sys.stdout.encoding != "utf-8":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    i18n = I18n(default_locale="zh")

    print("=== 语言检测 ===")
    tests = [
        ("你好世界", "zh"),
        ("Hello World", "en"),
        ("こんにちは世界", "ja"),
        ("안녕하세요", "ko"),
        ("Hello 你好 World", "en"),  # 混合 - 英文字符多
    ]
    for text, expected in tests:
        result = i18n.detect(text)
        status = "OK" if result == expected else "FAIL"
        print(f"  {status} '{text}' -> {result} (expected {expected})")

    print("\n=== 翻译 ===")
    print(f"  zh: {i18n.t('system.welcome')}")
    print(f"  en: {i18n.t('system.welcome', lang='en')}")
    print(f"  ja: {i18n.t('system.welcome', lang='ja')}")
    print(f"  ko: {i18n.t('system.welcome', lang='ko')}")

    print("\n=== 带参数翻译 ===")
    print(f"  zh: {i18n.t('agent.task_received', task='写代码')}")
    print(f"  en: {i18n.t('agent.task_received', lang='en', task='Write code')}")

    print("\n=== 自定义翻译 ===")
    i18n.add("custom.greeting", {
        "zh": "嘿，{name}！",
        "en": "Hey, {name}!",
    })
    print(f"  zh: {i18n.t('custom.greeting', name='Alice')}")
    print(f"  en: {i18n.t('custom.greeting', lang='en', name='LingMing')}")

    print("\n=== 切换locale ===")
    i18n.set_locale("en")
    print(f"  locale=en: {i18n.t('system.welcome')}")
    i18n.set_locale("zh")
    print(f"  locale=zh: {i18n.t('system.welcome')}")

    print("\n=== 数字格式化 ===")
    print(f"  zh: {i18n.format_number(12345678)}")
    print(f"  en: {i18n.format_number(12345678, 'en')}")

    print(f"\n=== 统计 ===\n  {i18n.stats()}")
    print(f"\n=== 缺失翻译 ===\n  ja missing: {i18n.missing_translations('ja')[:5]}...")
