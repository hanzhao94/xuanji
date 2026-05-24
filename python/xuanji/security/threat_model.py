"""
L0 威胁建模层 — 基于STRIDE模型识别和分析潜在威胁

识别威胁：外部攻击/内部滥用/配置错误/依赖漏洞
"""

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class ThreatModel:
    """威胁建模分析器
    
    基于STRIDE模型：
    - S: Spoofing（身份伪造）
    - T: Tampering（数据篡改）
    - R: Repudiation（抵赖）
    - I: Information Disclosure（信息泄露）
    - D: Denial of Service（拒绝服务）
    - E: Elevation of Privilege（权限提升）
    
    Usage:
        tm = ThreatModel()
        
        # 定义系统组件
        tm.add_component("api_server", "外部API接口", "internet")
        tm.add_component("agent_runtime", "Agent执行引擎", "internal")
        tm.add_component("fs_sandbox", "文件系统沙箱", "internal")
        tm.add_component("audit_log", "审计日志", "internal")
        
        # 定义数据流
        tm.add_flow("user -> api_server", "用户请求", "internet")
        tm.add_flow("api_server -> agent_runtime", "任务分发", "internal")
        tm.add_flow("agent_runtime -> fs_sandbox", "文件操作", "internal")
        
        # 分析威胁
        threats = tm.analyze()
        for t in threats:
            print(f"{t['stride']}: {t['description']}")
    """
    
    # === STRIDE威胁模板 ===
    
    _THREAT_TEMPLATES = {
        "S": [  # Spoofing
            {
                "id": "S-001",
                "name": "身份伪造攻击",
                "description": "攻击者伪造Agent身份执行未授权操作",
                "conditions": ["api_server"],
                "mitigations": [
                    "实施强身份认证（JWT + 签名验证）",
                    "Agent ID与IP/MAC绑定",
                    "定期轮换认证凭证",
                ],
            },
            {
                "id": "S-002",
                "name": "中间人攻击",
                "description": "攻击者拦截并篡改通信数据",
                "conditions": ["api_server", "agent_runtime"],
                "mitigations": [
                    "强制TLS 1.3加密通信",
                    "实施证书固定（Certificate Pinning）",
                    "通信数据完整性校验",
                ],
            },
            {
                "id": "S-003",
                "name": "DNS欺骗",
                "description": "攻击者篡改DNS解析指向恶意服务器",
                "conditions": ["api_server"],
                "mitigations": [
                    "使用DNSSEC验证",
                    "配置DNS over HTTPS",
                    "硬编码关键服务IP",
                ],
            },
        ],
        "T": [  # Tampering
            {
                "id": "T-001",
                "name": "配置文件篡改",
                "description": "攻击者修改安全配置文件降低防护等级",
                "conditions": ["fs_sandbox"],
                "mitigations": [
                    "配置文件只读权限（root only）",
                    "配置变更审计和告警",
                    "配置文件签名验证",
                ],
            },
            {
                "id": "T-002",
                "name": "审计日志篡改",
                "description": "攻击者删除或修改审计日志掩盖行为",
                "conditions": ["audit_log"],
                "mitigations": [
                    "Append-only日志（不可修改）",
                    "日志实时同步到远程存储",
                    "日志哈希链验证",
                ],
            },
            {
                "id": "T-003",
                "name": "数据注入攻击",
                "description": "攻击者注入恶意数据污染Agent上下文",
                "conditions": ["agent_runtime"],
                "mitigations": [
                    "输入数据严格消毒",
                    "上下文隔离（沙箱化）",
                    "输出数据过滤和验证",
                ],
            },
        ],
        "R": [  # Repudiation
            {
                "id": "R-001",
                "name": "操作抵赖",
                "description": "Agent否认执行了某项危险操作",
                "conditions": ["audit_log", "agent_runtime"],
                "mitigations": [
                    "不可篡改的审计日志",
                    "操作前强制确认记录",
                    "日志分布式存储",
                ],
            },
            {
                "id": "R-002",
                "name": "日志删除",
                "description": "攻击者删除日志文件消除痕迹",
                "conditions": ["audit_log"],
                "mitigations": [
                    "日志实时备份到远程",
                    "文件不可删除属性（chattr +a）",
                    "日志删除尝试告警",
                ],
            },
        ],
        "I": [  # Information Disclosure
            {
                "id": "I-001",
                "name": "敏感数据泄露",
                "description": "Agent访问并外传敏感文件（密钥/密码/个人信息）",
                "conditions": ["fs_sandbox", "agent_runtime"],
                "mitigations": [
                    "文件系统沙箱严格隔离",
                    "敏感文件模式匹配拦截",
                    "数据外发内容检测",
                ],
            },
            {
                "id": "I-002",
                "name": "内存数据泄露",
                "description": "通过内存dump获取敏感信息",
                "conditions": ["agent_runtime"],
                "mitigations": [
                    "敏感数据使用后立即清零",
                    "内存加密存储",
                    "禁用core dump",
                ],
            },
            {
                "id": "I-003",
                "name": "侧信道攻击",
                "description": "通过时间/功耗/缓存等侧信道获取信息",
                "conditions": ["agent_runtime"],
                "mitigations": [
                    "恒定时间比较算法",
                    "随机化延迟",
                    "缓存隔离",
                ],
            },
            {
                "id": "I-004",
                "name": "提示注入泄露",
                "description": "通过提示注入获取系统提示词或内部信息",
                "conditions": ["agent_runtime"],
                "mitigations": [
                    "提示词加密存储",
                    "输入内容安全过滤",
                    "提示注入检测机制",
                ],
            },
        ],
        "D": [  # Denial of Service
            {
                "id": "D-001",
                "name": "资源耗尽攻击",
                "description": "Agent被操控执行资源密集型操作耗尽系统资源",
                "conditions": ["agent_runtime"],
                "mitigations": [
                    "资源使用配额限制",
                    "操作频率限制（Rate Limiting）",
                    "异常行为自动终止",
                ],
            },
            {
                "id": "D-002",
                "name": "磁盘填满攻击",
                "description": "大量写入操作填满磁盘导致服务不可用",
                "conditions": ["fs_sandbox"],
                "mitigations": [
                    "磁盘配额限制",
                    "磁盘使用量监控告警",
                    "自动清理临时文件",
                ],
            },
            {
                "id": "D-003",
                "name": "API滥用",
                "description": "大量API调用导致外部服务不可用或费用激增",
                "conditions": ["api_server"],
                "mitigations": [
                    "API调用频率限制",
                    "费用上限设置",
                    "异常调用模式检测",
                ],
            },
        ],
        "E": [  # Elevation of Privilege
            {
                "id": "E-001",
                "name": "权限提升攻击",
                "description": "Agent尝试获取超出授权范围的权限",
                "conditions": ["agent_runtime", "fs_sandbox"],
                "mitigations": [
                    "最小权限原则",
                    "权限变更审计",
                    "权限提升尝试告警",
                ],
            },
            {
                "id": "E-002",
                "name": "沙箱逃逸",
                "description": "Agent突破沙箱限制访问系统资源",
                "conditions": ["fs_sandbox"],
                "mitigations": [
                    "多层沙箱防护",
                    "系统调用过滤（seccomp）",
                    "容器化隔离",
                ],
            },
            {
                "id": "E-003",
                "name": "配置降级",
                "description": "Agent修改配置降低安全级别",
                "conditions": ["fs_sandbox"],
                "mitigations": [
                    "配置变更需管理员确认",
                    "安全配置锁定",
                    "配置回滚机制",
                ],
            },
        ],
    }
    
    def __init__(self):
        self._components: Dict[str, Dict] = {}
        self._flows: List[Dict] = []
        self._custom_threats: List[Dict] = []
        self._trust_boundaries: List[Dict] = []
    
    def add_component(self, name: str, description: str, trust_zone: str = "internal") -> None:
        """添加系统组件
        
        Args:
            name: 组件名称
            description: 组件描述
            trust_zone: 信任区域 (internet/internal/dmz/trusted)
        """
        self._components[name] = {
            "name": name,
            "description": description,
            "trust_zone": trust_zone,
        }
    
    def add_flow(self, flow: str, description: str, trust_zone: str = "internal") -> None:
        """添加数据流
        
        Args:
            flow: 数据流描述（如 "user -> api_server"）
            description: 数据流描述
            trust_zone: 信任区域
        """
        self._flows.append({
            "flow": flow,
            "description": description,
            "trust_zone": trust_zone,
        })
        
        # 自动识别信任边界
        parts = flow.split("->")
        if len(parts) == 2:
            src = parts[0].strip()
            dst = parts[1].strip()
            src_zone = self._components.get(src, {}).get("trust_zone", trust_zone)
            dst_zone = self._components.get(dst, {}).get("trust_zone", trust_zone)
            if src_zone != dst_zone:
                self._trust_boundaries.append({
                    "source": src,
                    "destination": dst,
                    "source_zone": src_zone,
                    "destination_zone": dst_zone,
                })
    
    def add_custom_threat(
        self,
        stride: str,
        name: str,
        description: str,
        components: List[str],
        mitigations: List[str],
        likelihood: str = "medium",
        impact: str = "medium",
    ) -> None:
        """添加自定义威胁
        
        Args:
            stride: STRIDE类型 (S/T/R/I/D/E)
            name: 威胁名称
            description: 威胁描述
            components: 相关组件列表
            mitigations: 缓解措施
            likelihood: 可能性 (low/medium/high)
            impact: 影响程度 (low/medium/high/critical)
        """
        self._custom_threats.append({
            "stride": stride,
            "name": name,
            "description": description,
            "components": components,
            "mitigations": mitigations,
            "likelihood": likelihood,
            "impact": impact,
            "custom": True,
        })
    
    def analyze(self) -> List[Dict]:
        """执行威胁分析
        
        Returns:
            威胁列表，每项包含：
            {
                "id": str,
                "stride": str,
                "stride_name": str,
                "name": str,
                "description": str,
                "components": [str],
                "likelihood": str,
                "impact": str,
                "risk_score": int,
                "mitigations": [str],
            }
        """
        threats = []
        
        # 分析模板威胁
        for stride_type, templates in self._THREAT_TEMPLATES.items():
            for template in templates:
                # 检查条件是否满足（组件是否存在）
                if self._conditions_met(template.get("conditions", [])):
                    threat = self._build_threat(stride_type, template)
                    threats.append(threat)
        
        # 添加自定义威胁
        for custom in self._custom_threats:
            if self._conditions_met(custom.get("components", [])):
                threat = self._build_custom_threat(custom)
                threats.append(threat)
        
        # 按风险评分排序
        threats.sort(key=lambda t: t["risk_score"], reverse=True)
        
        return threats
    
    def get_threats_by_stride(self) -> Dict[str, List[Dict]]:
        """按STRIDE类型分组"""
        threats = self.analyze()
        grouped = {}
        
        stride_names = {
            "S": "Spoofing（身份伪造）",
            "T": "Tampering（数据篡改）",
            "R": "Repudiation（抵赖）",
            "I": "Information Disclosure（信息泄露）",
            "D": "Denial of Service（拒绝服务）",
            "E": "Elevation of Privilege（权限提升）",
        }
        
        for threat in threats:
            s = threat["stride"]
            if s not in grouped:
                grouped[s] = {
                    "name": stride_names.get(s, s),
                    "threats": [],
                }
            grouped[s]["threats"].append(threat)
        
        return grouped
    
    def risk_matrix(self) -> Dict:
        """生成风险矩阵"""
        threats = self.analyze()
        
        matrix = {
            "critical": [],
            "high": [],
            "medium": [],
            "low": [],
        }
        
        for threat in threats:
            level = threat["risk_level"]
            if level in matrix:
                matrix[level].append({
                    "id": threat["id"],
                    "name": threat["name"],
                    "stride": threat["stride"],
                    "score": threat["risk_score"],
                })
        
        return {
            "total_threats": len(threats),
            "by_level": {
                "critical": len(matrix["critical"]),
                "high": len(matrix["high"]),
                "medium": len(matrix["medium"]),
                "low": len(matrix["low"]),
            },
            "matrix": matrix,
        }
    
    def report_markdown(self) -> str:
        """生成Markdown报告"""
        threats = self.analyze()
        matrix = self.risk_matrix()
        
        lines = [
            "# 🛡️ 威胁建模报告",
            f"",
            f"## 概览",
            f"",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 总威胁数 | {matrix['total_threats']} |",
            f"| 严重 | {matrix['by_level']['critical']} |",
            f"| 高 | {matrix['by_level']['high']} |",
            f"| 中 | {matrix['by_level']['medium']} |",
            f"| 低 | {matrix['by_level']['low']} |",
            f"",
        ]
        
        lines.append("## 系统架构\n")
        lines.append("### 组件\n")
        for name, comp in self._components.items():
            lines.append(f"- **{name}**: {comp['description']} (信任区域: {comp['trust_zone']})")
        
        lines.append("\n### 数据流\n")
        for flow in self._flows:
            lines.append(f"- {flow['flow']}: {flow['description']}")
        
        if self._trust_boundaries:
            lines.append("\n### 信任边界\n")
            for tb in self._trust_boundaries:
                lines.append(f"- {tb['source']} ({tb['source_zone']}) → {tb['destination']} ({tb['destination_zone']})")
        
        lines.append("\n## 威胁详情\n")
        
        current_stride = None
        for threat in threats:
            if threat["stride"] != current_stride:
                current_stride = threat["stride"]
                lines.append(f"### {threat['stride_name']}")
                lines.append("")
            
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                threat["risk_level"], "⚪"
            )
            lines.append(f"{icon} **{threat['id']}: {threat['name']}** (风险评分: {threat['risk_score']})")
            lines.append(f"  - {threat['description']}")
            lines.append(f"  - 影响组件: {', '.join(threat['components'])}")
            lines.append(f"  - 缓解措施:")
            for m in threat["mitigations"]:
                lines.append(f"    - {m}")
            lines.append("")
        
        return "\n".join(lines)
    
    def report_json(self) -> str:
        """生成JSON报告"""
        return json.dumps({
            "threats": self.analyze(),
            "risk_matrix": self.risk_matrix(),
            "architecture": {
                "components": self._components,
                "flows": self._flows,
                "trust_boundaries": self._trust_boundaries,
            },
            "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }, ensure_ascii=False, indent=2)
    
    def _conditions_met(self, conditions: List[str]) -> bool:
        """检查威胁条件是否满足"""
        if not conditions:
            return True
        return any(c in self._components for c in conditions)
    
    def _build_threat(self, stride_type: str, template: Dict) -> Dict:
        """构建威胁对象"""
        stride_names = {
            "S": "Spoofing（身份伪造）",
            "T": "Tampering（数据篡改）",
            "R": "Repudiation（抵赖）",
            "I": "Information Disclosure（信息泄露）",
            "D": "Denial of Service（拒绝服务）",
            "E": "Elevation of Privilege（权限提升）",
        }
        
        # 基于组件信任区域评估风险
        components = template.get("conditions", [])
        has_internet = any(
            self._components.get(c, {}).get("trust_zone") == "internet"
            for c in components
        )
        
        likelihood = "high" if has_internet else "medium"
        impact = "high" if has_internet else "medium"
        risk_score = self._calculate_risk(likelihood, impact)
        
        return {
            "id": template["id"],
            "stride": stride_type,
            "stride_name": stride_names.get(stride_type, stride_type),
            "name": template["name"],
            "description": template["description"],
            "components": components,
            "likelihood": likelihood,
            "impact": impact,
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "mitigations": template["mitigations"],
        }
    
    def _build_custom_threat(self, custom: Dict) -> Dict:
        """构建自定义威胁对象"""
        risk_score = self._calculate_risk(
            custom.get("likelihood", "medium"),
            custom.get("impact", "medium"),
        )
        
        stride_names = {
            "S": "Spoofing（身份伪造）",
            "T": "Tampering（数据篡改）",
            "R": "Repudiation（抵赖）",
            "I": "Information Disclosure（信息泄露）",
            "D": "Denial of Service（拒绝服务）",
            "E": "Elevation of Privilege（权限提升）",
        }
        
        return {
            "id": f"CUST-{len(self._custom_threats):03d}",
            "stride": custom["stride"],
            "stride_name": stride_names.get(custom["stride"], custom["stride"]),
            "name": custom["name"],
            "description": custom["description"],
            "components": custom.get("components", []),
            "likelihood": custom.get("likelihood", "medium"),
            "impact": custom.get("impact", "medium"),
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "mitigations": custom.get("mitigations", []),
            "custom": True,
        }
    
    def _calculate_risk(self, likelihood: str, impact: str) -> int:
        """计算风险评分"""
        level_scores = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        l = level_scores.get(likelihood, 2)
        i = level_scores.get(impact, 2)
        return min(100, l * i * 100 // 16)
    
    def _risk_level(self, score: int) -> str:
        """风险等级"""
        if score >= 75:
            return "critical"
        elif score >= 50:
            return "high"
        elif score >= 25:
            return "medium"
        return "low"
