# DV Testplan & Traceability Matrix Template

## 1. Scope & Reference Revisions
- **DUT**: `<DUT_NAME>`
- **Spec Source & Revision**: `<relative_spec_path>` (`@<p4_cl_or_rev>`)
- **RTL Top & Revision**: `<relative_rtl_top_path>` (`@<p4_cl_or_rev>`)

## 2. Open Specification Gaps (`[SPEC_GAP]`)
| Gap ID | Spec Section / RTL Location | Ambiguity / Missing Definition | Proposed Default Assumption | Owner |
| :--- | :--- | :--- | :--- | :--- |
| `GAP-001` | `<spec.md:L120>` | `<Behavior on simultaneous FIFO full and soft_rst>` | `<Drop inflight transfer and clear pointers>` | `<Design>` |

## 3. Feature-to-Verification Traceability Matrix
| TP ID | Req ID (ADO/JIRA) | Category | Feature & Scenario Description | Spec Provenance | Verification Method | Stimulus / Sequence | Checker / SVA | Functional Coverage Target | Milestone |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `TP-DP-001` | `<REQ-101>` | Datapath | Single & burst read/write across boundary | `<spec:Sec3.1>` | `UVM_CRV` | `<burst_rw_vseq>` | `<dp_scoreboard>` | `cg_burst.cp_len x cp_size` | `V1` |
| `TP-ERR-001` | `<REQ-142>` | Error | Bus `SLVERR` propagation & IRQ assertion | `<spec:Sec5.4>` | `UVM_CRV` + `SVA` | `<err_inj_vseq>` | `AST_ERR_IRQ` + `sb` | `cg_err.cp_resp_type` | `V2` |
| `TP-RST-001` | `<REQ-160>` | Reset | Warm reset mid-burst & clean recovery | `<spec:Sec2.3>` | `UVM_CRV` | `<warm_rst_vseq>` | `<rst_mon>` + `RAL` | `cg_rst.cp_inflight_phase` | `V2` |
