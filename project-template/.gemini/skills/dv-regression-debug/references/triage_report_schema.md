# Regression Triage & Root-Cause Analysis Report Schema

## 1. Executive Summary
- **Failure Bucket ID**: `<TRIAGE-YYYYMMDD-NNN>`
- **Impacted Tests / Failing Seeds**: `<count> seeds (min-repro: TEST=<name> SEED=<seed>)`
- **Earliest Failure Timestamp**: `<time_ns>`
- **Primary Classification**: `<RTL_DEFECT | TB_DEFECT | SVA_DEFECT | SPEC_AMBIGUITY | ENV_OR_TOOL_ISSUE | INTENTIONAL_NEGATIVE_TEST>`
- **Epistemic Status**: `<CONFIRMED | HYPOTHESIS | UNKNOWN>`

## 2. Normalized Signature & Evidence Table
| Evidence ID | Source (`path:line` / `@CL`) | Sim Time | Observation | Epistemic Tag |
| :--- | :--- | :--- | :--- | :--- |
| `EV-01` | `<simv.log:L1842>` | `14520 ns` | `AST_HOLD_ON_STALL failed at u_dut.u_tx_fifo` | `[CONFIRMED]` |
| `EV-02` | `<rtl/tx_fifo.sv:L88-L95>` (`@CL48210`) | N/A | `valid_q` cleared when `clr_pulse` asserts without gating `ready_i` | `[CONFIRMED]` |

## 3. Causal Chain (Root Cause -> Symptom)
1. `<Step 1: Stimulus / boundary condition>`
2. `<Step 2: Architectural / RTL or TB state transition defect>`
3. `<Step 3: Observable assertion / scoreboard failure>`

## 4. Next Verification Actions & Proposed Fix
- **Verdi Waveform Check (if `[NEEDS_WAVEFORM]`)**: Signals `<hier.sig_a, hier.sig_b>` over window `[<t0>, <t1>]`.
- **Proposed Fix Preview (`[APPROVAL REQUIRED]`)**: `<concise diff description>`
- **Reproduction Command (`[APPROVAL REQUIRED]`)**: `<make run TEST=... SEED=...>`
- **JIRA / ADO Draft Summary**: `<ready-to-file defect summary>`
