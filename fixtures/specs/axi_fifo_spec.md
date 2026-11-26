# AXI-Stream Skidded FIFO Micro-Architecture Specification (v1.2)

## 1. Overview
`axi_fifo` buffers 32-bit AXI-Stream beats up to `DEPTH = 8` entries under single clock `clk` and active-low reset `rst_n`.

## 2. Requirements
- **REQ-FIFO-001 (Handshake Stability)**: Once `m_valid` asserts, `m_valid` and `m_data` MUST remain asserted and stable until `m_ready` is sampled high at a rising `clk` edge, unless hard reset `!rst_n` asserts.
- **REQ-FIFO-002 (Soft Clear Semantics)**: `soft_clr` shall defer pointer clearing or remain gated while an unaccepted beat (`m_valid && !m_ready`) is stalled on the egress interface, OR spec must define abort handshake. (Current spec gap if downstream does not tolerate `m_valid` drop.)
- **REQ-FIFO-003 (Error Status CSR)**: `err_status_w1c[3:0]` implements Write-1-to-Clear (`W1C`); simultaneous hardware error set and software `W1C` clear on the same cycle MUST prioritize hardware set (`HW_WINS`).
