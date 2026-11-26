"""Bridge integrating Agentic DV Framework with Google Research Harness^2.

Provides:
- ``GeminiCliSubstrate``: exposes ``GEMINI.md`` + ``.gemini/skills/*/SKILL.md`` + ``scripts/*.py``
  as a Harness^2 editable surface while keeping security policies/hooks immutable.
- ``DVHarness2Adapter``: runs Harness^2 Propose-Probe-Compose against the DV golden dataset
  and enforces mandatory DV security & portability invariants in ``validate_tree()``.
- ``DVOfflineProposer``: deterministic local proposer for credential-free verification.
"""

from .adapter import (
    CORE_DV_SKILLS,
    DVHarness2Adapter,
    DVOfflineProposer,
    GeminiCliSubstrate,
    ensure_harness2_importable,
    validate_dv_harness_safety,
)

__all__ = [
    "CORE_DV_SKILLS",
    "DVHarness2Adapter",
    "DVOfflineProposer",
    "GeminiCliSubstrate",
    "ensure_harness2_importable",
    "validate_dv_harness_safety",
]
