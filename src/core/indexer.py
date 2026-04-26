"""Skill / Project 语义索引：JSONL 存储、增量更新、可选 sentence-transformers 与关键词降级。"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 国内环境默认使用 HuggingFace 镜像
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from src.core.skills_tools import _parse_skill

try:
    from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - 环境无重依赖
    SentenceTransformer = None  # type: ignore[misc, assignment]

_HAS_ST = SentenceTransformer is not None
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


@dataclass
class _LoadedModel:
    """延迟加载的 embedding 模型。"""

    name: str
    _model: Any = field(default=None, repr=False)

    def get(self) -> Any:
        if not _HAS_ST:
            return None
        if self._model is None:
            self._model = SentenceTransformer(self.name)  # type: ignore[union-attr]
        return self._model


def _parse_project_body(content: str) -> str:
    """去掉 YAML frontmatter 后取正文，用于项目摘要与 embedding 文本。"""
    match = _FRONTMATTER_RE.match(content)
    if match:
        return content[match.end() :].strip()
    return content.strip()


def _project_preview_text(name: str, content: str) -> str:
    body = _parse_project_body(content)
    snippet = body[:200] if body else ""
    return f"{name} {snippet}".strip()


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _keyword_match_score(query: str, text: str) -> float:
    if not query.strip() or not text:
        return 0.0
    q = query.lower().strip()
    t = text.lower()
    if q in t:
        return 1.0
    words = _WORD_RE.findall(q)
    if not words:
        return 0.0
    return sum(1.0 for w in words if w in t) / len(words)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[call-arg]
        except OSError:
            pass


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _iter_skill_paths(skills_dir: Path) -> list[Path]:
    if not skills_dir.exists():
        return []
    return list(skills_dir.rglob("SKILL.md"))


def _skill_search_text(parsed: dict[str, Any]) -> str:
    name = str(parsed.get("name", ""))
    desc = str(parsed.get("description", ""))
    tr = parsed.get("triggers", [])
    if isinstance(tr, str):
        parts = [tr] if tr.strip() else []
    else:
        parts = [str(x) for x in tr] if isinstance(tr, list) else []
    return f"{name} {desc} {' '.join(parts)}".strip()


def _skill_created_and_invocation(skill_file: Path) -> tuple[str | None, int]:
    raw = _read_text_file(skill_file)
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return None, 0
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None, 0
    ca = meta.get("created_at")
    ca_str = str(ca)[:10] if ca is not None and str(ca).strip() else None
    ic_raw = meta.get("invocation_count", 0)
    try:
        ic = int(ic_raw)
    except (TypeError, ValueError):
        ic = 0
    return ca_str, ic


def _category_for_skill(skills_dir: Path, skill_file: Path) -> str:
    try:
        rel = skill_file.relative_to(skills_dir)
        prts = rel.parts
        if len(prts) >= 2:
            return prts[-2]
    except ValueError:
        pass
    return "general"


class SkillIndex:
    """Skill 索引管理器（仅元数据 + 关键词检索，不存 embedding）。"""

    def __init__(
        self,
        skills_dir: Path,
        index_dir: Path,
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        skills_management: dict[str, Any] | None = None,
    ) -> None:
        _ = embedding_model  # 保留参数以兼容 Session 调用；Skill 索引不再使用 embedding
        self.skills_dir = skills_dir
        self.index_dir = index_dir
        self._skills_management = skills_management
        self._entries: list[dict[str, Any]] = []
        self._by_path: dict[str, dict[str, Any]] = {}

    def _cleanup_config(self) -> dict[str, int]:
        if self._skills_management is not None:
            from src.core.config import get_skills_management_config

            return get_skills_management_config(
                {"skills_management": self._skills_management}
            )
        from src.core.config import get_skills_management_config, load_config

        return get_skills_management_config(load_config())

    def _maybe_cleanup(self) -> None:
        cfg = self._cleanup_config()
        max_skills = cfg["cleanup_max_skills"]
        if len(self._entries) < max_skills:
            return
        age_days = cfg["cleanup_age_days"]
        min_inv = cfg["cleanup_min_invocations"]
        cutoff = (datetime.now() - timedelta(days=age_days)).date().isoformat()
        to_archive: list[dict[str, Any]] = []
        for e in self._entries:
            ca = e.get("created_at")
            if not ca:
                continue
            ca_str = str(ca)[:10]
            if ca_str > cutoff:
                continue
            try:
                inv_int = int(e.get("invocation_count", 0))
            except (TypeError, ValueError):
                inv_int = 0
            if inv_int <= min_inv:
                to_archive.append(e)
        if not to_archive:
            return
        archive_dir = self.skills_dir / ".archived"
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            logger.warning("Could not create archive dir %s: %s", archive_dir, ex)
            return
        archived_keys: set[str] = set()
        archived_names: list[str] = []
        for e in to_archive:
            pkey = str(e.get("path", ""))
            skill_md = Path(pkey)
            skill_dir = skill_md.parent
            if not skill_dir.is_dir() or not skill_md.is_file():
                continue
            dest = archive_dir / skill_dir.name
            try:
                if dest.exists():
                    logger.warning("Skip archive, destination exists: %s", dest)
                    continue
                shutil.move(str(skill_dir), str(dest))
                archived_keys.add(pkey)
                archived_names.append(str(e.get("name", skill_dir.name)))
            except OSError as ex:
                logger.warning("Archive failed for %s: %s", skill_dir, ex)
        if not archived_keys:
            return
        self._entries = [x for x in self._entries if str(x.get("path", "")) not in archived_keys]
        self._by_path = {r["path"]: r for r in self._entries}
        out_path = self.index_dir / "skills.jsonl"
        _write_jsonl(out_path, self._entries)
        logger.info("Archived %d cold skill(s): %s", len(archived_names), archived_names)

    def load_or_build(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        path = self.index_dir / "skills.jsonl"
        old_rows = {str(r.get("path", "")): r for r in _read_jsonl(path) if r.get("path")}

        skill_files = _iter_skill_paths(self.skills_dir)
        current_paths: set[str] = set()
        new_rows: list[dict[str, Any]] = []

        for sf in skill_files:
            if ".archived" in sf.parts:
                continue
            key = str(sf.resolve())
            current_paths.add(key)
            try:
                mtime = sf.stat().st_mtime
            except OSError:
                continue
            prev = old_rows.get(key)
            if prev and abs(float(prev.get("mtime", 0)) - mtime) < 1e-6:
                row = {k: v for k, v in prev.items() if k != "embedding"}
                if not row.get("search_text"):
                    parsed0 = _parse_skill(sf)
                    if parsed0:
                        row = {**row, "search_text": _skill_search_text(parsed0)}
                if "created_at" not in row or "invocation_count" not in row:
                    ca, inv = _skill_created_and_invocation(sf)
                    row = {**row, "created_at": ca, "invocation_count": inv}
                new_rows.append(row)
                continue
            parsed = _parse_skill(sf)
            if not parsed:
                continue
            stext = _skill_search_text(parsed)
            ca, inv = _skill_created_and_invocation(sf)
            new_rows.append(
                {
                    "name": parsed["name"],
                    "category": _category_for_skill(self.skills_dir, sf),
                    "description": parsed.get("description", ""),
                    "triggers": list(parsed.get("triggers", [])),
                    "path": key,
                    "mtime": mtime,
                    "search_text": stext,
                    "created_at": ca,
                    "invocation_count": inv,
                }
            )

        new_rows = [r for r in new_rows if r.get("path") in current_paths]
        self._by_path = {r["path"]: r for r in new_rows}
        self._entries = list(self._by_path.values())
        _write_jsonl(path, new_rows)
        self._maybe_cleanup()

    def search(
        self, query: str, top_k: int = 3, similarity_threshold: float = 0.3
    ) -> list[str]:
        """关键词检索，返回匹配 skill 的 SKILL.md 全文列表。"""
        if not self._entries or not query.strip():
            return []
        q = query.strip()
        scored: list[tuple[float, str]] = []
        for e in self._entries:
            path = e.get("path", "")
            if not path:
                continue
            s = _keyword_match_score(q, e.get("search_text", ""))
            if s >= similarity_threshold:
                scored.append((s, path))
        scored.sort(key=lambda x: -x[0])
        out: list[str] = []
        for _, p in scored[:top_k]:
            content = _read_text_file(Path(p))
            if content.strip():
                out.append(content)
        return out

    def list_summaries(self) -> list[dict[str, str]]:
        """供 memory 等场景展示的 skill 概要（无需全文）。"""
        items: list[dict[str, str]] = []
        for e in self._entries:
            desc = str(e.get("description", ""))
            trs = e.get("triggers", [])
            if isinstance(trs, list):
                ts = [str(t) for t in trs]
            else:
                ts = []
            items.append(
                {
                    "name": str(e.get("name", "")),
                    "description": desc,
                    "triggers": ", ".join(ts),
                }
            )
        return items


def _iter_project_files(projects_dir: Path) -> list[Path]:
    if not projects_dir.exists():
        return []
    return [p for p in projects_dir.rglob("*.md") if p.is_file()]


class ProjectIndex:
    """Project 语义索引管理器。"""

    def __init__(
        self,
        projects_dir: Path,
        index_dir: Path,
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        self.projects_dir = projects_dir
        self.index_dir = index_dir
        self._model = _LoadedModel(embedding_model)
        self._entries: list[dict[str, Any]] = []
        self._by_path: dict[str, dict[str, Any]] = {}

    @property
    def _use_embedding(self) -> bool:
        return _HAS_ST

    def _embed(self, text: str) -> list[float]:
        if not text.strip() or not self._use_embedding:
            return []
        m = self._model.get()
        if m is None:
            return []
        vec = m.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return [float(x) for x in vec.tolist()]

    def load_or_build(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        path = self.index_dir / "projects.jsonl"
        old_rows = {str(r.get("path", "")): r for r in _read_jsonl(path) if r.get("path")}

        project_files = _iter_project_files(self.projects_dir)
        current_paths: set[str] = set()
        new_rows: list[dict[str, Any]] = []

        for pf in project_files:
            key = str(pf.resolve())
            current_paths.add(key)
            try:
                mtime = pf.stat().st_mtime
            except OSError:
                continue
            prev = old_rows.get(key)
            if prev and abs(float(prev.get("mtime", 0)) - mtime) < 1e-6 and (
                (not self._use_embedding)
                or (prev.get("embedding") and len(prev.get("embedding", [])) > 0)
            ):
                if not prev.get("search_text"):
                    raw0 = _read_text_file(pf)
                    st0 = _project_preview_text(pf.stem, raw0)
                    prev = {**prev, "search_text": st0}
                new_rows.append(prev)
                continue
            raw = _read_text_file(pf)
            name = pf.stem
            stext = _project_preview_text(name, raw)
            emb = self._embed(stext) if self._use_embedding else []
            if self._use_embedding and not emb and prev and prev.get("embedding"):
                emb = [float(x) for x in prev["embedding"]]
            new_rows.append(
                {
                    "name": name,
                    "path": key,
                    "mtime": mtime,
                    "embedding": emb,
                    "search_text": stext,
                }
            )

        new_rows = [r for r in new_rows if r.get("path") in current_paths]
        self._by_path = {r["path"]: r for r in new_rows}
        self._entries = list(self._by_path.values())
        _write_jsonl(path, new_rows)

    def search(
        self, query: str, top_k: int = 2, similarity_threshold: float = 0.3
    ) -> list[str]:
        """语义检索，返回匹配 project 的 .md 全文列表。"""
        if not self._entries or not query.strip():
            return []
        q = query.strip()
        qvec = self._embed(q) if self._use_embedding else []
        scored: list[tuple[float, str]] = []
        for e in self._entries:
            path = e.get("path", "")
            if not path:
                continue
            if qvec and e.get("embedding") and len(e["embedding"]) == len(qvec):
                s = _cosine_sim(qvec, [float(x) for x in e["embedding"]])
            else:
                s = _keyword_match_score(q, e.get("search_text", ""))
            if s >= similarity_threshold:
                scored.append((s, path))
        scored.sort(key=lambda x: -x[0])
        out: list[str] = []
        for _, p in scored[: top_k]:
            content = _read_text_file(Path(p))
            if content.strip():
                out.append(f"# {Path(p).stem}\n\n{content}")
        return out

    def list_summaries(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for e in self._entries:
            name = str(e.get("name", ""))
            items.append(
                {
                    "name": name,
                    "preview": e.get("search_text", name)[:300],
                }
            )
        return items
