// Synthetic SVA Checker Fixture (Non-intrusive bind target)
module axi_fifo_sva #(parameter int DATA_W = 32) (
  input logic              clk,
  input logic              rst_n,
  input logic              m_valid,
  input logic              m_ready,
  input logic [DATA_W-1:0] m_data
);
  AST_HOLD_ON_STALL: assert property (
    @(posedge clk) disable iff (!rst_n)
    (m_valid && !m_ready) |=> (m_valid && $stable(m_data))
  ) else $error("[SVA][AST_HOLD_ON_STALL] m_valid dropped or m_data changed while stalled");

  COV_HOLD_ON_STALL: cover property (
    @(posedge clk) disable iff (!rst_n)
    (m_valid && !m_ready) ##1 (m_valid && m_ready)
  );
endmodule
