"""Configuration = human-written rules (Markdown, applied by the AI) + technical settings (YAML, used by code).

config/rules.md              rules for every project: how to classify tasks and review estimates, in plain language
config/projects/<NAME>.md    extra rules of one project; appended after the common rules and take precedence
config/settings.yaml         technical settings: Jira mapping, Excel headers, required fields, duplicate threshold
config/projects/<NAME>.yaml  per-project settings overrides (only the keys that differ; lists replace)
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "settings.yaml"
DEFAULT_RULES = CONFIG_DIR / "rules.md"
PROJECTS_DIR = CONFIG_DIR / "projects"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
FIELD_CHOICES = ["title", "assignee", "estimate", "category", "description", "acceptanceCriteria"]


# ------------------------------------------------------------------ helpers
def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _backup_and_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def diff_config(base: dict, config: dict) -> dict:
    """Keys of `config` that differ from `base` (recursively for dicts) - what a project file needs to store."""
    out = {}
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            sub = diff_config(base[key], value)
            if sub:
                out[key] = sub
        elif base.get(key) != value:
            out[key] = copy.deepcopy(value)
    return out


def project_path(name: str, suffix: str = ".yaml") -> Path:
    if not _NAME_RE.match(name or ""):
        raise ValueError("Tên dự án chỉ gồm chữ, số, '-' hoặc '_' (tối đa 40 ký tự)")
    return PROJECTS_DIR / f"{name}{suffix}"


def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted({p.stem for p in PROJECTS_DIR.iterdir() if p.suffix in (".yaml", ".md")})


# ------------------------------------------------------------------ technical settings
def load_config(path: str | Path | None = None, project: str | None = None) -> dict[str, Any]:
    config = _read_yaml(Path(path) if path else DEFAULT_CONFIG)
    if project and project_path(project).exists():
        config = deep_merge(config, _read_yaml(project_path(project)))
    return config


def validate_config(config: dict) -> list[str]:
    """Problems in the technical settings; empty list means usable."""
    problems = []
    val = config.get("validation", {})
    if not set(val.get("required_fields", [])) <= set(FIELD_CHOICES):
        problems.append(f"Trường bắt buộc chỉ được chọn trong {FIELD_CHOICES}")
    if not set(val.get("blocking_fields", [])) <= set(val.get("required_fields", [])):
        problems.append("Trường chặn tạo issue phải nằm trong danh sách trường bắt buộc")
    threshold = val.get("duplicate_threshold", 85)
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 100:
        problems.append("Ngưỡng nghi trùng phải trong khoảng 1-100")
    jira = config.get("jira", {})
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", str(jira.get("project_key", ""))):
        problems.append("Project key Jira phải là chữ in hoa/số, vd KAN")
    if "default" not in jira.get("category_mapping", {}):
        problems.append("Map loại task → Jira cần có dòng 'default'")
    return problems


def save_project_settings(name: str, config: dict, base: dict | None = None) -> Path | None:
    """Store only the differences from settings.yaml as config/projects/<name>.yaml (None if nothing differs)."""
    problems = validate_config(config)
    if problems:
        raise ValueError("\n".join(problems))
    overlay = diff_config(base if base is not None else load_config(), config)
    path = project_path(name)
    if not overlay and not path.exists():
        return None
    header = (f"# Cấu hình kỹ thuật riêng dự án {name} - chỉ chứa phần khác so với config/settings.yaml.\n"
              f"# Cập nhật lúc {datetime.now():%Y-%m-%d %H:%M} từ Delivery Planning Agent.\n")
    body = yaml.safe_dump(overlay, allow_unicode=True, sort_keys=False, width=120, default_flow_style=None) if overlay else "{}\n"
    _backup_and_write(path, header + body)
    return path


# ------------------------------------------------------------------ human-written rules
def read_common_rules() -> str:
    return DEFAULT_RULES.read_text(encoding="utf-8") if DEFAULT_RULES.exists() else ""


def read_project_rules(project: str) -> str:
    path = project_path(project, ".md") if project else None
    return path.read_text(encoding="utf-8") if path and path.exists() else ""


def combine_rules(common: str, project_rules: str = "", project: str = "") -> str:
    if not project_rules.strip():
        return common
    # Project rules go first: they override the common rules, and models weigh early text more reliably.
    return (f"# PHẦN A - Rule riêng của dự án {project} (ÁP DỤNG TRƯỚC, ghi đè phần B khi mâu thuẫn)\n\n"
            f"{project_rules.strip()}\n\n---\n# PHẦN B - Rule chung\n\n{common.strip()}\n")


def load_rules(project: str | None = None) -> str:
    """The rules prompt the AI applies: common rules + the project's own rules."""
    return combine_rules(read_common_rules(), read_project_rules(project or ""), project or "")


def save_rules(text: str, project: str | None = None) -> Path:
    """Save the common rules (project=None) or a project's rules; the previous version is kept as .bak."""
    if not text.strip() and not project:
        raise ValueError("Rule chung không được để trống")
    path = project_path(project, ".md") if project else DEFAULT_RULES
    _backup_and_write(path, text.rstrip() + "\n")
    try:  # history of who changed which rule when; the files stay the source of truth
        from .db import get_repository
        get_repository().add_rule_version(project or "", text)
    except Exception:
        pass
    return path
