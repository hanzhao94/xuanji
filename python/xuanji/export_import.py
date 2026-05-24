"""
xuanji Agent导出/导入

支持将Agent打包为ZIP文件（配置+记忆+Skill+Profile），
以及从ZIP文件导入Agent。
零外部依赖，仅使用标准库。
"""

import json
import os
import shutil
import tempfile
import time
import zipfile
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ─── 常量 ───────────────────────────────────────────────────

MANIFEST_FILE = "manifest.json"
CONFIG_FILE = "config.json"
MEMORIES_FILE = "memories.json"
PROFILE_FILE = "profile.json"
SKILLS_DIR = "skills"

CURRENT_FORMAT_VERSION = "1.0.0"

EXPORT_COMPONENTS = {"config", "memories", "skills", "profile"}


# ─── 辅助函数 ───────────────────────────────────────────────

def _safe_json_load(path: str) -> Any:
    """安全加载JSON文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"加载JSON失败 {path}: {e}")
        return None


def _safe_json_dump(data: Any, path: str) -> None:
    """安全写入JSON文件"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _check_version_compat(pkg_version: str, current_version: str) -> bool:
    """检查版本兼容性（主版本号必须一致）"""
    try:
        pkg_major = int(pkg_version.split(".")[0])
        cur_major = int(current_version.split(".")[0])
        return pkg_major == cur_major
    except (ValueError, IndexError):
        return False


# ─── 导出结果 ───────────────────────────────────────────────

class ExportResult:
    """导出结果"""

    def __init__(self, success: bool, path: str = "", message: str = "",
                 components: Optional[List[str]] = None, size_bytes: int = 0):
        self.success = success
        self.path = path
        self.message = message
        self.components = components or []
        self.size_bytes = size_bytes

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "path": self.path,
            "message": self.message,
            "components": self.components,
            "size_bytes": self.size_bytes,
        }


class ImportResult:
    """导入结果"""

    def __init__(self, success: bool, agent_name: str = "", message: str = "",
                 components: Optional[List[str]] = None, warnings: Optional[List[str]] = None):
        self.success = success
        self.agent_name = agent_name
        self.message = message
        self.components = components or []
        self.warnings = warnings or []

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "agent_name": self.agent_name,
            "message": self.message,
            "components": self.components,
            "warnings": self.warnings,
        }


# ─── Agent数据收集器 ────────────────────────────────────────

class AgentDataCollector:
    """从指定目录收集Agent数据"""

    def __init__(self, agent_dir: str):
        self.agent_dir = agent_dir

    def collect_config(self) -> Optional[Dict]:
        """收集Agent配置"""
        config_path = os.path.join(self.agent_dir, CONFIG_FILE)
        return _safe_json_load(config_path)

    def collect_memories(self) -> Optional[List[Dict]]:
        """收集Agent记忆"""
        memories_path = os.path.join(self.agent_dir, MEMORIES_FILE)
        data = _safe_json_load(memories_path)
        if isinstance(data, list):
            return data
        # 尝试目录方式
        mem_dir = os.path.join(self.agent_dir, "memories")
        if os.path.isdir(mem_dir):
            memories = []
            for fname in os.listdir(mem_dir):
                if fname.endswith(".json"):
                    item = _safe_json_load(os.path.join(mem_dir, fname))
                    if item:
                        memories.append(item)
            return memories if memories else None
        return None

    def collect_profile(self) -> Optional[Dict]:
        """收集Agent Profile"""
        profile_path = os.path.join(self.agent_dir, PROFILE_FILE)
        return _safe_json_load(profile_path)

    def collect_skills(self) -> Optional[Dict[str, str]]:
        """收集Agent Skills（文件名→内容映射）"""
        skills_dir = os.path.join(self.agent_dir, SKILLS_DIR)
        if not os.path.isdir(skills_dir):
            return None
        skills = {}
        for root, _, files in os.walk(skills_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, skills_dir)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        skills[rel] = f.read()
                except Exception as e:
                    logger.warning(f"读取Skill文件失败 {fpath}: {e}")
        return skills if skills else None


# ─── AgentExporter ──────────────────────────────────────────

