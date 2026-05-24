"""
xuanji 统一错误码体系

用法:
  from xuanji.error_codes import ErrorCode, get_error, get_suggestion

  err = get_error("LLM-001")
  print(err.description)
  print(get_suggestion("LLM-001"))

错误分类:
  LAUNCH    — 启动相关
  LLM       — 大语言模型相关
  MEMORY    — 记忆系统相关
  TOOL      — 工具调用相关
  SECURITY  — 安全相关
  NETWORK   — 网络相关
  CONFIG    — 配置相关
  PERFORMANCE — 性能相关
"""

from typing import Dict, Optional


class ErrorCode:
    """错误码定义"""

    def __init__(self, code: str, name: str, category: str,
                 description: str, causes: str, suggestion: str, severity: str = "warning"):
        self.code = code
        self.name = name
        self.category = category
        self.description = description
        self.causes = causes
        self.suggestion = suggestion
        self.severity = severity  # info / warning / error / critical

    def __str__(self):
        return f"[{self.code}] {self.name}: {self.description}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "causes": self.causes,
            "suggestion": self.suggestion,
            "severity": self.severity,
        }


# ============================================================
# 错误码注册表
# ============================================================

_ERROR_REGISTRY: Dict[str, ErrorCode] = {}


def _register(code: str, name: str, category: str,
              description: str, causes: str, suggestion: str, severity: str = "warning"):
    err = ErrorCode(code, name, category, description, causes, suggestion, severity)
    _ERROR_REGISTRY[code] = err
    return err


# ============================================================
# LAUNCH — 启动相关 (LAUNCH-001 ~ LAUNCH-015)
# ============================================================

_register("LAUNCH-001", "启动失败", "LAUNCH",
    "Agent启动失败",
    "配置文件不存在或格式错误；依赖缺失；端口被占用",
    "1. 检查 config.toml 是否存在且格式正确\n2. 运行 xuanji status 查看依赖状态\n3. 检查端口是否被其他进程占用",
    "critical")

_register("LAUNCH-002", "插件加载失败", "LAUNCH",
    "无法加载指定插件",
    "插件目录不存在；plugin.toml格式错误；entry模块导入失败",
    "1. 确认插件目录存在且包含 plugin.toml\n2. 检查 plugin.toml 的 entry 字段格式\n3. 查看完整错误堆栈定位导入问题",
    "error")

_register("LAUNCH-003", "端口冲突", "LAUNCH",
    "指定端口已被占用",
    "另一个进程已绑定该端口；之前的实例未正常退出",
    "1. 运行 lsof -i :<端口> 查看占用进程\n2. 修改 config.toml 中的端口配置\n3. 或 kill 掉旧进程",
    "warning")

_register("LAUNCH-004", "依赖缺失", "LAUNCH",
    "缺少必要的Python依赖包",
    "requirements.txt未安装；环境不一致",
    "运行 pip install -r requirements.txt 安装依赖",
    "error")

_register("LAUNCH-005", "权限不足", "LAUNCH",
    "缺少文件或目录访问权限",
    "文件权限设置不正确；以非特权用户运行需要特权操作",
    "1. 检查文件权限: ls -la <路径>\n2. 使用 chmod 修改权限\n3. 或以有权限的用户运行",
    "error")

_register("LAUNCH-006", "工作目录不存在", "LAUNCH",
    "配置的工作目录不存在",
    "目录被删除或路径配置错误",
    "1. 检查 config.toml 中的路径配置\n2. 创建缺失的目录",
    "warning")

_register("LAUNCH-007", "版本不兼容", "LAUNCH",
    "插件版本与框架不兼容",
    "插件声明的版本超出框架支持范围",
    "1. 升级框架: pip install --upgrade xuanji\n2. 或降级插件版本",
    "error")

_register("LAUNCH-008", "初始化钩子失败", "LAUNCH",
    "插件的 on_init 钩子执行失败",
    "钩子函数抛出异常；依赖资源未就绪",
    "1. 查看插件的 on_init 实现\n2. 检查钩子中的日志输出",
    "error")

_register("LAUNCH-009", "运行时冲突", "LAUNCH",
    "多个运行时实例冲突",
    "同一配置启动了多个实例；锁文件未清理",
    "1. 检查是否有旧实例运行\n2. 删除锁文件: rm ~/.xuanji/runtime.lock",
    "warning")

