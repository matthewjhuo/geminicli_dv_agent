# UVM 1.2 / IEEE 1800.2 Review & Sign-Off Checklist

- [ ] **Factory Compliance**: All components, transactions, and sequences use `type_id::create()`; zero direct `new()` calls on factory types.
- [ ] **No Field-Macro Bloat**: `do_copy`, `do_compare`, and `convert2string` implemented cleanly; transaction handles cloned before writing to `uvm_analysis_port`.
- [ ] **Scoped `uvm_config_db`**: Zero unnecessary `"*"` global wildcards; every `uvm_config_db::get` return status checked.
- [ ] **Clocking Block Isolation**: Driver and monitor use dedicated clocking blocks; zero zero-delay `#0` hacks.
- [ ] **Constraint Solver Performance**: Avoid `solve ... before ...` unless distribution skew requires it; guard division/modulo constraints against zero; keep soft defaults overridable (`soft`).
- [ ] **On-the-Fly Reset**: `fork ... join_any; disable fork;` pattern handles asynchronous reset mid-transfer without `SEQREQZMB` sequencer lockups.
- [ ] **Scoreboard End-of-Test Check**: `check_phase` / `report_phase` verifies all expected queues, reorder buffers, and pending transaction tables are empty (zero silent drops).
- [ ] **DPI-C Safety**: `chandle` instance isolation, zero memory leaks, and clean error return codes checked on SV side.
