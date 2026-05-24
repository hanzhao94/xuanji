"""xuanji 专家人格库

20个内置专家人格，覆盖工程/游戏/设计/测试/管理/AI六大领域。
与TeamEngine对接：team角色自动匹配专家人格。

兼容旧接口，直接导入即可使用。
"""

from xuanji.persona_data import (
    ExpertPersona,
    BUILTIN_PERSONAS,
    TEAM_TEMPLATES,
)
from xuanji.persona_library import PersonaLibrary

__all__ = [
    "ExpertPersona", "PersonaLibrary",
    "BUILTIN_PERSONAS", "TEAM_TEMPLATES",
]