_register("LAUNCH-010", "信号处理失败", "LAUNCH",
    "无法注册信号处理器",
    "在Windows上某些信号不支持；权限问题",
    "通常不影响核心功能，可忽略。如需完整信号支持，使用Unix系统。",
    "info")

# ============================================================
# LLM — 大语言模型相关 (LLM-001 ~ LLM-020)
# ============================================================

_register("LLM-001", "API密钥无效", "LLM",
    "LLM API密钥无效或已过期",
    "密钥输入错误；密钥已过期或被撤销",
    "1. 检查 config.toml 中的密钥是否正确\n2. 到LLM服务商控制台重新生成密钥\n3. 确认密钥格式正确（如 sk- 前缀）",
    "critical")

_register("LLM-002", "请求超时", "LLM",
    "LLM API请求超时",
    "网络延迟；服务端负载高；请求内容过大",
    "1. 检查网络连接\n2. 增加超时配置: timeout = 60\n3. 减少请求内容长度",
    "warning")

_register("LLM-003", "速率限制", "LLM",
    "触发LLM API速率限制",
    "请求频率超过配额；并发请求过多",
    "1. 降低请求频率\n2. 配置速率限制: [rate_limit] requests_per_minute = 60\n3. 使用多个API密钥轮换",
    "warning")

_register("LLM-004", "Token超限", "LLM",
    "请求Token数超过模型上限",
    "上下文过长；系统提示词过大",
    "1. 使用 memory 模块管理上下文长度\n2. 启用上下文压缩\n3. 切换到支持更长上下文的模型",
    "error")

_register("LLM-005", "模型不存在", "LLM",
    "指定的模型名称不存在",
    "模型名称拼写错误；模型已下线",
    "1. 检查模型名称是否正确\n2. 查看LLM服务商的模型列表\n3. 更新到可用模型",
    "error")

_register("LLM-006", "响应解析失败", "LLM",
    "无法解析LLM返回的响应",
    "响应格式异常；网络截断；编码问题",
    "1. 检查网络是否稳定\n2. 查看原始响应内容\n3. 重试请求",
    "warning")

_register("LLM-007", "余额不足", "LLM",
    "LLM账户余额不足",
    "账户欠费；配额用尽",
    "1. 登录LLM服务商控制台查看余额\n2. 充值或升级套餐\n3. 切换到备用LLM",
    "critical")

_register("LLM-008", "服务不可用", "LLM",
    "LLM服务暂时不可用",
    "服务商维护；区域故障",
    "1. 查看服务商状态页面\n2. 等待恢复后重试\n3. 配置fallback到其他LLM",
    "error")

_register("LLM-009", "内容过滤", "LLM",
    "请求内容被LLM服务过滤",
    "触发内容安全策略；包含敏感词",
    "1. 修改提示词避免敏感内容\n2. 检查是否误触发过滤\n3. 联系服务商确认政策",
    "warning")

_register("LLM-010", "流式响应中断", "LLM",
    "流式响应中途断开",
    "网络不稳定；服务端异常；客户端超时",
    "1. 检查网络连接\n2. 增加超时时间\n3. 启用自动重试机制",
    "warning")

_register("LLM-011", "工具调用格式错误", "LLM",
    "LLM返回的工具调用参数格式错误",
    "模型输出不符合schema；JSON解析失败",
    "1. 检查工具schema定义\n2. 在提示词中明确参数格式\n3. 启用参数验证和重试",
    "warning")

_register("LLM-012", "多轮对话上下文丢失", "LLM",
    "多轮对话中上下文信息丢失",
    "会话ID不匹配；记忆模块异常",
    "1. 检查会话管理逻辑\n2. 确认memory模块正常工作\n3. 查看会话ID是否一致",
    "error")

_register("LLM-013", "嵌入向量维度不匹配", "LLM",
    "向量嵌入的维度与预期不符",
    "更换了嵌入模型；模型版本不一致",
    "1. 确认使用的嵌入模型一致\n2. 重新生成向量索引\n3. 检查模型版本",
    "error")

_register("LLM-014", "并发请求过多", "LLM",
    "同时发起的LLM请求超过限制",
    "并发配置过高；没有请求队列",
    "1. 降低并发数配置\n2. 启用请求队列\n3. 使用asyncio semaphore限制并发",
    "warning")

_register("LLM-015", "响应内容截断", "LLM",
    "LLM响应被截断，内容不完整",
    "max_tokens设置过小；输出超出限制",
    "1. 增加 max_tokens 配置\n2. 在提示词中要求简洁回答\n3. 使用流式响应",
    "warning")

