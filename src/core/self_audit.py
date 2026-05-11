"""自我审计模块：定时扫描 skills、projects、learned_modules，检查过时、错误、冗余。

审计触发：
    1. 定时：通过 TaskScheduler（APScheduler）每天凌晨 4 点调度
    2. 手动：用户输入 /self-audit 命令

审计维度：
    - Skills：frontmatter 缺失、内容过短、孤立文件（触发逻辑已改为 LLM，不再检查 triggers）
    - Projects：路径失效、信息过时、格式异常
    - Learned Modules：语法错误、危险 import、schema 不完整

审计报告通过飞书发送给 owner_chat_id（已配置时）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.core.config import LAMIX_DIR, SKILLS_DIR, PROJECTS_DIR, load_config

logger = logging.getLogger(__name__)

LEARNED_MODULES_DIR = LAMIX_DIR / "learned_modules"
AUDIT_LOG_DIR = LAMIX_DIR / "logs"
AUDIT_LOG_PATH = AUDIT_LOG_DIR / "self_audit.log"


def _audit_log(msg: str) -> None:
    """写入审计专用日志文件（同时输出到 stdout）。"""
    logger.info(msg)
    try:
        AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

# 默认审计时间（本地时间）
DEFAULT_AUDIT_HOUR = 4  # 凌晨 4 点
DEFAULT_AUDIT_MINUTE = 0



# ── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class AuditFinding:
    """一条审计发现。"""
    severity: str          # info / warning / error
    category: str           # skill / project / module
    target: str            # 文件/目录名
    message: str           # 发现描述
    suggestion: str = ""   # 修复建议
    fixed: bool = False    # 是否已自动修复
    fix_detail: str = ""   # 修复了什么


@dataclass
class AuditReport:
    """一份完整的审计报告。"""
    timestamp: str
    duration_seconds: float
    skills_scanned: int = 0
    projects_scanned: int = 0
    modules_scanned: int = 0
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def findings_by_severity(self) -> dict[str, list[AuditFinding]]:
        result: dict[str, list[AuditFinding]] = {"error": [], "warning": [], "info": []}
        for f in self.findings:
            result[f.severity].append(f)
        return result

    def summary_text(self) -> str:
        total = len(self.findings)
        errors = len(self.findings_by_severity["error"])
        warnings = len(self.findings_by_severity["warning"])
        lines = [
            f"审计时间：{self.timestamp}",
            f"扫描范围：{self.skills_scanned} skills / {self.projects_scanned} projects / {self.modules_scanned} modules",
            f"发现问题：{total} 条（error={errors}, warning={warnings}, info={total - errors - warnings}）",
        ]
        if total == 0:
            lines.append("✅ 没有发现问题，知识库状态良好。")
        return "\n".join(lines)


# ── 扫描器 ───────────────────────────────────────────────────────────────────

def scan_skills(auto_fix: bool = False) -> list[AuditFinding]:
    """扫描所有 skills，返回审计发现列表。

    注意：skill 的触发逻辑已改为 LLM 判断，不再检查 triggers 字段。
    知识性 skill（如 machines、user-data-location）没有编号步骤也正常，
    不再将"缺少编号步骤"作为警告。

    auto_fix=True 时，对可安全修复的问题执行自动修复：
    - 空目录（无 SKILL.md 且无其他文件）→ 删除
    - 额外 .md 文件 → 合并到 SKILL.md 末尾后删除
    - 缺少 frontmatter → 自动生成
    """
    findings: list[AuditFinding] = []

    if not SKILLS_DIR.exists():
        return findings

    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            # 检查目录是否为空（没有任何其他文件）
            other_files = list(skill_dir.iterdir())
            if not other_files and auto_fix:
                # 空目录，直接删除
                import shutil
                shutil.rmtree(skill_dir)
                findings.append(AuditFinding(
                    severity="warning",
                    category="skill",
                    target=skill_dir.name,
                    message="存在目录但没有 SKILL.md 文件",
                    suggestion="删除目录，或创建 SKILL.md",
                    fixed=True,
                    fix_detail="已删除空目录",
                ))
            else:
                findings.append(AuditFinding(
                    severity="warning",
                    category="skill",
                    target=skill_dir.name,
                    message="存在目录但没有 SKILL.md 文件",
                    suggestion="删除目录，或创建 SKILL.md",
                ))
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        # 检查 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            if auto_fix:
                # 自动生成 frontmatter：name 从目录名取，description 从正文第一行取
                body_lines = content.strip().splitlines()
                first_line = ""
                for line in body_lines:
                    stripped = line.strip()
                    # 跳过空行和纯标题标记
                    if stripped and not stripped.startswith("#"):
                        first_line = stripped
                        break
                    elif stripped.startswith("#"):
                        # 取标题内容作为 description
                        first_line = stripped.lstrip("# ").strip()
                        break
                if not first_line:
                    first_line = skill_dir.name
                fm_block = f"---\nname: {skill_dir.name}\ndescription: {first_line}\n---\n"
                new_content = fm_block + content
                skill_md.write_text(new_content, encoding="utf-8")
                # 重新读取以继续后续检查
                content = new_content
                fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
                findings.append(AuditFinding(
                    severity="warning",
                    category="skill",
                    target=skill_dir.name,
                    message="SKILL.md 缺少 frontmatter（--- ... ---）",
                    suggestion="添加 YAML frontmatter（name、description）",
                    fixed=True,
                    fix_detail=f"已自动生成 frontmatter（name={skill_dir.name}）",
                ))
            else:
                findings.append(AuditFinding(
                    severity="warning",
                    category="skill",
                    target=skill_dir.name,
                    message="SKILL.md 缺少 frontmatter（--- ... ---）",
                    suggestion="添加 YAML frontmatter（name、description）",
                ))
        else:
            # 解析 frontmatter
            try:
                import yaml
                meta = yaml.safe_load(fm_match.group(1)) or {}
                if not meta.get("name"):
                    findings.append(AuditFinding(
                        severity="info",
                        category="skill",
                        target=skill_dir.name,
                        message="frontmatter 缺少 name 字段",
                    ))
                if not meta.get("description"):
                    findings.append(AuditFinding(
                        severity="info",
                        category="skill",
                        target=skill_dir.name,
                        message="frontmatter 缺少 description 字段",
                    ))
                # 注意：不再检查 triggers 字段，触发逻辑已改为 LLM
            except yaml.YAMLError as e:
                findings.append(AuditFinding(
                    severity="error",
                    category="skill",
                    target=skill_dir.name,
                    message=f"frontmatter YAML 解析失败: {e}",
                    suggestion="修复 frontmatter 格式",
                ))

        # 检查正文结构
        body = content[fm_match.end():] if fm_match else content

        # 注意：不再检查是否有编号步骤，skill 可能是知识性内容（如 machines），
        # 没有固定步骤也完全正常。

        # 检查内容长度
        if len(body.strip()) < 50:
            findings.append(AuditFinding(
                severity="warning",
                category="skill",
                target=skill_dir.name,
                message="正文内容过短（<50 字符），可能是不完整的 skill",
                suggestion="补充完整的步骤描述和注意事项",
            ))

        # 检查是否只是模板未填充
        if "步骤一" in body and "步骤二" in body and "步骤三" in body:
            findings.append(AuditFinding(
                severity="warning",
                category="skill",
                target=skill_dir.name,
                message="正文仍为模板占位内容（步骤一/步骤二/步骤三），未填写实际内容",
                suggestion="替换为具体的操作步骤",
            ))

        # 检查孤立文件（目录下有非 SKILL.md 的 .md 文件）
        extra_md_files = [f for f in skill_dir.iterdir() if f.suffix == ".md" and f != skill_md]
        for f in extra_md_files:
            if auto_fix:
                # 把额外 .md 文件内容合并到 SKILL.md 末尾，然后删除
                try:
                    extra_content = f.read_text(encoding="utf-8")
                    current = skill_md.read_text(encoding="utf-8")
                    merged = current.rstrip() + f"\n\n<!-- merged from {f.name} -->\n" + extra_content
                    skill_md.write_text(merged, encoding="utf-8")
                    f.unlink()
                    findings.append(AuditFinding(
                        severity="info",
                        category="skill",
                        target=f"{skill_dir.name}/{f.name}",
                        message=f"目录中有额外的 .md 文件: {f.name}",
                        suggestion="合并到 SKILL.md 或删除",
                        fixed=True,
                        fix_detail=f"已将 {f.name} 内容合并到 SKILL.md 末尾并删除原文件",
                    ))
                except OSError as e:
                    logger.warning(f"auto_fix: 合并 {f} 失败: {e}")
                    findings.append(AuditFinding(
                        severity="info",
                        category="skill",
                        target=f"{skill_dir.name}/{f.name}",
                        message=f"目录中有额外的 .md 文件: {f.name}",
                        suggestion="合并到 SKILL.md 或删除",
                    ))
            else:
                findings.append(AuditFinding(
                    severity="info",
                    category="skill",
                    target=f"{skill_dir.name}/{f.name}",
                    message=f"目录中有额外的 .md 文件: {f.name}",
                    suggestion="合并到 SKILL.md 或删除",
                ))

    # 注意：不再检查触发词冲突，触发逻辑已改为 LLM，不再依赖 triggers 字段

    return findings


def scan_skill_overlap() -> list[AuditFinding]:
    """检测 skill 之间的职责重叠。

    逻辑：
    1. 收集所有 skill 的 (name, description)
    2. 对每对 skill，用关键词重叠度判断是否职责重叠
    3. 仅在 description 有高度重叠（>60% 的词相同）时报告
    """
    findings: list[AuditFinding] = []

    if not SKILLS_DIR.exists():
        return findings

    # 英文停用词
    _EN_STOP_WORDS = frozenset({
        "the", "a", "is", "for", "to", "of", "and", "in", "on", "with", "at",
        "an", "or", "it", "be", "as", "by", "this", "that", "are", "was",
    })

    def _extract_keywords(text: str) -> set[str]:
        """从文本中提取关键词（中文用 jieba，英文按空格分词）。"""
        keywords: set[str] = set()
        # 英文词
        en_words = re.findall(r"[a-zA-Z]+", text)
        for w in en_words:
            w_lower = w.lower()
            if len(w_lower) >= 2 and w_lower not in _EN_STOP_WORDS:
                keywords.add(w_lower)
        # 中文词（用 jieba 分词）
        chinese_text = re.sub(r"[a-zA-Z0-9\s\-_/\\.,;:!?(){}[\]\"'`~@#$%^&*+=|<>]", " ", text)
        if chinese_text.strip():
            try:
                import jieba
                for word in jieba.cut(chinese_text):
                    word = word.strip()
                    if len(word) >= 2:
                        keywords.add(word)
            except ImportError:
                # jieba 不可用时，按字拆分中文（长度>=2 的连续中文片段）
                for seg in re.findall(r"[\u4e00-\u9fff]{2,}", chinese_text):
                    keywords.add(seg)
        return keywords

    # 收集所有 skill 的 name 和 description
    skill_infos: list[tuple[str, str, set[str]]] = []  # (name, description, keywords)

    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            continue
        try:
            import yaml
            meta = yaml.safe_load(fm_match.group(1)) or {}
        except Exception:
            continue

        name = meta.get("name", skill_dir.name)
        description = meta.get("description", "")
        if not description:
            continue

        kws = _extract_keywords(description)
        if kws:
            skill_infos.append((name, description, kws))

    # 两两比较
    for i in range(len(skill_infos)):
        for j in range(i + 1, len(skill_infos)):
            name_a, desc_a, kws_a = skill_infos[i]
            name_b, desc_b, kws_b = skill_infos[j]

            overlap = kws_a & kws_b
            if len(overlap) <= 3:
                continue

            # 检查重叠度是否 >60%（以较小集合为基准）
            smaller = min(len(kws_a), len(kws_b))
            if smaller == 0:
                continue
            overlap_ratio = len(overlap) / smaller
            if overlap_ratio <= 0.6:
                continue

            findings.append(AuditFinding(
                severity="warning",
                category="skill",
                target=f"{name_a} / {name_b}",
                message=f"两个 skill 的 description 存在高度职责重叠（重叠词 {len(overlap)} 个，"
                        f"重叠率 {overlap_ratio:.0%}）：{', '.join(sorted(overlap)[:10])}",
                suggestion=f"建议检查 [{name_a}] 和 [{name_b}] 是否可以合并",
            ))

    return findings


def scan_projects() -> list[AuditFinding]:
    """扫描所有 projects，返回审计发现列表。"""
    findings: list[AuditFinding] = []

    if not PROJECTS_DIR.exists():
        return findings

    for project_md in PROJECTS_DIR.glob("*.md"):
        try:
            content = project_md.read_text(encoding="utf-8")
        except OSError:
            continue

        name = project_md.stem

        # 检查第一行是否为标题
        lines = content.splitlines()
        if not lines:
            findings.append(AuditFinding(
                severity="error",
                category="project",
                target=name,
                message="文件为空",
            ))
            continue

        first_line = lines[0].strip()
        if not first_line.startswith("# "):
            findings.append(AuditFinding(
                severity="warning",
                category="project",
                target=name,
                message="第一行不是 markdown 标题（# 项目名），格式不规范",
                suggestion="第一行改为 # 项目名",
            ))

        # 检查是否只有标题没有内容
        if len(content.strip()) < len(first_line) + 5:
            findings.append(AuditFinding(
                severity="warning",
                category="project",
                target=name,
                message="文件几乎只有标题，内容为空或不完整",
            ))

        # 检查路径是否有效（项目信息里如果有路径的话）
        path_pattern = re.findall(r"[-路径路径Path path:]+[:：]\s*([^\s\n]+)", content)
        for path_str in path_pattern:
            # 跳过 URL（http/https 协议或协议相对 URL）
            if path_str.startswith(('http://', 'https://', '//')):
                continue
            if path_str.startswith("/") or path_str.startswith("~"):
                p = Path(path_str).expanduser()
                if not p.exists():
                    findings.append(AuditFinding(
                        severity="warning",
                        category="project",
                        target=name,
                        message=f"记录的项目路径不存在: {path_str}",
                        suggestion="确认路径是否正确，或更新为新路径",
                    ))

        # 检查日期分节（查找过旧的更新）
        date_sections = re.findall(r"## (\d{4}-\d{2}-\d{2})", content)
        if date_sections:
            from datetime import date
            today = date.today()
            try:
                latest = max(datetime.strptime(d, "%Y-%m-%d").date() for d in date_sections)
                age_days = (today - latest).days
                if age_days > 180:
                    findings.append(AuditFinding(
                        severity="info",
                        category="project",
                        target=name,
                        message=f"最近一次更新是 {latest}，已 {age_days} 天未更新",
                    ))
            except ValueError:
                pass

        # 检查是否有未闭合的代码块
        code_blocks = re.findall(r"```", content)
        if len(code_blocks) % 2 != 0:
            findings.append(AuditFinding(
                severity="warning",
                category="project",
                target=name,
                message="存在未闭合的代码块（``` 数量为奇数）",
                suggestion="检查并修复代码块配对",
            ))

    return findings


def scan_learned_modules() -> list[AuditFinding]:
    """扫描所有 learned_modules，返回审计发现列表。"""
    findings: list[AuditFinding] = []

    if not LEARNED_MODULES_DIR.exists():
        return findings

    for py_file in sorted(LEARNED_MODULES_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        name = py_file.stem
        try:
            code = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # 语法检查
        try:
            import py_compile
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as e:
            findings.append(AuditFinding(
                severity="error",
                category="module",
                target=name,
                message=f"语法错误: {e}",
                suggestion="修复语法错误",
            ))
            continue

        # 危险 import 检查
        BLOCKED = frozenset({"src", "src.core", "src.tools", "src.feishu",
                              "src.skills", "src.memory", "src.platforms", "src.selfupdate",
                              "src.planning"})
        for line in code.splitlines():
            stripped = line.strip()
            m = re.match(r"^from\s+(\S+)", stripped)
            if m and m.group(1).split(".")[0] in BLOCKED:
                findings.append(AuditFinding(
                    severity="error",
                    category="module",
                    target=name,
                    message=f"危险 import: {stripped}",
                    suggestion="移除此 import，禁止 learned_module 调用 src 内部模块",
                ))
            m = re.match(r"^import\s+(\S+)", stripped)
            if m and m.group(1).split(".")[0] in BLOCKED:
                findings.append(AuditFinding(
                    severity="error",
                    category="module",
                    target=name,
                    message=f"危险 import: {stripped}",
                    suggestion="移除此 import",
                ))

        # 检查是否有 TOOL_SCHEMA 或 TOOL_RUNNER
        has_schema = "TOOL_SCHEMA" in code
        has_runner = "TOOL_RUNNER" in code
        if has_schema and not has_runner:
            findings.append(AuditFinding(
                severity="warning",
                category="module",
                target=name,
                message="定义了 TOOL_SCHEMA 但缺少 TOOL_RUNNER，工具不会被注册",
                suggestion="添加 TOOL_RUNNER 函数: TOOL_RUNNER(params: dict) -> str",
            ))
        if has_runner and not has_schema:
            findings.append(AuditFinding(
                severity="warning",
                category="module",
                target=name,
                message="定义了 TOOL_RUNNER 但缺少 TOOL_SCHEMA，无法注册为工具",
                suggestion="添加 TOOL_SCHEMA（OpenAI function calling schema）",
            ))

        # 检查 TOOL_RUNNER 函数签名
        if has_runner:
            runner_match = re.search(r"def\s+TOOL_RUNNER\s*\([^)]*\)\s*(?:->\s*\w+)?\s*:", code)
            if not runner_match:
                findings.append(AuditFinding(
                    severity="warning",
                    category="module",
                    target=name,
                    message="TOOL_RUNNER 签名不符合规范，应为: def TOOL_RUNNER(params: dict) -> str:",
                ))

        # 检查文件大小（异常大的模块）
        if len(code) > 50_000:
            findings.append(AuditFinding(
                severity="info",
                category="module",
                target=name,
                message=f"模块代码 {len(code)} 字符，较大。建议拆分。",
            ))

    return findings


# ── 主审计流程 ───────────────────────────────────────────────────────────────


def scan_user_patterns(days: int = 1) -> list[AuditFinding]:
    """扫描近期 session 日志，检测用户高频重复操作，判断是否需要沉淀为 skill。

    检测逻辑：
    1. 统计近期 session 中用户请求的出现频次
    2. 用 SkillIndex 语义检索判断是否已被现有 skill 覆盖
    3. 过滤掉基本工具能力
    4. 出现 3 次以上的模式标记为建议沉淀
    """
    import json
    from collections import Counter
    from datetime import date, timedelta

    findings: list[AuditFinding] = []
    sessions_dir = LAMIX_DIR / "memory" / "sessions"
    if not sessions_dir.exists():
        return findings

    # 1. 收集近期用户消息
    today = date.today()
    cutoff = today - timedelta(days=days)
    user_messages: list[str] = []

    for day_dir in sorted(sessions_dir.iterdir()):
        if not day_dir.is_dir() or day_dir.name.endswith(".md"):
            continue
        try:
            day_date = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day_date < cutoff:
            continue

        for channel_dir in day_dir.iterdir():
            if not channel_dir.is_dir():
                continue
            for jsonl_file in channel_dir.glob("*.jsonl"):
                try:
                    with open(jsonl_file, encoding="utf-8") as f:
                        for line in f:
                            rec = json.loads(line)
                            if rec.get("role") == "user":
                                text = rec.get("content", "")
                                if isinstance(text, str) and len(text.strip()) > 3:
                                    # 过滤掉太短的、命令类的、闲聊类的
                                    stripped = text.strip()
                                    if stripped.startswith("/"):
                                        continue
                                    if len(stripped) < 5:
                                        continue
                                    user_messages.append(stripped)
                except Exception:
                    continue

    if not user_messages:
        return findings

    # 2. 精确去重统计（不额外做关键词聚类，直接用原始消息匹配）
    msg_counter = Counter(user_messages)

    # 3. 使用 SkillIndex 检索判断高频操作是否已被现有 skill 覆盖
    from src.core.indexer import SkillIndex
    from src.core.config import INDEX_DIR

    skill_index = SkillIndex(SKILLS_DIR, INDEX_DIR)
    try:
        skill_index.load_or_build()
    except Exception as e:
        logger.warning(f"scan_user_patterns: SkillIndex 加载失败: {e}")
        skill_index = None

    # 4. 基本工具能力过滤：判断操作是否属于 agent 基本能力，不需要沉淀为 skill
    from src.core.tools import get_all_schemas
    _TOOL_PATTERNS: dict[str, str] = {}
    for schema in get_all_schemas():
        func = schema.get("function", {})
        name = func.get("name", "")
        if not name:
            continue
        # 用工具名 + description 自动生成基础 pattern
        desc = func.get("description", "")
        # 从 description 提取英文单词和中文关键词
        auto_tokens = re.findall(r"[a-zA-Z]+", f"{name} {desc}")
        _TOOL_PATTERNS[name] = "|".join(set(t.lower() for t in auto_tokens if len(t) >= 2))

    # 补充常见中文口语变体（这些是工具能力但 description 里不会出现的词）
    # 只覆盖明确的单步操作，不覆盖复杂工作流（如"部署到生产环境"）
    _CAPABILITY_EXTENSIONS: dict[str, str] = {
        "shell": r"git|push|pull|提交代码|执行一下|运行.*脚本",
        "file_read": r"看看|查看|读取|读一下|看看日志",
        "file_write": r"写入|保存|创建文件",
        "search": r"搜索|查找|找.*文件",
        "web_search": r"搜一下|查一下|搜索.*网",
    }
    for tool_name, ext in _CAPABILITY_EXTENSIONS.items():
        if tool_name in _TOOL_PATTERNS:
            _TOOL_PATTERNS[tool_name] = _TOOL_PATTERNS[tool_name] + "|" + ext

    def _is_basic_capability(query: str) -> bool:
        """判断 query 是否属于 agent 基本工具能力范围。"""
        for tool_name, pattern in _TOOL_PATTERNS.items():
            if pattern and re.search(pattern, query, re.IGNORECASE):
                return True
        return False

    # 5. 找出高频且未覆盖的模式
    # 先按频次排序，高频优先
    sorted_msgs = msg_counter.most_common()

    for msg, count in sorted_msgs:
        if count < 3:
            break  # 后续消息频次更低，不需要继续

        # 过滤掉纯闲聊/问候
        chat_patterns = {"你好", "咋样", "啥情况", "继续", "你在干啥", "谢谢",
                          "你刚才在做什么", "我上次在让你干啥", "刚刚你在",
                          "刚刚你", "上次我最后让你干的事儿", "我上次",
                          "你在做什么", "你刚才", "你好吗"}
        if any(p in msg for p in chat_patterns):
            continue

        # 检查 1：是否已被现有 skill 覆盖
        # 用 SkillIndex 检索，取 top_k=5，只要有一个 skill 的 description
        # 能覆盖该操作就跳过
        covered_by = ""
        if skill_index is not None:
            # 用更严格的阈值（0.5）确保只有真正相关的 skill 才算覆盖
            matched = skill_index.search(msg, top_k=5, similarity_threshold=0.5)
            if matched:
                for m in matched:
                    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", m, re.DOTALL)
                    if fm:
                        try:
                            import yaml
                            meta = yaml.safe_load(fm.group(1)) or {}
                            # 用 skill name 判断是否覆盖，而非只靠阈值
                            skill_name = meta.get("name", "")
                            skill_desc = meta.get("description", "")
                            # 如果 skill name 或 description 包含该消息的核心词，认为覆盖
                            if skill_name and len(skill_name) > 1:
                                # 检查消息中是否包含 skill name 的关键部分
                                # 简单策略：检查 skill name 的每个词是否在消息中
                                name_words = set(re.findall(r"[\w]+", skill_name.lower()))
                                name_words = {w for w in name_words if len(w) >= 2}
                                msg_words = set(re.findall(r"[\w]+", msg.lower()))
                                if name_words and name_words & msg_words:
                                    covered_by = skill_name
                                    break
                        except Exception:
                            pass
                    # 如果没有 frontmatter，用正文内容做模糊匹配
                    # 简单策略：消息长度 <30 且是某个 skill 的子串
                    if not covered_by and len(msg) < 40:
                        if msg in m:
                            covered_by = "(skill匹配)"
                            break

        if covered_by:
            continue  # 已被现有 skill 覆盖，跳过

        # 检查 2：是否属于 agent 基本工具能力
        if _is_basic_capability(msg):
            continue  # 基本能力不需要沉淀为 skill

        # 找到所有与该消息语义相同的高频变体
        # 统计所有与当前消息有共同关键词的消息（用于给出更多样例）
        related = []
        msg_words = set(re.findall(r"[\w]+", msg.lower()))
        msg_words = {w for w in msg_words if len(w) >= 2}
        for other_msg, other_count in sorted_msgs:
            if other_msg == msg:
                continue
            other_words = set(re.findall(r"[\w]+", other_msg.lower()))
            other_words = {w for w in other_words if len(w) >= 2}
            if msg_words and other_words:
                overlap = msg_words & other_words
                # 如果有 2 个以上的共同关键词，认为相关
                if len(overlap) >= 2 and other_count >= 2:
                    related.append(other_msg)

        suggestions = [msg]
        if related:
            suggestions.extend(related[:4])

        findings.append(AuditFinding(
            severity="info",
            category="skill",
            target="高频操作模式",
            message=f"检测到高频操作（{count}次）：{msg}",
            suggestion=f"考虑沉淀为 skill。相关请求：{suggestions}",
        ))

    return findings


def run_audit(auto_fix: bool = True) -> AuditReport:
    """执行完整审计，返回报告。

    auto_fix=True 时，对可安全修复的发现执行自动修复，修复结果写入 finding 的 fixed 字段。
    """
    import time
    start = time.time()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 统计扫描数量
    skills_count = len(list(SKILLS_DIR.glob("*/SKILL.md"))) if SKILLS_DIR.exists() else 0
    projects_count = len(list(PROJECTS_DIR.glob("*.md"))) if PROJECTS_DIR.exists() else 0
    modules_count = len([p for p in LEARNED_MODULES_DIR.glob("*.py") if not p.name.startswith("_")]) if LEARNED_MODULES_DIR.exists() else 0

    findings: list[AuditFinding] = []
    findings.extend(scan_skills(auto_fix=auto_fix))
    findings.extend(scan_skill_overlap())
    findings.extend(scan_projects())
    findings.extend(scan_learned_modules())
    findings.extend(scan_user_patterns())
    findings.extend(cleanup_stale_knowledge(auto_fix=auto_fix))

    duration = time.time() - start

    report = AuditReport(
        timestamp=timestamp,
        duration_seconds=duration,
        skills_scanned=skills_count,
        projects_scanned=projects_count,
        modules_scanned=modules_count,
        findings=findings,
    )
    return report


def format_report_detail(report: AuditReport) -> str:
    """格式化报告详情，用于飞书消息。"""
    lines = [report.summary_text(), ""]

    by_severity = report.findings_by_severity

    # 按严重程度输出
    for severity in ("error", "warning", "info"):
        items = by_severity[severity]
        if not items:
            continue

        icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}[severity]
        header = f"{icon} {severity.upper()} ({len(items)} 条)"

        # 按类别分组
        by_category: dict[str, list[AuditFinding]] = {}
        for f in items:
            by_category.setdefault(f.category, []).append(f)

        lines.append(header)
        for cat, cat_findings in by_category.items():
            cat_icon = {"skill": "⚙️", "project": "📁", "module": "🐍"}.get(cat, "•")
            lines.append(f"  {cat_icon} {cat}: {len(cat_findings)} 条")
            for f in cat_findings:
                lines.append(f"    • [{f.target}] {f.message}")
                if f.suggestion:
                    lines.append(f"      → {f.suggestion}")
        lines.append("")

    # 已自动修复的问题列表
    fixed_items = [f for f in report.findings if f.fixed]
    if fixed_items:
        lines.append("🔧 已自动修复 ({} 条)".format(len(fixed_items)))
        for f in fixed_items:
            lines.append(f"  • [{f.target}] {f.fix_detail}")
        lines.append("")

    return "\n".join(lines)


# ── 基于使用频率的归档清理 ──────────────────────────────────────────────────

def cleanup_stale_knowledge(auto_fix: bool = True) -> list[AuditFinding]:
    """根据使用频率归档长期未用的 skill/info/project。

    规则：
    - 7天内有调用的，留着
    - 7天内没调用，且总调用次数0次的，归档
    - 7天内没调用，但总调用次数>0的，暂时留着
    - 30天内没调用的，归档
    """
    from datetime import date, timedelta
    import shutil

    findings: list[AuditFinding] = []
    today = date.today()
    stale_7 = today - timedelta(days=7)
    stale_30 = today - timedelta(days=30)

    def _parse_date(s: str) -> date | None:
        try:
            return date.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None

    # ── Skills 清理 ──
    if SKILLS_DIR.exists():
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
            if not fm_match:
                continue
            try:
                import yaml
                meta = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                continue

            name = meta.get("name", skill_dir.name)
            last_used = _parse_date(meta.get("last_used_at", ""))
            created = _parse_date(meta.get("created_at", ""))
            invocation_count = int(meta.get("invocation_count", 0))

            # 判断是否应该归档
            should_archive = False
            reason = ""
            anchor = last_used or created

            if anchor and anchor <= stale_30:
                should_archive = True
                reason = f"30天未使用（最后使用: {anchor}）"
            elif anchor and anchor <= stale_7 and invocation_count == 0:
                should_archive = True
                reason = f"7天未使用且从未被调用（创建于: {created}）"

            if should_archive and auto_fix:
                archive_dir = SKILLS_DIR / ".archived"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / skill_dir.name
                if dest.exists():
                    import uuid
                    dest = archive_dir / f"{skill_dir.name}_{uuid.uuid4().hex[:6]}"
                shutil.move(str(skill_dir), str(dest))
                findings.append(AuditFinding(
                    severity="info",
                    category="skill",
                    target=name,
                    message=f"已归档: {reason}",
                    suggestion="如需恢复，从 .archived/ 目录移回",
                    fixed=True,
                    fix_detail=f"移至 {dest}",
                ))
            elif should_archive:
                findings.append(AuditFinding(
                    severity="warning",
                    category="skill",
                    target=name,
                    message=f"建议归档: {reason}",
                    suggestion="auto_fix=True 时自动归档",
                ))

    # ── Info 清理 ──
    if PROJECTS_DIR.exists():
        _info_dir = PROJECTS_DIR.parent / "info"
    else:
        from src.core.config import LAMIX_DIR
        _info_dir = LAMIX_DIR / "memory" / "info"

    if _info_dir.exists():
        for info_file in _info_dir.glob("*.md"):
            try:
                raw = info_file.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
            if not fm_match:
                continue
            try:
                import yaml
                meta = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                continue

            name = meta.get("name", info_file.stem)
            last_used = _parse_date(meta.get("last_used_at", ""))
            created = _parse_date(meta.get("created_at", ""))

            should_archive = False
            reason = ""
            anchor = last_used or created

            if anchor and anchor <= stale_30:
                should_archive = True
                reason = f"30天未使用（最后使用: {anchor}）"
            elif anchor and anchor <= stale_7:
                # info 没有 invocation_count，7天没用直接归档
                should_archive = True
                reason = f"7天未使用（最后使用: {anchor}）"

            if should_archive and auto_fix:
                archive_dir = _info_dir / ".archived"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / info_file.name
                if dest.exists():
                    import uuid
                    dest = archive_dir / f"{info_file.stem}_{uuid.uuid4().hex[:6]}.md"
                shutil.move(str(info_file), str(dest))
                findings.append(AuditFinding(
                    severity="info",
                    category="info",
                    target=name,
                    message=f"已归档: {reason}",
                    fixed=True,
                    fix_detail=f"移至 {dest}",
                ))
            elif should_archive:
                findings.append(AuditFinding(
                    severity="warning",
                    category="info",
                    target=name,
                    message=f"建议归档: {reason}",
                ))

    # ── Projects 清理 ──
    if PROJECTS_DIR.exists():
        for proj_file in PROJECTS_DIR.glob("*.md"):
            try:
                raw = proj_file.read_text(encoding="utf-8")
            except OSError:
                continue
            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
            if not fm_match:
                continue
            try:
                import yaml
                meta = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                continue

            name = meta.get("name", proj_file.stem)
            last_used = _parse_date(meta.get("last_used_at", ""))
            created = _parse_date(meta.get("created_at", ""))

            should_archive = False
            reason = ""
            anchor = last_used or created

            if anchor and anchor <= stale_30:
                should_archive = True
                reason = f"30天未使用（最后使用: {anchor}）"

            if should_archive and auto_fix:
                archive_dir = PROJECTS_DIR / ".archived"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / proj_file.name
                if dest.exists():
                    import uuid
                    dest = archive_dir / f"{proj_file.stem}_{uuid.uuid4().hex[:6]}.md"
                shutil.move(str(proj_file), str(dest))
                findings.append(AuditFinding(
                    severity="info",
                    category="project",
                    target=name,
                    message=f"已归档: {reason}",
                    fixed=True,
                    fix_detail=f"移至 {dest}",
                ))
            elif should_archive:
                findings.append(AuditFinding(
                    severity="warning",
                    category="project",
                    target=name,
                    message=f"建议归档: {reason}",
                ))

    return findings