class AgentExporter:
    """Agent导出/导入管理器
    
    用法::
    
        exporter = AgentExporter(agents_dir="/path/to/agents")
        
        # 导出
        result = exporter.export_agent("my_agent", "/tmp/my_agent.zip")
        
        # 选择性导出
        result = exporter.export_agent("my_agent", "/tmp/backup.zip",
                                        components={"config", "memories"})
        
        # 导入
        result = exporter.import_agent("/tmp/my_agent.zip")
        
        # 列出包内容
        info = exporter.inspect_package("/tmp/my_agent.zip")
    """

    def __init__(self, agents_dir: str = "./agents"):
        """
        Args:
            agents_dir: Agent数据根目录，每个Agent一个子目录
        """
        self.agents_dir = agents_dir

    def _agent_dir(self, agent_name: str) -> str:
        return os.path.join(self.agents_dir, agent_name)

    def export_agent(self, agent_name: str, output_path: str,
                     components: Optional[Set[str]] = None) -> ExportResult:
        """导出Agent为ZIP包
        
        Args:
            agent_name: Agent名称
            output_path: 输出ZIP文件路径
            components: 要导出的组件集合，默认全部
                       可选: {"config", "memories", "skills", "profile"}
        
        Returns:
            ExportResult
        """
        if components is None:
            components = EXPORT_COMPONENTS
        else:
            invalid = components - EXPORT_COMPONENTS
            if invalid:
                return ExportResult(False, message=f"未知组件: {invalid}")

        agent_dir = self._agent_dir(agent_name)
        if not os.path.isdir(agent_dir):
            return ExportResult(False, message=f"Agent目录不存在: {agent_dir}")

        collector = AgentDataCollector(agent_dir)
        exported = []

        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # 写入manifest
                manifest = {
                    "format_version": CURRENT_FORMAT_VERSION,
                    "agent_name": agent_name,
                    "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "components": list(components),
                }

                # 导出config
                if "config" in components:
                    config = collector.collect_config()
                    if config:
                        zf.writestr(CONFIG_FILE, json.dumps(config, ensure_ascii=False, indent=2))
                        exported.append("config")

                # 导出memories
                if "memories" in components:
                    memories = collector.collect_memories()
                    if memories:
                        zf.writestr(MEMORIES_FILE, json.dumps(memories, ensure_ascii=False, indent=2))
                        exported.append("memories")

                # 导出profile
                if "profile" in components:
                    profile = collector.collect_profile()
                    if profile:
                        zf.writestr(PROFILE_FILE, json.dumps(profile, ensure_ascii=False, indent=2))
                        exported.append("profile")

                # 导出skills
                if "skills" in components:
                    skills = collector.collect_skills()
                    if skills:
                        for rel_path, content in skills.items():
                            zf.writestr(f"{SKILLS_DIR}/{rel_path}", content)
                        exported.append("skills")

                manifest["exported_components"] = exported
                zf.writestr(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))

            size = os.path.getsize(output_path)
            logger.info(f"Agent '{agent_name}' 导出成功: {output_path} ({size} bytes)")
            return ExportResult(
                success=True,
                path=output_path,
                message=f"导出成功，包含: {', '.join(exported)}",
                components=exported,
                size_bytes=size,
            )

        except Exception as e:
            logger.error(f"导出Agent失败: {e}")
            return ExportResult(False, message=f"导出失败: {e}")

    def import_agent(self, package_path: str, target_name: Optional[str] = None,
                     overwrite: bool = False) -> ImportResult:
        """从ZIP包导入Agent
        
        Args:
            package_path: ZIP文件路径
            target_name: 目标Agent名称，默认使用包内名称
            overwrite: 是否覆盖已存在的Agent
        
        Returns:
            ImportResult
        """
        if not os.path.isfile(package_path):
            return ImportResult(False, message=f"包文件不存在: {package_path}")

        warnings = []

        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                # 读取manifest
                if MANIFEST_FILE not in zf.namelist():
                    return ImportResult(False, message="无效的Agent包：缺少manifest.json")

                manifest = json.loads(zf.read(MANIFEST_FILE).decode("utf-8"))
                pkg_version = manifest.get("format_version", "0.0.0")

                if not _check_version_compat(pkg_version, CURRENT_FORMAT_VERSION):
                    return ImportResult(
                        False,
                        message=f"版本不兼容: 包版本 {pkg_version}，当前 {CURRENT_FORMAT_VERSION}"
                    )

                agent_name = target_name or manifest.get("agent_name", "imported_agent")
                agent_dir = self._agent_dir(agent_name)

                if os.path.isdir(agent_dir) and not overwrite:
                    return ImportResult(
                        False, agent_name=agent_name,
                        message=f"Agent '{agent_name}' 已存在，使用 overwrite=True 覆盖"
                    )

                os.makedirs(agent_dir, exist_ok=True)
                imported = []

                # 导入config
                if CONFIG_FILE in zf.namelist():
                    config_data = zf.read(CONFIG_FILE).decode("utf-8")
                    with open(os.path.join(agent_dir, CONFIG_FILE), "w", encoding="utf-8") as f:
                        f.write(config_data)
                    imported.append("config")

                # 导入memories
                if MEMORIES_FILE in zf.namelist():
                    mem_data = zf.read(MEMORIES_FILE).decode("utf-8")
                    with open(os.path.join(agent_dir, MEMORIES_FILE), "w", encoding="utf-8") as f:
                        f.write(mem_data)
                    imported.append("memories")

                # 导入profile
                if PROFILE_FILE in zf.namelist():
                    prof_data = zf.read(PROFILE_FILE).decode("utf-8")
                    with open(os.path.join(agent_dir, PROFILE_FILE), "w", encoding="utf-8") as f:
                        f.write(prof_data)
                    imported.append("profile")

                # 导入skills
                skill_files = [n for n in zf.namelist() if n.startswith(f"{SKILLS_DIR}/")]
                if skill_files:
                    for sf in skill_files:
                        target_path = os.path.join(agent_dir, sf)
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with open(target_path, "wb") as f:
                            f.write(zf.read(sf))
                    imported.append("skills")

                logger.info(f"Agent '{agent_name}' 导入成功，组件: {imported}")
                return ImportResult(
                    success=True,
                    agent_name=agent_name,
                    message=f"导入成功，包含: {', '.join(imported)}",
                    components=imported,
                    warnings=warnings,
                )

        except zipfile.BadZipFile:
            return ImportResult(False, message="无效的ZIP文件")
        except Exception as e:
            logger.error(f"导入Agent失败: {e}")
            return ImportResult(False, message=f"导入失败: {e}")

    def inspect_package(self, package_path: str) -> Dict:
        """查看ZIP包内容
        
        Args:
            package_path: ZIP文件路径
        
        Returns:
            包信息字典
        """
        if not os.path.isfile(package_path):
            return {"error": f"文件不存在: {package_path}"}

        try:
            with zipfile.ZipFile(package_path, "r") as zf:
                files = []
                total_size = 0
                for info in zf.infolist():
                    files.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                    })
                    total_size += info.file_size

                manifest = {}
                if MANIFEST_FILE in zf.namelist():
                    manifest = json.loads(zf.read(MANIFEST_FILE).decode("utf-8"))

                return {
                    "manifest": manifest,
                    "files": files,
                    "file_count": len(files),
                    "total_size": total_size,
                    "compressed_size": os.path.getsize(package_path),
                }
        except Exception as e:
            return {"error": str(e)}

    def list_exportable(self) -> List[Dict]:
        """列出所有可导出的Agent
        
        Returns:
            Agent信息列表
        """
        if not os.path.isdir(self.agents_dir):
            return []

        agents = []
        for name in os.listdir(self.agents_dir):
            agent_dir = os.path.join(self.agents_dir, name)
            if not os.path.isdir(agent_dir):
                continue

            info = {"name": name, "components": []}
            if os.path.isfile(os.path.join(agent_dir, CONFIG_FILE)):
                info["components"].append("config")
            if (os.path.isfile(os.path.join(agent_dir, MEMORIES_FILE))
                    or os.path.isdir(os.path.join(agent_dir, "memories"))):
                info["components"].append("memories")
            if os.path.isfile(os.path.join(agent_dir, PROFILE_FILE)):
                info["components"].append("profile")
            if os.path.isdir(os.path.join(agent_dir, SKILLS_DIR)):
                info["components"].append("skills")

            agents.append(info)

        return agents
