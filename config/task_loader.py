"""Load download task records from a separate task.yml file.

Base credentials / defaults stay in config.yml. When ``--task`` is provided,
each record supplies one or more links plus optional per-task overrides
(mode / number / path / …). Existing config.yml format is unchanged.

Direct single-work modes (do not go through user post/like/mix strategies):
  - ``mode: video`` / ``gallery`` / ``note`` with ``link:``
  - shorthand ``video: <url|list>`` / ``gallery:`` / ``note:``
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from config.config_loader import ConfigLoader
from utils.logger import setup_logger

logger = setup_logger("TaskLoader")

# Keys that describe the task itself rather than ConfigLoader fields.
_META_KEYS = {
    "name",
    "id",
    "link",
    "links",
    "url",
    "urls",
    "video",
    "gallery",
    "note",
}

# Task-level direct download modes (single aweme URLs). Not user-page strategies.
_DIRECT_MODES = frozenset({"video", "gallery", "note"})
_DIRECT_LINK_KEYS = ("video", "gallery", "note")


def _normalize_links(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        links: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                links.append(item.strip())
        return links
    return []


def _normalize_mode_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip().lower()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        modes: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                modes.append(item.strip().lower())
        return modes
    return []


def _extract_links(record: Dict[str, Any]) -> List[str]:
    for key in ("link", "links", "url", "urls") + _DIRECT_LINK_KEYS:
        if key in record:
            return _normalize_links(record.get(key))
    return []


def _direct_mode_from_record(record: Dict[str, Any]) -> Optional[str]:
    """Return direct mode name if this record is a single-work download task."""
    for key in _DIRECT_LINK_KEYS:
        if key in record and _normalize_links(record.get(key)):
            return key

    modes = _normalize_mode_list(record.get("mode"))
    direct = [m for m in modes if m in _DIRECT_MODES]
    other = [m for m in modes if m not in _DIRECT_MODES]
    # Pure direct mode: mode: video / mode: [video]
    if direct and not other:
        return direct[0]
    return None


def _extract_overrides(record: Dict[str, Any], *, direct_mode: Optional[str]) -> Dict[str, Any]:
    overrides = {key: value for key, value in record.items() if key not in _META_KEYS}
    if "mode" not in overrides:
        return overrides

    modes = _normalize_mode_list(overrides.get("mode"))
    # Strip direct modes so they never land in ConfigLoader.mode (user strategies).
    user_modes = [m for m in modes if m not in _DIRECT_MODES]
    if direct_mode or not user_modes:
        overrides = dict(overrides)
        overrides.pop("mode", None)
    elif user_modes != modes:
        overrides = dict(overrides)
        overrides["mode"] = user_modes
    return overrides


def load_task_records(task_path: str) -> List[Dict[str, Any]]:
    """Parse task.yml into a list of raw task dicts.

    Accepted shapes:
      - ``{tasks: [ {...}, {...} ]}``
      - top-level list ``[ {...}, {...} ]``
      - single mapping with ``link`` / ``video`` / … (treated as one task)

    Direct single-video examples::

        - mode: video
          link: https://www.douyin.com/video/123

        - video: https://www.douyin.com/video/123

        - video:
            - https://www.douyin.com/video/123
            - https://www.douyin.com/note/456
    """
    path = Path(task_path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []

    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("tasks"), list):
            records = raw["tasks"]
        elif _extract_links(raw) or any(k in raw for k in _META_KEYS):
            records = [raw]
        else:
            raise ValueError(
                f"Task file {path} must be a list, or a mapping with "
                f"`tasks:` / `link` / `video` fields"
            )
    else:
        raise ValueError(f"Task file {path} has unsupported YAML root type")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(records, 1):
        if not isinstance(item, dict):
            logger.warning("Skip non-mapping task record #%s in %s", index, path)
            continue
        links = _extract_links(item)
        if not links:
            logger.warning("Skip task record #%s in %s: missing link/video", index, path)
            continue
        direct_mode = _direct_mode_from_record(item)
        name = str(item.get("name") or item.get("id") or f"task-{index}").strip()
        if direct_mode and name == f"task-{index}":
            name = f"{direct_mode}-{index}"
        normalized.append(
            {
                "name": name,
                "index": index,
                "link": links,
                "direct_mode": direct_mode,
                "overrides": _extract_overrides(item, direct_mode=direct_mode),
            }
        )
    return normalized


def build_task_config(base: ConfigLoader, record: Dict[str, Any]) -> ConfigLoader:
    """Clone base config, apply per-task overrides, set record links."""
    child = ConfigLoader(None)
    child.config_path = base.config_path
    child.config = deepcopy(base.config)

    overrides = record.get("overrides") or {}
    if overrides:
        child.config = child._merge_config(child.config, overrides)

    child.config["link"] = list(record.get("link") or [])
    return child


def iter_task_jobs(
    base: ConfigLoader, task_path: str
) -> List[Tuple[str, ConfigLoader, List[str]]]:
    """Return ``(task_name, task_config, urls)`` for each valid record."""
    jobs: List[Tuple[str, ConfigLoader, List[str]]] = []
    for record in load_task_records(task_path):
        task_config = build_task_config(base, record)
        urls = list(task_config.get_links())
        if not urls:
            continue
        jobs.append((str(record["name"]), task_config, urls))
    return jobs
