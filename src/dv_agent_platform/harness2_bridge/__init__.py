"""Bridge integrating Agentic DV Framework with Google Research Harness^2,
extended with GUARD regret-guided evolution guards and WikiSkill persistent ledger.
"""

from .adapter import (
    CORE_DV_SKILLS,
    DVHarness2Adapter,
    DVOfflineProposer,
    GeminiCliSubstrate,
    ensure_harness2_importable,
    validate_dv_harness_safety,
)
from .guard_wiki import (
    WikiPattern,
    WikiSkillLedger,
    compute_paired_regret,
    evaluate_soft_hurt_gate,
    evaluate_two_layer_checklist,
    rank_candidate_pool,
    route_task_signature,
    validate_guard_scoping,
)

__all__ = [
    "CORE_DV_SKILLS",
    "DVHarness2Adapter",
    "DVOfflineProposer",
    "GeminiCliSubstrate",
    "ensure_harness2_importable",
    "validate_dv_harness_safety",
    "WikiPattern",
    "WikiSkillLedger",
    "compute_paired_regret",
    "evaluate_soft_hurt_gate",
    "evaluate_two_layer_checklist",
    "rank_candidate_pool",
    "route_task_signature",
    "validate_guard_scoping",
]
