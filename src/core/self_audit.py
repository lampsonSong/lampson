"""自我审计模块：定时扫描 skills、projects、learned_modules，检查过时、错误、冗余。

审计触发：
    1. 定时：每天凌晨 4 点（可配置），在后台线程运行
    2. 手动：用户输入 /self-audit 命令

审计维度：
    - Skills：内容过时、结构异常、步骤缺失、触发词冲突、孤立文件
    - Projects：路径失效、信息过时、格式异常
    - Learned Modules：语法错误、危险 import、schema 不完整

审计报告通过飞书发送给 owner_chat_id（已配置时）。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import LAMPSON_DIR, SKILLS_DIR, PROJECTS_DIR, load_config

logger = logging.getLogger(__name__)

LEARNED_MODULES_DIR = LAMPSON_DIR / "learned_modules"

# 默认审计时间（本地时间）
DEFAULT_AUDIT_HOUR = 4  # 凌晨 4 点
DEFAULT_AUDIT_MINUTE = 0

# 检查间隔（秒）
_POLL_INTERVAL = 60


# ── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class AuditFinding:
    """一条审计发现。"""
    severity: str          # info / warning / error
    category: str           # skill / project / module
    target: str            # 文件/目录名
    message: str           # 发现描述
    suggestion: str = ""   # 修复建议


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

def scan_skills() -> list[AuditFinding]:
    """扫描所有 skills，返回审计发现列表。"""
    findings: list[AuditFinding] = []

    if not SKILLS_DIR.exists():
        return findings

    all_skills = {}
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
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

        all_skills[skill_dir.name] = content

        # 检查 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            findings.append(AuditFinding(
                severity="warning",
                category="skill",
                target=skill_dir.name,
                message="SKILL.md 缺少 frontmatter（--- ... ---）",
                suggestion="添加 YAML frontmatter（name、description、triggers）",
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
                triggers = meta.get("triggers", [])
                if not triggers or (isinstance(triggers, list) and len(triggers) == 0):
                    findings.append(AuditFinding(
                        severity="warning",
                        category="skill",
                        target=skill_dir.name,
                        message="frontmatter 缺少 triggers 字段（skill 无法被触发）",
                    ))
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

        # 提取编号步骤
        step_matches = re.findall(r"^(\d+)\.\s+\*\*(.+?)\*\*", body, re.MULTILINE)
        if not step_matches:
            findings.append(AuditFinding(
                severity="info",
                category="skill",
                target=skill_dir.name,
                message="正文没有找到编号步骤（如 1. **步骤名**），可能不是规范的 skill",
            ))

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
        for f in skill_dir.iterdir():
            if f.suffix == ".md" and f != skill_md:
                findings.append(AuditFinding(
                    severity="info",
                    category="skill",
                    target=f"{skill_dir.name}/{f.name}",
                    message=f"目录中有额外的 .md 文件: {f.name}",
                    suggestion="合并到 SKILL.md 或删除",
                ))

    # 检查触发词冲突（不同 skill 有相同触发词）
    trigger_map: dict[str, list[str]] = {}
    for skill_name, content in all_skills.items():
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            continue
        try:
            import yaml
            meta = yaml.safe_load(fm_match.group(1)) or {}
            for t in meta.get("triggers", []):
                trigger_map.setdefault(str(t).lower(), []).append(skill_name)
        except yaml.YAMLError:
            continue

    for trigger, names in trigger_map.items():
        if len(names) > 1:
            findings.append(AuditFinding(
                severity="warning",
                category="skill",
                target=", ".join(names),
                message=f"触发词冲突: '{trigger}' 同时被多个 skill 使用",
                suggestion="保留一个，合并或调整其他 skill 的触发词",
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


def scan_user_patterns(days: int = 7) -> list[AuditFinding]:
    """扫描近期 session 日志，检测用户高频重复操作，判断是否需要沉淀为 skill。

    检测逻辑：
    1. 统计近期 session 中用户请求的出现频次
    2. 将语义相似的请求聚合（关键词交集）
    3. 过滤掉已有 skill 覆盖的操作
    4. 出现 3 次以上的模式标记为建议沉淀
    """
    import json
    from collections import Counter
    from datetime import date, timedelta

    findings: list[AuditFinding] = []
    sessions_dir = LAMPSON_DIR / "memory" / "sessions"
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

    # 2. 精确去重统计
    msg_counter = Counter(user_messages)

    # 3. 关键词聚合：将语义相似的消息合并
    # 提取每条消息的关键词集合
    def extract_keywords(text: str) -> set[str]:
        stop_words = {"的", "了", "吗", "呢", "吧", "啊", "我", "你", "是", "在",
                       "有", "不", "也", "都", "就", "要", "会", "可以", "能",
                       "把", "给", "让", "跟", "和", "对", "这个", "那个",
                       "一下", "一个", "什么", "怎么", "如何", "帮我"}
        words = set(re.findall(r"[\w]+", text.lower()))
        return words - stop_words - {w for w in words if len(w) < 2}

    # 按频次排序，高频优先做聚类中心
    sorted_msgs = msg_counter.most_common()
    clustered: dict[int, list[tuple[str, int]]] = {}  # cluster_id -> [(msg, count)]
    assigned: dict[str, int] = {}  # msg -> cluster_id
    cluster_id = 0

    for msg, count in sorted_msgs:
        if msg in assigned:
            continue
        kw = extract_keywords(msg)
        if not kw:
            continue

        # 检查是否和已有聚类中心相似
        found_cluster = None
        for cid, members in clustered.items():
            center_msg = members[0][0]
            center_kw = extract_keywords(center_msg)
            if not center_kw:
                continue
            overlap = kw & center_kw
            union = kw | center_kw
            similarity = len(overlap) / len(union) if union else 0
            if similarity >= 0.4:
                found_cluster = cid
                break

        if found_cluster is not None:
            clustered[found_cluster].append((msg, count))
            assigned[msg] = found_cluster
        else:
            clustered[cluster_id] = [(msg, count)]
            assigned[msg] = cluster_id
            cluster_id += 1

    # 4. 加载已有 skills 的 description 和 triggers，用于过滤
    existing_skill_keywords: set[str] = set()
    if SKILLS_DIR.exists():
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            try:
                text = skill_md.read_text(encoding="utf-8").lower()
                existing_skill_keywords.update(extract_keywords(text))
            except OSError:
                continue

    # 5. 找出高频且未覆盖的模式
    for cid, members in clustered.items():
        total_count = sum(c for _, c in members)
        representative = members[0][0]  # 频次最高的作为代表

        if total_count < 3:
            continue

        # 检查是否已被现有 skill 覆盖
        rep_kw = extract_keywords(representative)
        overlap_with_skills = rep_kw & existing_skill_keywords
        coverage_ratio = len(overlap_with_skills) / len(rep_kw) if rep_kw else 0

        if coverage_ratio >= 0.7:
            continue  # 已被现有 skill 覆盖

        # 过滤掉纯闲聊/问候
        chat_patterns = {"你好", "咋样", "啥情况", "继续", "你在干啥", "谢谢",
                          "你刚才在做什么", "我上次在让你干啥", "刚刚你在",
                          "刚刚你", "上次我最后让你干的事儿", "我上次",
                          "你在做什么", "你刚才", "你好吗"}
        if any(p in representative for p in chat_patterns):
            continue

        # 过滤掉已有命令覆盖的操作（如 /restart-lampson skill 已有重启能力）
        # 合并语义相似的变体到同一条 finding，避免多条重复建议
        skip_keywords = {"重启一下你自己，然后告诉", "重启一下你自己，然后说"}
        if any(k in representative for k in skip_keywords):
            # 这是 restart-lampson 的变体，已被覆盖
            continue

        examples = [m for m, _ in members[:5]]
        findings.append(AuditFinding(
            severity="info",
            category="skill",
            target="高频操作模式",
            message=f"检测到高频操作（{total_count}次）：{representative}",
            suggestion=f"考虑沉淀为 skill。相关请求：{examples}",
        ))

    return findings


def run_audit() -> AuditReport:
    """执行完整审计，返回报告。"""
    import time
    start = time.time()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 统计扫描数量
    skills_count = len(list(SKILLS_DIR.glob("*/SKILL.md"))) if SKILLS_DIR.exists() else 0
    projects_count = len(list(PROJECTS_DIR.glob("*.md"))) if PROJECTS_DIR.exists() else 0
    modules_count = len([p for p in LEARNED_MODULES_DIR.glob("*.py") if not p.name.startswith("_")]) if LEARNED_MODULES_DIR.exists() else 0

    findings: list[AuditFinding] = []
    findings.extend(scan_skills())
    findings.extend(scan_projects())
    findings.extend(scan_learned_modules())
    findings.extend(scan_user_patterns())

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

    return "\n".join(lines)


# ── 定时调度器 ───────────────────────────────────────────────────────────────

class SelfAuditScheduler:
    """自我审计定时调度器：在独立线程中运行，每天指定时间触发审计。"""

    def __init__(
        self,
        hour: int = DEFAULT_AUDIT_HOUR,
        minute: int = DEFAULT_AUDIT_MINUTE,
    ) -> None:
        self.hour = hour
        self.minute = minute
        self._stop = False
        self._thread: Any = None

    def _next_fire_time(self) -> float:
        """计算距离下一次触发的时间（秒）。"""
        from datetime import timedelta
        import calendar
        now = datetime.now()
        today_target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if now < today_target:
            future = today_target
        else:
            future = today_target + timedelta(days=1)
        return calendar.timegm(future.utctimetuple()) - calendar.timegm(now.utctimetuple())

    def _wait_until_next(self) -> None:
        """等待到下一次触发时间。"""
        while not self._stop:
            delay = self._next_fire_time()
            if delay <= 0:
                delay = _POLL_INTERVAL
            print(f"[self_audit] 下次审计计划: {delay:.0f} 秒后（{self.hour:02d}:{self.minute:02d}）")
            # 分段等待，方便快速响应 stop 信号
            waited = 0.0
            while waited < delay and not self._stop:
                step = min(_POLL_INTERVAL, delay - waited)
                time.sleep(step)
                waited += step

    def _loop(self) -> None:
        """调度线程主循环。"""
        while not self._stop:
            self._wait_until_next()
            if self._stop:
                break

            print("[self_audit] 定时审计触发", flush=True)
            try:
                report = run_audit()
                self._deliver_report(report)
            except Exception as e:
                print(f"[self_audit] 审计执行失败: {e}")

    def _deliver_report(self, report: AuditReport) -> None:
        """发送审计报告到飞书。"""
        config = load_config()
        owner_chat_id = config.get("feishu", {}).get("owner_chat_id", "").strip()
        app_id = config.get("feishu", {}).get("app_id", "").strip()
        app_secret = config.get("feishu", {}).get("app_secret", "").strip()

        if not owner_chat_id or not app_id or not app_secret:
            logger.debug("[self_audit] 未配置飞书，跳过推送")
            return

        try:
            from src.feishu.client import FeishuClient
            client = FeishuClient(app_id=app_id, app_secret=app_secret)
            content = format_report_detail(report)
            # 飞书消息有长度限制，超过 4000 字截断
            if len(content) > 4000:
                content = content[:4000] + "\n\n...（报告过长已截断）"
            client.send_message(
                receive_id=owner_chat_id,
                text=f"🕐 Lampson 自我审计报告\n\n{content}",
                receive_id_type="chat_id",
            )
            print("[self_audit] 审计报告已发送", flush=True)
        except Exception as e:
            print(f"[self_audit] 报告推送失败: {e}")

    def start(self) -> None:
        """启动调度线程。"""
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True, name="SelfAuditScheduler")
        self._thread.start()
        print(f"[self_audit] 调度器已启动（计划时间: {self.hour:02d}:{self.minute:02d}）")

    def stop(self) -> None:
        """停止调度线程。"""
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def trigger_now(self) -> AuditReport:
        """立即触发一次审计（用于手动调用）。"""
        report = run_audit()
        self._deliver_report(report)
        return report
