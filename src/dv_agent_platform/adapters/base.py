"""Base class for all DV tool/infrastructure adapters.

Enforces:
1. Mock-by-default operation; ``adapter_mode='real'`` raises ``AdapterNotValidated`` unless
   explicitly marked validated for that adapter in ``config.adapters_validated``.
2. Explicit human approval via ``ApprovalGate`` for simulation, waveform batch, source/P4
   workspace preparation, and ticket writes.
3. Confidentiality check before outbound data transfer.
4. Structured audit logging of every adapter call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import FrameworkConfig
from ..errors import AdapterNotValidated, ConfidentialityError
from ..security.approval import ApprovalGate, ApprovalRequest
from ..telemetry.logger import StructuredLogger


class BaseAdapter:
    name: str = "base"

    def __init__(
        self,
        config: FrameworkConfig,
        approval_gate: Optional[ApprovalGate] = None,
        logger: Optional[StructuredLogger] = None,
    ):
        self.config = config
        self.approval_gate = approval_gate or ApprovalGate()
        self.logger = logger or StructuredLogger(config.logs_dir / "adapters.jsonl", component=f"adapter.{self.name}")

    @property
    def is_mock(self) -> bool:
        if self.config.adapter_mode == "mock":
            return True
        if not self.config.adapters_validated.get(self.name, False):
            raise AdapterNotValidated(
                f"Adapter '{self.name}' requested in 'real' mode, but adapters_validated['{self.name}'] is not True. "
                "Keep DV_ADAPTER_MODE=mock until validated against the actual Linux environment."
            )
        return False

    def _require_approval(self, category: str, summary: str, details: Dict[str, Any],
                          approval_id: Optional[str] = None):
        req = ApprovalRequest(category=category, summary=summary, details=details)
        return self.approval_gate.require(req, approval_id=approval_id)

    def _check_outbound_confidentiality(self, external: bool, target: str) -> None:
        if external and not self.config.allow_external_upload:
            raise ConfidentialityError(
                f"Confidentiality policy blocks outbound payload to '{target}' "
                "(DV_ALLOW_EXTERNAL_UPLOAD is False)."
            )

    def _resolve_project_file(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        target = (p if p.is_absolute() else self.config.project_root / p).resolve()
        target.relative_to(self.config.project_root)
        return target

    def _load_mock_infra(self) -> Dict[str, Any]:
        candidates = []
        if self.config.fixtures_dir:
            candidates.append(self.config.fixtures_dir / "mock_infra" / "infra_data.json")
        candidates.append(self.config.project_root / "fixtures" / "mock_infra" / "infra_data.json")
        for c in candidates:
            if c.is_file():
                return json.loads(c.read_text(encoding="utf-8"))
        return {}