# ============================================================
# MEMORY — 记忆系统相关 (MEMORY-001 ~ MEMORY-015)
# ============================================================

_register("MEMORY-001", "记忆存储不可用", "MEMORY",
    "记忆存储后端无法连接",
    "数据库未启动；连接配置错误；权限不足",
    "1. 检查数据库服务是否运行\n2. 验证连接配置\n3. 确认数据库用户权限",
    "critical")

_register("MEMORY-002", "记忆写入失败", "MEMORY",
    "无法写入记忆数据",
    "磁盘空间不足；数据库写入锁；数据格式错误",
    "1. 检查磁盘空间\n2. 确认数据库状态\n3. 验证数据格式",
    "error")

_register("MEMORY-003", "记忆读取失败", "MEMORY",
    "无法读取记忆数据",
    "数据损坏；查询条件错误；索引失效",
    "1. 检查数据完整性\n2. 验证查询语句\n3. 重建索引",
    "error")

_register("MEMORY-004", "记忆溢出", "MEMORY",
    "记忆存储超出容量限制",
    "未配置清理策略；数据量增长过快",
    "1. 配置自动清理策略\n2. 增加存储容量\n3. 启用数据压缩",
    "warning")

_register("MEMORY-005", "向量检索失败", "MEMORY",
    "向量相似度检索失败",
    "向量索引损坏；查询向量维度不匹配",
    "1. 重建向量索引\n2. 确认查询向量维度一致\n3. 检查向量数据库状态",
    "error")

_register("MEMORY-006", "会话ID冲突", "MEMORY",
    "多个会话使用相同ID",
    "ID生成算法冲突；并发创建会话",
    "1. 使用UUID生成会话ID\n2. 添加时间戳前缀\n3. 检查并发控制",
    "warning")

_register("MEMORY-007", "记忆过期清理失败", "MEMORY",
    "自动清理过期记忆时出错",
    "清理任务冲突；数据库锁；权限问题",
    "1. 检查清理任务日志\n2. 确认数据库权限\n3. 手动清理后重试",
    "warning")

_register("MEMORY-008", "RAG检索超时", "MEMORY",
    "RAG检索耗时过长",
    "知识库过大；索引未优化；查询复杂",
    "1. 优化知识库索引\n2. 增加检索超时配置\n3. 使用缓存减少重复检索",
    "warning")

_register("MEMORY-009", "记忆碎片化", "MEMORY",
    "记忆数据碎片化严重",
    "频繁的小规模写入；未定期整理",
    "1. 定期执行记忆整理\n2. 合并相似记忆条目\n3. 批量写入替代频繁写入",
    "info")

_register("MEMORY-010", "上下文窗口溢出", "MEMORY",
    "对话上下文超出模型窗口限制",
    "对话过长；未启用上下文压缩",
    "1. 启用上下文摘要压缩\n2. 保留关键历史信息\n3. 使用滑动窗口策略",
    "error")

# ============================================================
# TOOL — 工具调用相关 (TOOL-001 ~ TOOL-015)
# ============================================================

_register("TOOL-001", "工具不存在", "TOOL",
    "请求的工具未注册",
    "工具名称拼写错误；工具未安装；插件未加载",
    "1. 检查工具名称是否正确\n2. 确认工具插件已安装\n3. 运行 xuanji status 查看已加载工具",
    "error")

_register("TOOL-002", "工具执行超时", "TOOL",
    "工具执行时间超过限制",
    "工具逻辑复杂；外部服务慢；死循环",
    "1. 增加工具超时配置\n2. 优化工具实现\n3. 添加超时中断机制",
    "warning")

_register("TOOL-003", "工具参数错误", "TOOL",
    "工具调用参数不符合schema",
    "LLM生成的参数格式错误；缺少必填参数",
    "1. 优化工具的schema定义\n2. 在提示词中说明参数要求\n3. 启用参数自动修复",
    "warning")

_register("TOOL-004", "工具权限不足", "TOOL",
    "工具执行需要更高权限",
    "沙箱限制；文件系统权限；网络访问限制",
    "1. 检查安全配置\n2. 授予工具所需权限\n3. 确认沙箱策略",
    "error")

_register("TOOL-005", "工具返回异常", "TOOL",
    "工具返回了异常结果",
    "工具内部错误；外部依赖异常",
    "1. 查看工具的错误日志\n2. 检查外部依赖状态\n3. 重试或启用fallback",
    "error")

