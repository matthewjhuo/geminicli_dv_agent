"""Custom offline mutation demonstration using a built-in Python FIFO model.

Recognizes a narrow set of SVA text patterns after comment removal. Does not compile
or execute SVA, mutate an input RTL design, prove non-vacuity, or grant signoff.
The five-stage presentation is an evaluation aid, not an external product integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

MUTANT_CATALOG: Dict[str, str] = {
    "M0_SOFT_CLR_STALL_DROP": (
        "Buggy soft_clr resets wr_ptr_q/rd_ptr_q unconditionally during output stall (m_valid && !m_ready), "
        "matching fixtures/rtl/axi_fifo.sv."
    ),
    "M1_STALL_DATA_CORRUPT": (
        "Keeps m_valid high on output stall, but corrupts m_data (XOR 0x1) while stalled — "
        "catches weak SVA oracles that omit $stable(m_data)."
    ),
    "M2_OVERFLOW_WR_ON_FULL": (
        "Ignores !full guard on write and overwrites the queued tail entry during full condition "
        "(head m_data stays stable under stall, so only an overflow assertion or scoreboard kills it)."
    ),
    "M3_RESET_STUCK_VALID": (
        "Fails to clear pointers on !rst_n, leaving m_valid asserted during reset."
    ),
    "M4_STALL_SPURIOUS_POP": (
        "Advances rd_ptr_q on m_valid even when !m_ready, dropping an unaccepted beat under backpressure."
    ),
}


@dataclass(frozen=True)
class CycleSample:
    cycle: int
    rst_n: int
    soft_clr: int
    s_valid: int
    s_ready: int
    s_data: int
    m_valid: int
    m_ready: int
    m_data: int
    full: int
    empty: int
    count: int


def _default_stimulus() -> List[Dict[str, int]]:
    """Deterministic stimulus exercising reset, push/pop, stall, soft_clr-under-stall, and full overflow."""
    vec: List[Dict[str, int]] = []
    # Cycles 0..1: reset asserted
    vec.append({"rst_n": 0, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 0})
    vec.append({"rst_n": 0, "soft_clr": 0, "s_valid": 1, "s_data": 0xAAAA0001, "m_ready": 0})
    # Cycles 2..5: fill 4 beats with output stalled (m_ready=0)
    for i in range(4):
        vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 1, "s_data": 0xDEAD0000 + i, "m_ready": 0})
    # Cycle 6: assert soft_clr while stalled (m_valid=1, m_ready=0)
    vec.append({"rst_n": 1, "soft_clr": 1, "s_valid": 0, "s_data": 0, "m_ready": 0})
    # Cycle 7..8: keep stalled, then release m_ready to hit COV_HOLD_ON_STALL
    vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 0})
    vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 1})
    # Cycles 9..18: drive FIFO to full (DEPTH=8) and attempt extra write while full
    for i in range(10):
        vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 1, "s_data": 0xCAFE0000 + i, "m_ready": 0})
    # Cycles 19..27: drain all queued beats cleanly (exposing tail corruption in M2)
    for _ in range(9):
        vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 1})
    # Cycle 28: push 1 beat then assert async reset on cycle 29 to test M3_RESET_STUCK_VALID
    vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 1, "s_data": 0xBEEF0001, "m_ready": 0})
    vec.append({"rst_n": 0, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 0})
    vec.append({"rst_n": 1, "soft_clr": 0, "s_valid": 0, "s_data": 0, "m_ready": 1})
    return vec


def simulate_axi_fifo(
    mutant: Optional[str] = None,
    *,
    depth: int = 8,
    stimulus: Optional[Sequence[Dict[str, int]]] = None,
) -> List[CycleSample]:
    """Deterministic cycle-accurate model of ``axi_fifo`` (golden fixed RTL or named mutant)."""
    if mutant is not None and mutant not in MUTANT_CATALOG:
        raise ValueError(f"Unknown RTL mutant {mutant!r}; expected one of {sorted(MUTANT_CATALOG)}")

    stim = list(stimulus) if stimulus is not None else _default_stimulus()
    mem = [0] * depth
    wr_ptr = 0
    rd_ptr = 0
    ptr_mod = depth * 2
    corrupt_mask = 0

    trace: List[CycleSample] = []
    for cyc, inp in enumerate(stim):
        rst_n = int(inp["rst_n"])
        soft_clr = int(inp["soft_clr"])
        s_valid = int(inp["s_valid"])
        s_data = int(inp["s_data"]) & 0xFFFFFFFF
        m_ready = int(inp["m_ready"])

        # Asynchronous reset takes effect immediately on negedge rst_n (matches axi_fifo.sv:32)
        if not rst_n and mutant != "M3_RESET_STUCK_VALID":
            wr_ptr = 0
            rd_ptr = 0
            corrupt_mask = 0

        # Combinational outputs before posedge
        cnt = (wr_ptr - rd_ptr) % ptr_mod
        full = 1 if cnt == depth else 0
        empty = 1 if cnt == 0 else 0
        s_ready = 0 if full else 1
        m_valid = 0 if empty else 1
        m_data = (mem[rd_ptr % depth] ^ corrupt_mask) & 0xFFFFFFFF

        trace.append(
            CycleSample(
                cycle=cyc,
                rst_n=rst_n,
                soft_clr=soft_clr,
                s_valid=s_valid,
                s_ready=s_ready,
                s_data=s_data,
                m_valid=m_valid,
                m_ready=m_ready,
                m_data=m_data,
                full=full,
                empty=empty,
                count=cnt,
            )
        )

        # Sequential state update on posedge clk
        corrupt_mask = 0
        if not rst_n:
            if mutant != "M3_RESET_STUCK_VALID":
                wr_ptr = 0
                rd_ptr = 0
        else:
            stalled = bool(m_valid and not m_ready)
            if soft_clr:
                if mutant == "M0_SOFT_CLR_STALL_DROP":
                    wr_ptr = 0
                    rd_ptr = 0
                else:
                    # Golden spec REQ-FIFO-001: defer/ignore soft_clr while stalled
                    if not stalled:
                        wr_ptr = 0
                        rd_ptr = 0
            else:
                if mutant == "M1_STALL_DATA_CORRUPT" and stalled:
                    corrupt_mask = 0x1
                if s_valid and s_ready:
                    mem[wr_ptr % depth] = s_data
                    wr_ptr = (wr_ptr + 1) % ptr_mod
                elif mutant == "M2_OVERFLOW_WR_ON_FULL" and s_valid and full:
                    # Bug: clobbers newest queued tail entry without disturbing stalled head m_data
                    mem[(wr_ptr - 1) % depth] = s_data
                do_read = bool(m_valid and (True if mutant == "M4_STALL_SPURIOUS_POP" else m_ready))
                if do_read:
                    rd_ptr = (rd_ptr + 1) % ptr_mod

    return trace


@dataclass
class OracleCheckResult:
    name: str
    antecedent_hits: int
    failures: List[int]

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0

    @property
    def vacuous(self) -> bool:
        return self.antecedent_hits == 0


def parse_sva_structure(sva_text: str) -> Dict[str, Any]:
    """Stage 1: lightweight structural inspection of SVA assertions & cover properties."""
    sva_text = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", " ", sva_text)
    asserts = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*assert\s+property\s*\(([\s\S]*?)\)\s*(?:else|;)", sva_text)
    covers = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*cover\s+property\s*\(([\s\S]*?)\)\s*;", sva_text)
    has_module = bool(re.search(r"\bmodule\b", sva_text)) and bool(re.search(r"\bendmodule\b", sva_text))

    parsed_asserts: List[Dict[str, Any]] = []
    for name, body in asserts:
        norm = " ".join(body.split())
        parsed_asserts.append(
            {
                "name": name,
                "body": norm,
                "checks_m_valid_hold": bool(
                    re.search(r"m_valid\s*&&\s*!m_ready", norm) and "|=>" in norm and "m_valid" in norm.split("|=>", 1)[1]
                ),
                "checks_m_data_stable": bool(
                    re.search(r"\$stable\s*\(\s*m_data\s*\)", norm)
                ),
                "checks_reset_valid_low": bool(
                    re.search(r"!rst_n.*(?:!m_valid|m_valid\s*==\s*1?'?b?0)", norm)
                ),
                "checks_no_overflow_corrupt": bool(
                    re.search(r"full|s_ready|overflow|wr_ptr", norm, re.I)
                ),
                "tautology_always_true": bool(re.search(r"\|\s*=>\s*\(?\s*1'b1\s*\)?|\b1'b1\b\s*$", norm)),
            }
        )

    parsed_covers = [{"name": name, "body": " ".join(body.split())} for name, body in covers]
    syntax_ok = bool(has_module and len(parsed_asserts) > 0)
    return {
        "syntax_ok": syntax_ok,
        "has_module_bounds": has_module,
        "assertion_count": len(parsed_asserts),
        "cover_count": len(parsed_covers),
        "assertions": parsed_asserts,
        "covers": parsed_covers,
    }


def _evaluate_oracles_on_trace(
    trace: Sequence[CycleSample],
    structure: Dict[str, Any],
    *,
    include_scoreboard_integrity: bool = False,
) -> Tuple_OracleEval:
    results: List[OracleCheckResult] = []

    for a in structure["assertions"]:
        name = a["name"]
        hits = 0
        fails: List[int] = []
        if a["tautology_always_true"]:
            # Vacuous/trivial oracle: passes every mutant!
            results.append(OracleCheckResult(name=name, antecedent_hits=0, failures=[]))
            continue

        if a["checks_m_valid_hold"] or a["checks_m_data_stable"]:
            for i in range(len(trace) - 1):
                cur, nxt = trace[i], trace[i + 1]
                if cur.rst_n and nxt.rst_n and cur.m_valid and not cur.m_ready:
                    hits += 1
                    ok_valid = (nxt.m_valid == 1) if a["checks_m_valid_hold"] else True
                    ok_data = (nxt.m_data == cur.m_data) if a["checks_m_data_stable"] else True
                    if not (ok_valid and ok_data):
                        fails.append(nxt.cycle)
        elif a["checks_reset_valid_low"]:
            for cur in trace:
                if not cur.rst_n:
                    hits += 1
                    if cur.m_valid != 0:
                        fails.append(cur.cycle)
        elif a["checks_no_overflow_corrupt"]:
            for i in range(len(trace) - 1):
                cur, nxt = trace[i], trace[i + 1]
                if cur.rst_n and nxt.rst_n and cur.full and cur.s_valid and not cur.m_ready:
                    hits += 1
                    if nxt.m_data != cur.m_data:
                        fails.append(nxt.cycle)
        results.append(OracleCheckResult(name=name, antecedent_hits=hits, failures=fails))

    if include_scoreboard_integrity:
        # Reference scoreboard oracle: tracks expected FIFO queue on accepted handshakes
        exp_q: List[int] = []
        sb_hits = 0
        sb_fails: List[int] = []
        for s in trace:
            if not s.rst_n:
                exp_q.clear()
                if s.m_valid and s.cycle > 0:
                    sb_fails.append(s.cycle)
                continue
            if s.m_valid and exp_q:
                sb_hits += 1
                if s.m_data != exp_q[0]:
                    sb_fails.append(s.cycle)
            stalled = bool(s.m_valid and not s.m_ready)
            if s.soft_clr and not stalled:
                exp_q.clear()
            else:
                if s.s_valid and s.s_ready:
                    exp_q.append(s.s_data)
                if s.m_valid and s.m_ready and exp_q:
                    exp_q.pop(0)
        results.append(OracleCheckResult(name="SB_AXI_FIFO_INTEGRITY", antecedent_hits=sb_hits, failures=sb_fails))

    # Cover property evaluation (non-vacuity check on golden trace)
    cov_hits = 0
    for i in range(len(trace) - 1):
        cur, nxt = trace[i], trace[i + 1]
        if cur.rst_n and nxt.rst_n and cur.m_valid and not cur.m_ready and nxt.m_valid and nxt.m_ready:
            cov_hits += 1

    return results, cov_hits


Tuple_OracleEval = tuple[List[OracleCheckResult], int]


def run_mutation_oracle_ladder(
    sva_text: str,
    *,
    mutants: Optional[Sequence[str]] = None,
    include_scoreboard_integrity: bool = False,
    min_kill_rate: float = 0.6,
) -> Dict[str, Any]:
    """Run all 5 Evaluation Ladder stages on an SVA/oracle artifact against ``axi_fifo`` mutants."""
    if isinstance(min_kill_rate, bool) or not isinstance(min_kill_rate, (int, float)) or not 0 <= min_kill_rate <= 1:
        raise ValueError("min_kill_rate must be finite and between 0 and 1")
    if mutants is not None and not mutants:
        raise ValueError("At least one mutant is required")
    # Stage 1: Pattern inspection only; this is not an SV syntax check.
    structure = parse_sva_structure(sva_text)
    stage1_ok = bool(structure["syntax_ok"])

    # Stage 2: Golden RTL reference simulation
    golden_trace = simulate_axi_fifo(mutant=None)
    golden_results, cov_hits = _evaluate_oracles_on_trace(
        golden_trace, structure, include_scoreboard_integrity=include_scoreboard_integrity
    )
    golden_clean = all(r.passed for r in golden_results) and len(golden_results) > 0

    # Stage 3: Coverage & non-vacuity
    non_vacuous_oracles = [r.name for r in golden_results if not r.vacuous]
    vacuous_oracles = [r.name for r in golden_results if r.vacuous]
    non_vacuous = len(non_vacuous_oracles) > 0 and (len(vacuous_oracles) == 0)
    cover_hit = cov_hits > 0 if structure["cover_count"] > 0 else True
    stage3_ok = bool(non_vacuous and cover_hit)

    # Stage 4: Oracle strength (RTL Mutation / Fault Injection)
    target_mutants = list(mutants) if mutants is not None else list(MUTANT_CATALOG.keys())
    mutant_reports: List[Dict[str, Any]] = []
    killed_count = 0
    for m_id in target_mutants:
        m_trace = simulate_axi_fifo(mutant=m_id)
        m_results, _ = _evaluate_oracles_on_trace(
            m_trace, structure, include_scoreboard_integrity=include_scoreboard_integrity
        )
        caught_by = [r.name for r in m_results if not r.passed]
        killed = len(caught_by) > 0
        if killed:
            killed_count += 1
        mutant_reports.append(
            {
                "mutant_id": m_id,
                "description": MUTANT_CATALOG[m_id],
                "killed": killed,
                "caught_by": caught_by,
            }
        )

    kill_rate = round(killed_count / float(max(1, len(target_mutants))), 4)
    stage4_ok = bool(golden_clean and kill_rate >= float(min_kill_rate))

    # Stage 5: Signoff Readiness
    synthetic_ready = bool(stage1_ok and golden_clean and stage3_ok and stage4_ok)
    signoff_ready = False  # A Python pattern-based demonstration cannot grant EDA signoff.
    return {
        "ladder_version": "dv-eval-ladder/v2",
        "execution_mode": "synthetic_python",
        "synthetic": True,
        "synthetic_ready": synthetic_ready,
        "limitations": "Built-in FIFO Python model and recognized SVA patterns only; no SV compiler, property evaluation, RTL simulation, formal proof or signoff. Cover hits are synthetic scenario hits, not execution of the supplied cover expression.",
        "stages": {
            "1_syntax_and_structure": {
                "passed": stage1_ok,
                "validation_kind": "regex_structure_only",
                "assertion_count": structure["assertion_count"],
                "cover_count": structure["cover_count"],
            },
            "2_simulation_golden_clean": {
                "passed": golden_clean,
                "golden_cycles": len(golden_trace),
                "false_positives": [
                    {"oracle": r.name, "cycles": r.failures} for r in golden_results if not r.passed
                ],
            },
            "3_coverage_and_non_vacuity": {
                "passed": stage3_ok,
                "non_vacuous_oracles": non_vacuous_oracles,
                "vacuous_oracles": vacuous_oracles,
                "cover_hits": cov_hits,
            },
            "4_oracle_mutation_strength": {
                "passed": stage4_ok,
                "killed_mutants": killed_count,
                "total_mutants": len(target_mutants),
                "mutation_kill_rate": kill_rate,
                "min_kill_rate": float(min_kill_rate),
                "mutants": mutant_reports,
            },
            "5_signoff_readiness": {
                "passed": signoff_ready,
                "verdict": "SYNTHETIC_DEMO_PASS" if synthetic_ready else "SYNTHETIC_ORACLE_GAP",
            },
        },
        "mutation_kill_rate": kill_rate,
        "signoff_ready": signoff_ready,
    }


def run_mutation_oracle_file(
    sva_path: Path | str,
    *,
    mutants: Optional[Sequence[str]] = None,
    include_scoreboard_integrity: bool = False,
    min_kill_rate: float = 0.6,
) -> Dict[str, Any]:
    path = Path(sva_path)
    text = path.read_text(encoding="utf-8")
    report = run_mutation_oracle_ladder(
        text,
        mutants=mutants,
        include_scoreboard_integrity=include_scoreboard_integrity,
        min_kill_rate=min_kill_rate,
    )
    report["sva_path"] = path.as_posix()
    return report
