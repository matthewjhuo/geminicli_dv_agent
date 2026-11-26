# Standard SVA Patterns & Non-Intrusive Bind Reference

## 1. Canonical Valid/Ready Handshake Stability & X-Check
```systemverilog
module axi_stream_sva #(
  parameter int DATA_W = 64,
  parameter int MAX_STALL_CYC = 256
) (
  input logic              clk,
  input logic              rst_n,
  input logic              valid,
  input logic              ready,
  input logic [DATA_W-1:0] data
);
  // 1) Reset and handshake controls must never be X/Z
  AST_RST_KNOWN: assert property (@(posedge clk) !$isunknown(rst_n))
    else $error("[SVA][AST_RST_KNOWN] X/Z detected on rst_n");

  AST_VALID_KNOWN: assert property (@(posedge clk) rst_n |-> !$isunknown({valid, ready}))
    else $error("[SVA][AST_VALID_KNOWN] X/Z on handshake valid=%b ready=%b", $sampled(valid), $sampled(ready));

  COV_VALID_KNOWN: cover property (@(posedge clk) rst_n && valid && ready);

  // 2) Payload must be known whenever a transfer completes
  AST_DATA_KNOWN: assert property (
    @(posedge clk) disable iff (!rst_n)
    (valid && ready) |-> !$isunknown(data)
  ) else $error("[SVA][AST_DATA_KNOWN] X/Z on data payload during valid && ready handshake");

  COV_DATA_KNOWN: cover property (
    @(posedge clk) disable iff (!rst_n)
    (valid && ready && !$isunknown(data))
  );

  // 3) Payload stability while stalled (valid && !ready)
  AST_HOLD_ON_STALL: assert property (
    @(posedge clk) disable iff (!rst_n)
    (valid && !ready) |=> (valid && $stable(data))
  ) else $error("[SVA][AST_HOLD_ON_STALL] valid dropped or data mutated during backpressure");

  COV_HOLD_ON_STALL: cover property (
    @(posedge clk) disable iff (!rst_n)
    (valid && !ready) ##1 (valid && ready)
  );

  // 4) Bounded backpressure responsiveness
  AST_BOUNDED_READY: assert property (
    @(posedge clk) disable iff (!rst_n)
    (valid && !ready) |-> ##[1:MAX_STALL_CYC] ready
  ) else $error("[SVA][AST_BOUNDED_READY] Ready stalled beyond %0d cycles", MAX_STALL_CYC);

  COV_BOUNDED_READY: cover property (
    @(posedge clk) disable iff (!rst_n)
    (valid && !ready) ##[1:MAX_STALL_CYC] ready
  );
endmodule
```

## 2. Non-Intrusive Bind Template
```systemverilog
bind dut_top axi_stream_sva #(
  .DATA_W(64),
  .MAX_STALL_CYC(128)
) u_egress_sva (
  .clk   (clk_sys),
  .rst_n (rst_sys_n),
  .valid (m_axis_tvalid),
  .ready (m_axis_tready),
  .data  (m_axis_tdata)
);
```
