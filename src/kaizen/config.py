"""Configuration layer for the AI Kaizen Framework.

Business users edit a single YAML file — rules, prompts, SQDIP targets, and
human standard work — without touching code. Every save creates a new version
and archives the previous one, so experiments are always reversible.
"""

from __future__ import annotations

import copy
import datetime as _dt
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "process": {
        "name": "unnamed-process",
        "description": "",
    },
    # The process owner: the only role that may approve a change to standard
    # work. Agents can propose and pilot changes; only the owner standardizes
    # them. On Microsoft Planner this maps to task assignment + completion.
    "process_owner": "",
    "sandbox": False,
    "jidoka": {
        # Stop the line when an exception at or above this severity occurs.
        "stop_on_severity": "high",
    },
    # Individual defects are always recorded to the run log and counted; they
    # are NOT auto-carded. Cards come from two places: a Jidoka line-stop
    # (immediate andon), and a missed target at the daily review (see targets).
    "tickets": {
        "on_stop": True,               # a line-stop raises an immediate card
        "cards_for_sqdip_misses": False,  # also card when an SQDIP metric misses target
    },
    # Measure targets. A missed target raises ONE card at the daily review with
    # a problem statement framed as the gap to target. See src/kaizen/targets.py.
    "targets": [],
    "rules": [],
    "prompts": {
        "daily_reflection": (
            "You are a Kaizen coach facilitating a daily improvement kata for the "
            "process '{process_name}'. Review today's SQDIP metrics and exceptions, "
            "then write a short daily Kaizen summary for the joint human-AI standup.\n\n"
            "SQDIP snapshot:\n{sqdip}\n\nExceptions:\n{exceptions}\n\n"
            "Structure your answer as:\n"
            "1. SQDIP analysis (call out anything off-target)\n"
            "2. Patterns worth a 5 Whys root cause analysis\n"
            "3. Two or three small, testable improvement suggestions — for the "
            "automated process AND for human standard work\n"
            "4. One question for the team to discuss today"
        ),
    },
    "standard_work": {
        "daily_kata": [
            "Review the daily Kaizen summary together (AI prepares, humans interpret).",
            "Pick at most one exception pattern for 5 Whys root cause analysis.",
            "Agree on one small countermeasure and who owns it.",
            "Update rules/prompts/standard work in the config if the standard changed.",
        ],
    },
    "sqdip_targets": {
        "safety": {"description": "Guardrail breaches / policy violations", "target": 0},
        "quality": {"description": "Exception rate (%)", "target": 2.0},
        "delivery": {"description": "Runs completed on time (%)", "target": 98.0},
        "inventory": {"description": "Open Kanban tickets", "target": 10},
        "productivity": {"description": "Runs completed per day", "target": None},
    },
    "kanban": {
        "provider": "local",
        "board_path": "kaizen_board.json",
        "buckets": {
            "problems": "Problems",
            "kaizen": "Daily Kaizen",
            "ideas": "Improvement Ideas",
            "experiments": "Experiments",
        },
    },
}


class KaizenConfig:
    """Versioned, YAML-backed configuration.

    >>> cfg = KaizenConfig.load("config/kaizen_config.yaml")
    >>> cfg.rules
    [...]
    >>> cfg.data["jidoka"]["stop_on_severity"] = "medium"
    >>> cfg.save()  # bumps version, archives the previous file
    """

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None):
        self.data = data
        self.path = Path(path) if path else None

    # -- loading / saving -------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "KaizenConfig":
        p = Path(path)
        with open(p, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        data = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), raw)
        return cls(data, p)

    @classmethod
    def default(cls) -> "KaizenConfig":
        return cls(copy.deepcopy(DEFAULT_CONFIG))

    def save(self, path: Optional[str] = None) -> None:
        """Persist the config, bumping the version and archiving the old file."""
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("No path given and config was not loaded from a file.")
        if target.exists():
            history = target.parent / "config_history"
            history.mkdir(exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
            old_version = self.data.get("version", 1)
            shutil.copy2(target, history / f"{target.stem}.v{old_version}.{stamp}{target.suffix}")
            self.data["version"] = int(old_version) + 1
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.data, fh, sort_keys=False, allow_unicode=True)
        self.path = target

    # -- convenience accessors --------------------------------------------

    @property
    def process_name(self) -> str:
        return self.data.get("process", {}).get("name", "unnamed-process")

    @property
    def sandbox(self) -> bool:
        return bool(self.data.get("sandbox", False))

    @property
    def process_owner(self) -> str:
        return self.data.get("process_owner", "")

    @property
    def stop_on_severity(self) -> str:
        return self.data.get("jidoka", {}).get("stop_on_severity", "high")

    @property
    def tickets(self) -> Dict[str, Any]:
        return self.data.get("tickets", {})

    @property
    def targets(self) -> List[Dict[str, Any]]:
        return self.data.get("targets", [])

    @property
    def rules(self) -> List[Dict[str, Any]]:
        return self.data.get("rules", [])

    @property
    def prompts(self) -> Dict[str, str]:
        return self.data.get("prompts", {})

    @property
    def standard_work(self) -> Dict[str, Any]:
        return self.data.get("standard_work", {})

    @property
    def sqdip_targets(self) -> Dict[str, Any]:
        return self.data.get("sqdip_targets", {})

    @property
    def kanban(self) -> Dict[str, Any]:
        return self.data.get("kanban", {})


def load_env(start: Optional[str] = None) -> Dict[str, str]:
    """Load a ``.env`` file into ``os.environ`` (existing variables win).

    Walks upward from ``start`` (default: current directory) to the filesystem
    root and loads the first ``.env`` found. Zero dependencies — supports
    simple ``KEY=value`` lines, comments, and optional quotes. Returns the
    variables that were newly set.

    Keep secrets like ``ANTHROPIC_API_KEY`` in a git-ignored ``.env`` rather
    than a shell profile: scoped to the project, and never committed.
    """
    import os

    directory = Path(start or ".").resolve()
    loaded: Dict[str, str] = {}
    for candidate in [directory, *directory.parents]:
        env_file = candidate / ".env"
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
        break
    return loaded


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
