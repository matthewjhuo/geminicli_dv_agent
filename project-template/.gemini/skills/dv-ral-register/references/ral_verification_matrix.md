# Register (CSR) & UVM RAL Verification Matrix

| Test Category | Target Scenario | Vehicle / Sequence | Self-Checking Mechanism | Built-in UVM Seq Sufficient? |
| :--- | :--- | :--- | :--- | :---: |
| **POR Reset** | Cold reset default values across all maps | `uvm_reg_hw_reset_seq` | RAL mirror compare | Yes |
| **Selective Warm Reset** | Sticky/retention fields survive warm reset while volatile CSRs reset | Custom `csr_warm_rst_seq` | `reg_model.reset("WARM")` + frontdoor read | **No** |
| **Bit Bash & Aliasing** | Walking 1/0 across `RW`/`RO` fields; cross-address non-interference | `uvm_reg_bit_bash_seq` + `uvm_reg_mem_hdl_paths_seq` | Explicit `uvm_reg_predictor` | Partial |
| **Frontdoor / Backdoor** | HDL path accuracy (`write`->`peek`, `poke`->`read`) | `uvm_reg_access_seq` | DPI HDL backdoor vs. bus monitor | Yes (for non-volatile regs) |
| **Unmapped & Hole Decode** | Read/write to unallocated offsets & boundary gaps | Custom `csr_unmapped_err_seq` | Bus `SLVERR`/`DECERR` or RAZ/WI check | **No** |
| **Sub-Word Byte Strobes** | Byte/halfword `WSTRB`/`PSTRB` partial field writes | Custom `csr_byte_strb_seq` | Explicit byte-aware predictor | **No** |
| **Dynamic Lock / Security** | Lock register blocks protected CSR writes until unlock sequence | Custom `csr_lock_prot_seq` + `uvm_reg_cbs` | Predictor + callback expected value | **No** |
| **SW W1C vs. HW Set Race** | Simultaneous SW `W1C` clear and HW interrupt/status event | Custom `csr_hw_sw_race_vseq` + SVA | SVA `AST_W1C_HW_PRIO` + Scoreboard | **No** |