_register("TOOL-006", "MCP连接失败", "TOOL",
    "无法连接到MCP Server",
    "MCP Server未启动；端口错误；协议不兼容",
    "1. 确认MCP Server正在运行\n2. 检查连接配置\n3. 验证协议版本兼容性",
    "error")

_register("TOOL-007", "工具调用死循环", "TOOL",
    "工具调用形成循环依赖",
    "工具A调用工具B，工具B又调用工具A",
    "1. 检查工具调用链\n2. 设置最大调用深度\n3. 添加调用图检测",
    "critical")

_register("TOOL-008", "工具输出过大", "TOOL",
    "工具返回结果超出限制",
    "文件读取过大；查询返回过多数据",
    "1. 限制工具输出大小\n2. 使用分页或流式输出\n3. 添加数据过滤",
    "warning")

_register("TOOL-009", "工具状态不一致", "TOOL",
    "工具内部状态与预期不符",
    "并发调用导致状态冲突；未正确初始化",
    "1. 添加状态锁\n2. 确保工具幂等性\n3. 检查初始化流程",
    "warning")

_register("TOOL-010", "文件操作失败", "TOOL",
    "文件读写操作失败",
    "文件不存在；权限不足；路径错误",
    "1. 检查文件路径和权限\n2. 确认文件存在\n3. 使用绝对路径避免歧义",
    "error")

# ============================================================
# SECURITY — 安全相关 (SECURITY-001 ~ SECURITY-015)
# ============================================================

_register("SECURITY-001", "未授权访问", "SECURITY",
    "检测到未授权的访问尝试",
    "RBAC权限配置错误；Token过期；IP未白名单",
    "1. 检查RBAC配置\n2. 刷新认证Token\n3. 添加IP到白名单",
    "critical")

_register("SECURITY-002", "注入攻击检测", "SECURITY",
    "检测到潜在的注入攻击",
    "用户输入包含SQL/命令注入特征",
    "1. 启用输入过滤\n2. 使用参数化查询\n3. 记录攻击日志并告警",
    "critical")

_register("SECURITY-003", "敏感数据泄露", "SECURITY",
    "检测到敏感数据可能泄露",
    "日志中包含密钥；响应中包含隐私数据",
    "1. 启用日志脱敏\n2. 检查输出过滤规则\n3. 审计数据流向",
    "critical")

_register("SECURITY-004", "沙箱逃逸", "SECURITY",
    "检测到沙箱逃逸尝试",
    "恶意插件尝试访问受限资源",
    "1. 立即终止插件执行\n2. 检查沙箱配置\n3. 审查插件来源",
    "critical")

_register("SECURITY-005", "Token泄露", "SECURITY",
    "API Token可能已泄露",
    "Token出现在日志中；被未授权方获取",
    "1. 立即轮换Token\n2. 检查日志脱敏配置\n3. 启用Token加密存储",
    "critical")

_register("SECURITY-006", "越权操作", "SECURITY",
    "检测到越权操作尝试",
    "用户尝试访问超出权限范围的资源",
    "1. 检查权限配置\n2. 审计用户操作日志\n3. 强化权限验证",
    "error")

_register("SECURITY-007", "恶意内容检测", "SECURITY",
    "检测到恶意内容输入",
    "包含恶意代码；钓鱼内容；有害信息",
    "1. 启用内容安全过滤\n2. 记录安全事件\n3. 必要时阻断来源",
    "error")

_register("SECURITY-008", "频率滥用", "SECURITY",
    "检测到频率滥用行为",
    "短时间内大量请求；可能的DDoS攻击",
    "1. 启用速率限制\n2. 添加IP封禁\n3. 配置告警通知",
    "warning")

_register("SECURITY-009", "插件安全扫描失败", "SECURITY",
    "插件安全扫描发现风险",
    "插件包含危险操作；未签名；来源不可信",
    "1. 审查插件代码\n2. 要求插件签名\n3. 使用认证插件市场",
    "error")

_register("SECURITY-010", "数据加密失败", "SECURITY",
    "数据加密/解密失败",
    "密钥丢失；加密算法不兼容",
    "1. 检查密钥管理\n2. 确认加密算法一致性\n3. 备份加密数据",
    "error")

# ============================================================
# NETWORK — 网络相关 (NETWORK-001 ~ NETWORK-015)
# ============================================================

