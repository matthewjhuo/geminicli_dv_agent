"""Configuration loading: defaults < dv_project.json < environment < explicit overrides.

No paths are hardcoded; everything is resolved relative to the project root,
which itself comes from an argument, ``$DV_PROJECT_ROOT`` or the CWD.
Credentials are never accepted in configuration files.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import ConfigError

HARD_MAX_CONCURRENCY = 4
CONFIG_FILENAME = "dv_project.json"
_SECRET_KEY_RE = re.compile(
    r"(?i)^(.*[_-])?(password|passwd|p4passwd|secret|api[_-]?key|credential|"
    r"(?:auth|access|refresh|oauth|bearer)[_-]?token|private[_-]?key)$|^token$"
)
_SECRET_VALUE_RES = (
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"\bsk-[0-9A-Za-z]{20,}\b"),
    re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}\b"),
    re.compile(r"\bxox[abpr]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|password|passwd|p4passwd|secret|token)\s*[:=]\s*['\"](?!env:)[^'\"<$\s]{8,}['\"]"
    ),
)
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


_ENV_MAP = {
    "DV_PROJECT_ID": ("project_id", str),
    "DV_AGENT_STATE_DIR": ("state_dir", str),
    "DV_AGENT_MAX_CONCURRENCY": ("max_concurrency", int),
    "DV_AGENT_TOKEN_BUDGET": ("token_budget", int),
    "DV_AGENT_TASK_TIMEOUT_S": ("task_timeout_s", int),
    "DV_AGENT_MAX_RETRIES": ("max_retries", int),
    "DV_AGENT_MAX_REVIEW_ROUNDS": ("max_review_rounds", int),
    "DV_AGENT_DRY_RUN": ("dry_run", _to_bool),
    "DV_ADAPTER_MODE": ("adapter_mode", str),
    "DV_ALLOW_EXTERNAL_UPLOAD": ("allow_external_upload", _to_bool),
    "DV_FIXTURES_DIR": ("fixtures_dir", str),
}


def _reject_secrets(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(val, str) and val and not (val.startswith("$") or val.startswith("env:")):
                if _SECRET_KEY_RE.match(str(key)):
                    raise ConfigError(
                        f"Credential-like key '{here}' found in config. Store only the NAME of an "
                        "environment variable (e.g. 'env:JIRA_API_TOKEN'), never the secret itself."
                    )
                for pat in _SECRET_VALUE_RES:
                    if pat.search(val):
                        raise ConfigError(
                            f"Potential embedded secret value at '{here}' in config. "
                            "Store only environment variable references (e.g. 'env:VAR_NAME')."
                        )
            _reject_secrets(val, here)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _reject_secrets(item, f"{path}[{i}]")
    elif isinstance(obj, str) and obj and not (obj.startswith("$") or obj.startswith("env:")):
        for pat in _SECRET_VALUE_RES:
            if pat.search(obj):
                where = path or "<value>"
                raise ConfigError(
                    f"Potential embedded secret value at '{where}' in config. "
                    "Store only environment variable references (e.g. 'env:VAR_NAME')."
                )


@dataclass
class FrameworkConfig:
    project_root: Path
    project_id: str
    state_dir: Path
    max_concurrency: int = 2
    token_budget: int = 200_000
    task_timeout_s: int = 600
    max_retries: int = 1
    max_review_rounds: int = 2
    dry_run: bool = True
    adapter_mode: str = "mock"
    adapters_validated: Dict[str, bool] = field(default_factory=dict)
    allow_external_upload: bool = False
    fixtures_dir: Optional[Path] = None
    pricing: Dict[str, float] = field(
        default_factory=lambda: {"input_per_1m_usd": 0.0, "output_per_1m_usd": 0.0}
    )
    conflict_margin: float = 0.25
    testbench: Dict[str, Any] = field(default_factory=dict)
    adapters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).resolve()
        self.state_dir = Path(self.state_dir)
        if not _PROJECT_ID_RE.match(self.project_id):
            raise ConfigError(f"Invalid project_id {self.project_id!r}")
        if self.max_concurrency < 1:
            raise ConfigError("max_concurrency must be >= 1")
        self.max_concurrency = min(self.max_concurrency, HARD_MAX_CONCURRENCY)
        if self.token_budget <= 0:
            raise ConfigError("token_budget must be > 0")
        if self.task_timeout_s <= 0:
            raise ConfigError("task_timeout_s must be > 0")
        if not 0 <= self.max_retries <= 3:
            raise ConfigError("max_retries must be within 0..3 (bounded retry)")
        if not 0 <= self.max_review_rounds <= 3:
            raise ConfigError("max_review_rounds must be within 0..3 (bounded review loop)")
        if self.adapter_mode not in {"mock", "real"}:
            raise ConfigError("adapter_mode must be 'mock' or 'real'")
        if not 0.0 <= self.conflict_margin < 1.0:
            raise ConfigError("conflict_margin must be within [0, 1)")
        if not isinstance(self.testbench, dict):
            raise ConfigError("testbench must be a mapping/dict")
        if not isinstance(self.adapters, dict) or any(
            not isinstance(v, dict) for v in self.adapters.values()
        ):
            raise ConfigError("adapters must be a mapping of adapter_name -> dict")
        _reject_secrets(self.testbench, "testbench")
        _reject_secrets(self.adapters, "adapters")

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def memory_db(self) -> Path:
        return self.state_dir / "memory.db"

    def to_public_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            out[f.name] = str(val) if isinstance(val, Path) else val
        return out

    @classmethod
    def load(
        cls,
        project_root: Optional[os.PathLike] = None,
        config_file: Optional[os.PathLike] = None,
        env: Optional[Mapping[str, str]] = None,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "FrameworkConfig":
        env = dict(os.environ if env is None else env)
        root = Path(project_root or env.get("DV_PROJECT_ROOT") or os.getcwd()).resolve()
        data: Dict[str, Any] = {}
        cfg_path = Path(config_file) if config_file else root / CONFIG_FILENAME
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"Invalid JSON in {cfg_path.name}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError("Config root must be a JSON object")
            _reject_secrets(data)
            data.pop("$schema", None)
            data.pop("_comment", None)
        for env_key, (name, conv) in _ENV_MAP.items():
            if env.get(env_key, "") != "":
                try:
                    data[name] = conv(env[env_key])
                except ValueError as exc:
                    raise ConfigError(f"Invalid value for {env_key}") from exc
        data.update(dict(overrides or {}))
        _reject_secrets(data)

        project_id = data.pop("project_id", None) or re.sub(r"[^A-Za-z0-9._-]", "_", root.name)
        state_dir = Path(data.pop("state_dir", None) or (root / ".dv_agent"))
        if not state_dir.is_absolute():
            state_dir = root / state_dir
        fixtures = data.pop("fixtures_dir", None)
        fixtures_path = None
        if fixtures:
            fixtures_path = Path(fixtures)
            if not fixtures_path.is_absolute():
                fixtures_path = root / fixtures_path
        data.pop("project_root", None)
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"Unknown config keys: {sorted(unknown)}")
        return cls(
            project_root=root,
            project_id=project_id,
            state_dir=state_dir,
            fixtures_dir=fixtures_path,
            **data,
        )

    @classmethod
    def from_file(
        cls,
        config_file: os.PathLike | str,
        project_root: Optional[os.PathLike | str] = None,
        env: Optional[Mapping[str, str]] = None,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> "FrameworkConfig":
        cfg_path = Path(config_file)
        if not cfg_path.is_file():
            raise ConfigError(f"Config file not found: {cfg_path}")
        env_map = dict(os.environ if env is None else env)
        root = project_root or env_map.get("DV_PROJECT_ROOT") or cfg_path.resolve().parent
        return cls.load(
            project_root=root,
            config_file=cfg_path,
            env=env_map,
            overrides=overrides,
        )
