# Formal Property Verification (FPV) Sign-Off Checklist

- [ ] **Reset Legality**: Formal reset sequence matches hardware power-on/warm reset spec; post-reset state free of unintended X's.
- [ ] **Input-Only Assumptions**: Zero `assume` statements on internal RTL signals (except documented cut-points with separate upstream proofs).
- [ ] **Assume-Guarantee Mapping**: Every boundary `assume` maps to either a standard bus VIP rule or an `assert` proven/simulated on the peer block.
- [ ] **Zero Vacuity & Contradiction**: All `assert` properties pass non-vacuity checks; dead-end state check (`cover` extending `N` cycles past witness) passes.
- [ ] **End-to-End Witness Covers**: Maximum-concurrency, backpressure, and error-path `cover` properties reach witness traces within expected latency.
- [ ] **Bounded Proof Justification**: Any inconclusive/bounded property documents `achieved_bound >= architectural_diameter` plus complexity mitigation attempts.
- [ ] **Formal Coverage**: Formal Cone-of-Influence / observability coverage audited alongside SVA proof table.
