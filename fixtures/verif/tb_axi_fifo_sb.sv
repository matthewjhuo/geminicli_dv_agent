// Synthetic UVM Scoreboard Fixture
class axi_fifo_scoreboard extends uvm_scoreboard;
  `uvm_component_utils(axi_fifo_scoreboard)

  bit [31:0] exp_q[$];
  int unsigned mismatch_cnt;

  function new(string name = "axi_fifo_scoreboard", uvm_component parent = null);
    super.new(name, parent);
  endfunction

  function void write_egress(bit [31:0] act_data);
    bit [31:0] exp_data;
    if (exp_q.size() == 0) begin
      `uvm_error("SB_UNEXP", $sformatf("Unexpected egress beat 0x%08h", act_data))
      mismatch_cnt++;
      return;
    end
    exp_data = exp_q.pop_front();
    if (act_data !== exp_data) begin
      `uvm_error("SB_MISMATCH", $sformatf("Exp=0x%08h Act=0x%08h", exp_data, act_data))
      mismatch_cnt++;
    end
  endfunction
endclass
