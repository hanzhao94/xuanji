"""
xuanji 自动文档生成

从Agent对象或配置自动提取信息，生成Markdown/HTML文档。
零外部依赖，仅使用标准库。
"""

import inspect
import json
import os
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── HTML模板 ───────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 900px; margin: 0 auto; padding: 40px 20px;
       background: #fafafa; color: #333; line-height: 1.8; }}
h1 {{ color: #1a1a2e; border-bottom: 3px solid #16213e; padding-bottom: 12px; }}
h2 {{ color: #16213e; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 8px; }}
h3 {{ color: #0f3460; margin-top: 24px; }}
code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px;
        font-family: 'Cascadia Code', monospace; font-size: 0.9em; }}
pre {{ background: #1a1a2e; color: #e0e0e0; padding: 16px; border-radius: 8px;
       overflow-x: auto; font-size: 0.85em; line-height: 1.5; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #ddd; padding: 10px 14px; text-align: left; }}
th {{ background: #f5f5f5; font-weight: 600; }}
tr:nth-child(even) {{ background: #fafafa; }}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px;
          font-size: 12px; font-weight: 600; margin-right: 4px; }}
.badge-tool {{ background: #e3f2fd; color: #1565c0; }}
.badge-skill {{ background: #f3e5f5; color: #7b1fa2; }}
.badge-channel {{ background: #e8f5e9; color: #2e7d32; }}
.meta {{ color: #888; font-size: 0.85em; }}
.toc {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
        padding: 16px 24px; margin: 20px 0; }}
.toc a {{ color: #0f3460; text-decoration: none; }}
.toc a:hover {{ text-decoration: underline; }}
.toc ul {{ list-style: none; padding-left: 20px; }}
.toc > ul {{ padding-left: 0; }}
</style>
</head>
<body>
{content}
<hr>
<p class="meta">由 xuanji AutoDoc 自动生成 | {timestamp}</p>
</body>
</html>"""


# ─── 信息提取器 ─────────────────────────────────────────────

class InfoExtractor:
    """从各种来源提取Agent信息"""

    @staticmethod
    def from_object(agent: Any) -> Dict:
        """从Agent对象提取信息"""
        info: Dict[str, Any] = {
            "name": getattr(agent, "name", agent.__class__.__name__),
            "description": getattr(agent, "description", ""),
            "version": getattr(agent, "version", "0.1.0"),
            "class_name": agent.__class__.__name__,
            "module": agent.__class__.__module__,
            "docstring": inspect.getdoc(agent) or "",
        }

        # 提取工具列表
        tools = getattr(agent, "tools", None)
        if tools:
            if isinstance(tools, (list, tuple)):
                info["tools"] = list(tools)
            elif isinstance(tools, dict):
                info["tools"] = list(tools.keys())
            else:
                info["tools"] = []
        else:
            info["tools"] = []

        # 提取技能列表
        skills = getattr(agent, "skills", None)
        if skills:
            if isinstance(skills, (list, tuple)):
                info["skills"] = list(skills)
            elif isinstance(skills, dict):
                info["skills"] = list(skills.keys())
            else:
                info["skills"] = []
        else:
            info["skills"] = []

        # 提取资源/配置
        resources = getattr(agent, "resources", {})
        info["resources"] = dict(resources) if resources else {}

        # 提取公开方法
        methods = []
        for name, method in inspect.getmembers(agent, predicate=inspect.ismethod):
            if name.startswith("_"):
                continue
            doc = inspect.getdoc(method) or ""
            sig = ""
            try:
                sig = str(inspect.signature(method))
            except (ValueError, TypeError):
                pass
            methods.append({"name": name, "signature": sig, "docstring": doc})
        info["methods"] = methods

        # 提取事件处理器
        handlers = [m["name"] for m in methods if m["name"].startswith("on_")]
        info["handlers"] = handlers

        return info

    @staticmethod
    def from_config(config: Dict) -> Dict:
        """从配置字典提取信息"""
        return {
            "name": config.get("name", "unnamed"),
            "description": config.get("description", ""),
            "version": config.get("version", "0.1.0"),
            "tools": config.get("tools", []),
            "skills": config.get("skills", []),
            "resources": config.get("resources", {}),
            "model": config.get("model", ""),
            "system_prompt": config.get("system_prompt", ""),
            "channels": config.get("channels", []),
            "methods": [],
            "handlers": [],
        }

    @staticmethod
    def from_directory(agent_dir: str) -> Dict:
        """从Agent目录提取信息"""
        info: Dict[str, Any] = {
            "name": os.path.basename(agent_dir),
            "description": "",
            "version": "0.1.0",
            "tools": [],
            "skills": [],
            "resources": {},
            "methods": [],
            "handlers": [],
        }

        # 读取config.json
        config_path = os.path.join(agent_dir, "config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                info.update(InfoExtractor.from_config(config))
            except Exception as e:
                logger.warning(f"读取config.json失败: {e}")

        # 扫描skills目录
        skills_dir = os.path.join(agent_dir, "skills")
        if os.path.isdir(skills_dir):
            for item in os.listdir(skills_dir):
                item_path = os.path.join(skills_dir, item)
                if os.path.isdir(item_path) or item.endswith(".py"):
                    info["skills"].append(item.replace(".py", ""))

        # 读取profile.json
        profile_path = os.path.join(agent_dir, "profile.json")
        if os.path.isfile(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
                info["profile"] = profile
            except Exception:
                pass

        return info


# ─── Markdown生成器 ─────────────────────────────────────────

class MarkdownGenerator:
    """Markdown文档生成器"""

    def __init__(self):
        self._lines: List[str] = []

    def heading(self, text: str, level: int = 1) -> "MarkdownGenerator":
        self._lines.append(f"{'#' * level} {text}\n")
        return self

    def paragraph(self, text: str) -> "MarkdownGenerator":
        self._lines.append(f"{text}\n")
        return self

    def code_block(self, code: str, lang: str = "") -> "MarkdownGenerator":
        self._lines.append(f"```{lang}\n{code}\n```\n")
        return self

    def table(self, headers: List[str], rows: List[List[str]]) -> "MarkdownGenerator":
        if not headers:
            return self
        self._lines.append("| " + " | ".join(headers) + " |")
        self._lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            # 补齐列数
            padded = row + [""] * (len(headers) - len(row))
            self._lines.append("| " + " | ".join(padded[:len(headers)]) + " |")
        self._lines.append("")
        return self

    def bullet_list(self, items: List[str]) -> "MarkdownGenerator":
        for item in items:
            self._lines.append(f"- {item}")
        self._lines.append("")
        return self

    def hr(self) -> "MarkdownGenerator":
        self._lines.append("---\n")
        return self

    def raw(self, text: str) -> "MarkdownGenerator":
        self._lines.append(text)
        return self

    def build(self) -> str:
        return "\n".join(self._lines)


# ─── AutoDoc 主类 ──────────────────────────────────────────

class AutoDoc:
    """自动文档生成器
    
    用法::
    
        doc = AutoDoc()
        
        # 从Agent对象生成
        markdown = doc.generate(my_agent)
        
        # 从配置字典生成
        markdown = doc.generate_from_config(config_dict)
        
        # 从目录生成
        markdown = doc.generate_from_directory("/path/to/agent")
        
        # 导出HTML
        html = doc.to_html(markdown, title="My Agent")
        
        # 保存到文件
        doc.save(markdown, "/path/to/output.md")
        doc.save_html(markdown, "/path/to/output.html")
    """

    def generate(self, agent: Any) -> str:
        """从Agent对象生成Markdown文档
        
        Args:
            agent: Agent对象（需要有 name/tools/skills 等属性）
        
        Returns:
            Markdown文档字符串
        """
        info = InfoExtractor.from_object(agent)
        return self._build_markdown(info)

    def generate_from_config(self, config: Dict) -> str:
        """从配置字典生成文档"""
        info = InfoExtractor.from_config(config)
        return self._build_markdown(info)

    def generate_from_directory(self, agent_dir: str) -> str:
        """从Agent目录生成文档"""
        info = InfoExtractor.from_directory(agent_dir)
        return self._build_markdown(info)

    def _build_markdown(self, info: Dict) -> str:
        """构建Markdown文档"""
        md = MarkdownGenerator()

        # 标题
        name = info.get("name", "Agent")
        md.heading(f"📋 {name} 文档")

        # 基本信息
        md.heading("基本信息", 2)
        meta_rows = [
            ["名称", info.get("name", "-")],
            ["版本", info.get("version", "-")],
        ]
        if info.get("class_name"):
            meta_rows.append(["类名", f"`{info['class_name']}`"])
        if info.get("module"):
            meta_rows.append(["模块", f"`{info['module']}`"])
        if info.get("model"):
            meta_rows.append(["模型", info["model"]])
        md.table(["属性", "值"], meta_rows)

        # 描述
        desc = info.get("description") or info.get("docstring")
        if desc:
            md.heading("描述", 2)
            md.paragraph(desc)

        # 系统提示词
        if info.get("system_prompt"):
            md.heading("系统提示词", 2)
            prompt = info["system_prompt"]
            if len(prompt) > 500:
                prompt = prompt[:500] + "..."
            md.code_block(prompt)

        # 工具列表
        tools = info.get("tools", [])
        if tools:
            md.heading("工具列表", 2)
            md.paragraph(f"共 {len(tools)} 个工具：")
            tool_rows = []
            for t in tools:
                if isinstance(t, dict):
                    tool_rows.append([t.get("name", str(t)), t.get("description", "")])
                else:
                    tool_rows.append([str(t), ""])
            md.table(["工具名", "描述"], tool_rows)

        # 技能列表
        skills = info.get("skills", [])
        if skills:
            md.heading("技能列表", 2)
            md.paragraph(f"共 {len(skills)} 个技能：")
            md.bullet_list([str(s) for s in skills])

        # 通信渠道
        channels = info.get("channels", [])
        if channels:
            md.heading("通信渠道", 2)
            md.bullet_list([str(c) for c in channels])

        # API接口（公开方法）
        methods = info.get("methods", [])
        if methods:
            md.heading("API接口", 2)
            for m in methods:
                name = m["name"]
                sig = m.get("signature", "")
                doc = m.get("docstring", "")
                md.heading(f"`{name}{sig}`", 3)
                if doc:
                    md.paragraph(doc)

        # 事件处理器
        handlers = info.get("handlers", [])
        if handlers:
            md.heading("事件处理器", 2)
            md.bullet_list([f"`{h}`" for h in handlers])

        # 资源/配置
        resources = info.get("resources", {})
        if resources:
            md.heading("资源配置", 2)
            md.code_block(json.dumps(resources, ensure_ascii=False, indent=2), "json")

        # Profile
        profile = info.get("profile")
        if profile:
            md.heading("Agent Profile", 2)
            md.code_block(json.dumps(profile, ensure_ascii=False, indent=2), "json")

        # 页脚
        md.hr()
        md.paragraph(f"*由 xuanji AutoDoc 自动生成 | {time.strftime('%Y-%m-%d %H:%M:%S')}*")

        return md.build()

    def to_html(self, markdown: str, title: str = "Agent文档") -> str:
        """将Markdown转换为HTML
        
        简单转换，不依赖外部Markdown解析器。
        支持标题、代码块、表格、列表等基本格式。
        
        Args:
            markdown: Markdown文本
            title: HTML页面标题
        
        Returns:
            HTML字符串
        """
        lines = markdown.split("\n")
        html_lines = []
        in_code_block = False
        in_table = False
        table_rows = []

        for line in lines:
            # 代码块
            if line.startswith("```"):
                if in_code_block:
                    html_lines.append("</pre>")
                    in_code_block = False
                else:
                    lang = line[3:].strip()
                    html_lines.append(f"<pre>")
                    in_code_block = True
                continue

            if in_code_block:
                # HTML转义
                escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_lines.append(escaped)
                continue

            # 表格
            if line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if all(c.replace("-", "") == "" for c in cells):
                    continue  # 分隔行
                if not in_table:
                    html_lines.append("<table>")
                    html_lines.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
                    in_table = True
                else:
                    html_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                continue
            elif in_table:
                html_lines.append("</table>")
                in_table = False

            # 标题
            if line.startswith("# "):
                html_lines.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                html_lines.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("- "):
                html_lines.append(f"<li>{line[2:]}</li>")
            elif line.startswith("---"):
                html_lines.append("<hr>")
            elif line.startswith("*") and line.endswith("*"):
                html_lines.append(f"<p><em>{line.strip('*')}</em></p>")
            elif line.strip():
                html_lines.append(f"<p>{line}</p>")

        if in_table:
            html_lines.append("</table>")

        content = "\n".join(html_lines)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        return HTML_TEMPLATE.format(title=title, content=content, timestamp=timestamp)

    def save(self, content: str, path: str) -> str:
        """保存文档到文件
        
        Args:
            content: 文档内容
            path: 输出路径
        
        Returns:
            保存的文件路径
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"文档已保存: {path}")
        return path

    def save_html(self, markdown: str, path: str, title: str = "Agent文档") -> str:
        """保存HTML文档
        
        Args:
            markdown: Markdown内容
            path: 输出路径
            title: 页面标题
        
        Returns:
            保存的文件路径
        """
        html = self.to_html(markdown, title)
        return self.save(html, path)
