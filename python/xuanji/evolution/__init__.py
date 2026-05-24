"""xuanji 进化引擎子包

进化引擎、钩子系统、失败学习、模式复用。
"""

from .engine import EvolutionEngine
from .hook import EvolutionHook
from .hooks import HookManager, HookPoint
from .failure_learning import FailureLearner, ErrorCategory
from .pattern_reuse import PatternExtractor, PatternLibrary
from .learning import LearningEngine
from .antipattern import AntipatternDetector

__all__ = [
    "EvolutionEngine",
    "EvolutionHook",
    "HookManager",
    "HookPoint",
    "FailureLearner",
    "ErrorCategory",
    "PatternExtractor",
    "PatternLibrary",
    "LearningEngine",
    "AntipatternDetector",
]
