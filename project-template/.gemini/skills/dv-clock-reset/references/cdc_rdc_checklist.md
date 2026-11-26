# Clock, Reset, CDC & RDC Verification Checklist

- [ ] **Clock Frequency & Phase Sweep**: Fast->Slow, Slow->Fast, 1:1 phase-swept, and `±ppm` drift corners covered across regression seeds.
- [ ] **Clock Gating Wake-Up**: Traffic/IRQ arrival on exact cycle of clock-gate disable (`clk_en` falling edge) verified without dropped transactions.
- [ ] **Async FIFO Gray-Code Check**: SVA enforces `<= 1` bit transition per source clock edge on Gray pointers prior to synchronizer sampling.
- [ ] **MCP / Handshake Data Stability**: SVA verifies multi-bit CDC payload remains `$stable` from `req` launch until synchronized `ack` return.
- [ ] **Randomized Sync Latency (1-Cycle Jitter)**: Behavioral CDC synchronizer models support `±1` destination-cycle metastability latency randomization in simulation (`ifdef SIM_CDC_JITTER`).
- [ ] **RDC Isolation**: Asserting partial/domain reset `rst_a_n` while domain `B` stays active causes zero X-propagation or protocol violation in domain `B`.
- [ ] **Mid-Transaction Warm Reset**: Reset asserted mid-burst cleanly recovers to full post-reset traffic without UVM sequencer lockup (`SEQREQZMB`) or stale scoreboard mismatches.
- [ ] **X-Optimism Check**: Critical reset/retention paths validated under VCS X-propagation / random register initialization settings.