_register("NETWORK-001", "连接超时", "NETWORK",
    "网络连接超时",
    "网络不通；目标服务不可达；防火墙阻止",
    "1. 检查网络连接\n2. ping 目标地址\n3. 检查防火墙规则",
    "error")

_register("NETWORK-002", "DNS解析失败", "NETWORK",
    "域名解析失败",
    "DNS配置错误；域名不存在",
    "1. 检查DNS配置\n2. 使用 nslookup 排查\n3. 尝试使用IP地址",
    "error")

_register("NETWORK-003", "SSL证书错误", "NETWORK",
    "SSL/TLS证书验证失败",
    "证书过期；自签名证书；域名不匹配",
    "1. 更新SSL证书\n2. 添加CA信任\n3. 临时禁用验证（不推荐生产环境）",
    "error")

_register("NETWORK-004", "WebSocket断开", "NETWORK",
    "WebSocket连接意外断开",
    "网络波动；服务端重启；超时",
    "1. 启用自动重连\n2. 配置心跳检测\n3. 检查服务端状态",
    "warning")

_register("NETWORK-005", "代理配置错误", "NETWORK",
    "代理服务器配置不正确",
    "代理地址错误；认证失败；代理不支持",
    "1. 检查代理配置\n2. 验证代理认证信息\n3. 测试代理连通性",
    "error")

_register("NETWORK-006", "请求被拒绝", "NETWORK",
    "HTTP请求被目标服务器拒绝",
    "IP被封禁；请求频率过高；需要认证",
    "1. 检查目标服务器状态\n2. 添加认证信息\n3. 降低请求频率",
    "warning")

_register("NETWORK-007", "数据传输中断", "NETWORK",
    "数据传输过程中断",
    "网络不稳定；连接超时；数据包丢失",
    "1. 启用断点续传\n2. 增加重试机制\n3. 检查网络质量",
    "warning")

_register("NETWORK-008", "端口不可达", "NETWORK",
    "目标端口无法连接",
    "端口未开放；服务未启动；防火墙拦截",
    "1. 检查目标服务是否运行\n2. 确认端口号正确\n3. 检查防火墙规则",
    "error")

# ============================================================
# CONFIG — 配置相关 (CONFIG-001 ~ CONFIG-010)
# ============================================================

_register("CONFIG-001", "配置文件不存在", "CONFIG",
    "找不到配置文件",
    "文件被删除；路径配置错误；未初始化",
    "1. 运行 xuanji init 创建项目\n2. 检查配置文件路径\n3. 确认文件权限",
    "error")

_register("CONFIG-002", "配置格式错误", "CONFIG",
    "配置文件格式不正确",
    "TOML语法错误；缺少必填字段；类型不匹配",
    "1. 使用TOML验证工具检查语法\n2. 参考示例配置\n3. 查看具体错误行号",
    "error")

_register("CONFIG-003", "配置值无效", "CONFIG",
    "配置项的值超出有效范围",
    "数值超出范围；枚举值不正确；路径不存在",
    "1. 查看配置项的取值范围\n2. 修正配置值\n3. 参考文档确认格式",
    "warning")

_register("CONFIG-004", "配置冲突", "CONFIG",
    "多个配置项之间存在冲突",
    "重复配置；互斥配置同时启用",
    "1. 检查配置项之间的依赖关系\n2. 移除冲突配置\n3. 查看配置优先级",
    "warning")

_register("CONFIG-005", "环境变量缺失", "CONFIG",
    "必需的环境变量未设置",
    "环境变量未导出；.env文件缺失",
    "1. 检查 .env 文件是否存在\n2. 导出必需的环境变量\n3. 查看配置文档确认所需变量",
    "error")

_register("CONFIG-006", "配置热更新失败", "CONFIG",
    "热更新配置时出错",
    "新配置格式错误；更新过程中配置被修改",
    "1. 验证新配置格式\n2. 确保更新过程中无并发修改\n3. 回滚到旧配置",
    "warning")

# ============================================================
# PERFORMANCE — 性能相关 (PERF-001 ~ PERF-015)
# ============================================================

_register("PERF-001", "内存使用过高", "PERFORMANCE",
    "内存使用量超过阈值",
    "内存泄漏；缓存过大；并发过高",
    "1. 启用内存监控\n2. 限制缓存大小\n3. 降低并发数\n4. 检查是否存在内存泄漏",
    "warning")

