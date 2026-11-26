# Coverage Hole Disposition & Waiver Governance Matrix

## 1. Coverage Metrics Summary
- **Report Source**: `<sim_out/urgReport/dashboard.txt>` (`@<p4_cl>`)
- **Code Coverage**: Line `<X%>` | Cond `<X%>` | Toggle `<X%>` | FSM `<X%>` | Branch `<X%>`
- **Functional / SVA Coverage**: Groups `<X%>` | Assertions Non-Vacuous `<X%>`

## 2. Hole Disposition Matrix
| Hole ID | Metric Type | Hierarchy / Covergroup Bin | RTL / TB Provenance | Root Cause Category | Closure Action | Waiver Allowed? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `COV-001` | Cross | `cg_axi.cp_burst x cp_wrap_err` | `axi_cov.sv:L74` | `STIMULUS_CONSTRAINT_GAP` | Add `axi_wrap_err_vseq` | **No** |
| `COV-002` | Condition | `u_dut.u_ecc (syndrome == 7'h7f)` | `ecc_dec.sv:L112` | `STIMULUS_CONSTRAINT_GAP` | Enable multi-bit error injection knob | **No** |
| `COV-003` | Branch | `u_dut.u_fsm default:` | `ctrl_fsm.sv:L210` | `RTL_DEAD_OR_UNREACHABLE_CODE` | Run Formal UNR; prepare `.el` waiver | **Only w/ UNR + RTL Sign-off** |

## 3. Mandatory Waiver Record Fields (When Applicable)
- **Target Instance / Expression**: `<hier_path + line + checksum>`
- **RTL Revision**: `@<p4_changelist>`
- **Justification & Proof**: `<Structural tie-off or Formal UNR proof log>`
- **Required Approvers**: `<RTL_Owner_ID>` + `<DV_Lead_ID>`