_register("PERF-002", "CPU使用过高", "PERFORMANCE",
    "CPU使用率持续过高",
    "计算密集型任务；死循环；并发过多",
    "1. 使用性能分析工具定位热点\n2. 优化算法\n3. 限制并发数",
    "warning")

_register("PERF-003", "响应延迟过高", "PERFORMANCE",
    "请求响应时间超过SLA",
    "上游服务慢；数据库查询慢；队列积压",
    "1. 使用性能分析工具\n2. 优化慢查询\n3. 增加缓存层\n4. 扩展服务实例",
    "warning")

_register("PERF-004", "磁盘IO瓶颈", "PERFORMANCE",
    "磁盘读写成为性能瓶颈",
    "频繁的小文件读写；未使用缓存；磁盘慢",
    "1. 使用内存缓存减少磁盘IO\n2. 批量写入替代频繁写入\n3. 使用SSD",
    "warning")

_register("PERF-005", "线程池耗尽", "PERFORMANCE",
    "线程池已满，新任务被拒绝",
    "并发任务过多；线程池配置过小；任务阻塞",
    "1. 增加线程池大小\n2. 优化任务执行时间\n3. 添加任务队列",
    "error")

_register("PERF-006", "连接池耗尽", "PERFORMANCE",
    "数据库/HTTP连接池已满",
    "连接未正确释放；连接池配置过小；慢查询",
    "1. 确保连接正确关闭\n2. 增加连接池大小\n3. 优化查询性能",
    "error")

_register("PERF-007", "GC频繁触发", "PERFORMANCE",
    "垃圾回收频繁触发影响性能",
    "大量短生命周期对象；内存压力大",
    "1. 减少对象创建\n2. 使用对象池\n3. 调整GC参数",
    "info")

_register("PERF-008", "日志写入瓶颈", "PERFORMANCE",
    "日志写入影响主流程性能",
    "同步日志写入；日志量过大；磁盘慢",
    "1. 使用异步日志\n2. 调整日志级别\n3. 使用日志采样",
    "info")

_register("PERF-009", "序列化开销大", "PERFORMANCE",
    "JSON/消息序列化耗时过长",
    "数据量大；序列化库效率低",
    "1. 减少序列化数据量\n2. 使用更快的序列化库\n3. 缓存序列化结果",
    "info")

_register("PERF-010", "上下文切换频繁", "PERFORMANCE",
    "Agent上下文切换过于频繁",
    "多Agent协作设计不合理；频繁切换角色",
    "1. 优化Agent协作流程\n2. 减少不必要的上下文切换\n3. 使用长上下文保持",
    "info")


# ============================================================
# 公共API
# ============================================================

def get_error(code: str) -> Optional[ErrorCode]:
    """根据错误码获取错误详情"""
    return _ERROR_REGISTRY.get(code.upper())


def get_suggestion(code: str) -> str:
    """获取错误修复建议"""
    err = get_error(code)
    return err.suggestion if err else "未知错误码"


def get_errors_by_category(category: str) -> list:
    """按分类获取所有错误码"""
    return [e for e in _ERROR_REGISTRY.values() if e.category == category.upper()]


def list_all_codes() -> list:
    """列出所有错误码"""
    return sorted(_ERROR_REGISTRY.keys())


def search_errors(keyword: str) -> list:
    """搜索错误码（模糊匹配名称和描述）"""
    keyword = keyword.lower()
    results = []
    for err in _ERROR_REGISTRY.values():
        if keyword in err.name.lower() or keyword in err.description.lower() or keyword in err.causes.lower():
            results.append(err)
    return results


def format_error(err: ErrorCode, verbose: bool = False) -> str:
    """格式化错误信息"""
    lines = [f"❌ [{err.code}] {err.name}", f"   {err.description}"]
    if verbose:
        lines.append(f"   原因: {err.causes}")
        lines.append(f"   修复: {err.suggestion}")
        lines.append(f"   严重度: {err.severity}")
    return "\n".join(lines)


def create_error(code: str, message: str) -> dict:
    """创建标准错误响应"""
    err = get_error(code)
    if err:
        return {
            "error": True,
            "code": err.code,
            "name": err.name,
            "message": message or err.description,
            "suggestion": err.suggestion,
            "severity": err.severity,
        }
    return {
        "error": True,
        "code": code,
        "name": "未知错误",
        "message": message,
        "suggestion": "查看文档或联系管理员",
        "severity": "error",
    }
