"""Unit and MCP integration tests for BTTF SQLite Waveform Store (waves.db) & dv-verdi tools."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dv_agent_platform.adapters import VerdiAdapter  # noqa: E402
from dv_agent_platform.config import FrameworkConfig  # noqa: E402
from dv_agent_platform.dv_tools import WaveformStore, ingest_fsdb_samples, ingest_vcd  # noqa: E402
from dv_agent_platform.mcp_servers import create_dv_mcp_server  # noqa: E402

VCD_FIXTURE = REPO_ROOT / "fixtures" / "waveforms" / "axi_fifo_fail.vcd"


class TestBTTFWaveformStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="dv_bttf_test_"))
        shutil.copytree(REPO_ROOT / "fixtures", self._tmp / "fixtures")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_01_vcd_ingestion_prunes_clocks_and_zero_toggle_preserving_active_coverage(self) -> None:
        db_pruned = self._tmp / "waves_pruned.db"
        db_raw = self._tmp / "waves_raw.db"

        stats_pruned = ingest_vcd(VCD_FIXTURE, db_pruned, prune_clocks=True, prune_zero_toggle=True)
        stats_raw = ingest_vcd(VCD_FIXTURE, db_raw, prune_clocks=False, prune_zero_toggle=False)

        self.assertEqual(stats_pruned["raw_signal_count"], 8)
        self.assertEqual(stats_pruned["pruned_clock_signals"], 1)
        self.assertEqual(stats_pruned["pruned_zero_toggle_signals"], 2)
        self.assertEqual(stats_pruned["active_signal_count"], 5)
        self.assertEqual(stats_pruned["active_toggle_coverage_preserved"], 1.0)
        self.assertGreater(stats_pruned["change_reduction_pct"], 50.0)
        self.assertLess(stats_pruned["stored_change_count"], stats_raw["stored_change_count"])

        store = WaveformStore(db_pruned)
        self.assertEqual(store.stats()["timescale"], "1ns")

        # Zero-toggle signal still answers exact constant value queries without change rows
        zt_val = store.get_value_at_time("err_status_w1c", 1260, scope="tb_top.u_dut")
        self.assertEqual(zt_val["value"], "b0")
        self.assertEqual(zt_val["pruned_reason"], "zero_toggle")

        # Clock signal reports clock pruning clearly
        clk_val = store.get_value_at_time("clk", 1260, scope="tb_top.u_dut")
        self.assertEqual(clk_val["pruned_reason"], "clock")

        # Active signals match raw unpruned values identically
        raw_store = WaveformStore(db_raw)
        for sig, ts in [("m_valid", 1240), ("soft_clr", 1250), ("m_data", 1245), ("m_valid", 1260)]:
            p_res = store.get_value_at_time(sig, ts, scope="tb_top.u_dut")
            r_res = raw_store.get_value_at_time(sig, ts, scope="tb_top.u_dut")
            self.assertEqual(p_res["value"], r_res["value"])
            self.assertEqual(p_res["hex_value"], r_res["hex_value"])

    def test_02_deterministic_queries_and_backward_causal_trace(self) -> None:
        db_path = self._tmp / "waves.db"
        ingest_vcd(VCD_FIXTURE, db_path)
        store = WaveformStore(db_path)

        # find_signal_in_scope
        m_sigs = store.find_signal_in_scope("m_*", scope="tb_top.u_dut")
        self.assertEqual({s["name"] for s in m_sigs}, {"m_valid", "m_ready", "m_data"})

        # get_first_time_of_value supports hex matching against stored VCD binary
        first_deadbeef = store.get_first_time_of_value("m_data", "0xdeadbeef", scope="tb_top.u_dut")
        self.assertTrue(first_deadbeef["found"])
        self.assertEqual(first_deadbeef["first_time"], 1240)
        self.assertEqual(first_deadbeef["hex_value"], "0xdeadbeef")

        first_clr = store.get_first_time_of_value("soft_clr", "1", scope="tb_top.u_dut")
        self.assertTrue(first_clr["found"])
        self.assertEqual(first_clr["first_time"], 1250)

        # get_signal_toggles
        toggles = store.get_signal_toggles("m_valid", start_time=1200, end_time=1270, scope="tb_top.u_dut")
        self.assertEqual(toggles["window_toggles"], 2)
        self.assertEqual([t["time"] for t in toggles["transitions"]], [1240, 1260])

        # get_scope_toggle_coverage
        cov = store.get_scope_toggle_coverage("tb_top.u_dut")
        self.assertEqual(len(cov["scopes"]), 1)
        sc = cov["scopes"][0]
        self.assertEqual(sc["non_clock_signals"], 7)
        self.assertEqual(sc["toggled_signals"], 5)
        self.assertEqual(set(sc["zero_toggle_signals"]), {"err_status_w1c", "dbg_tie_low"})

        # backward_trace_window strictly causal (t <= 1260) exposes soft_clr -> m_valid drop
        trace = store.backward_trace_window(
            violation_time=1260,
            signals=["soft_clr", "m_valid", "m_ready", "m_data"],
            window_before=30,
            scope="tb_top.u_dut",
        )
        self.assertEqual(trace["window"], [1230, 1260])
        self.assertTrue(all(ev["time"] <= 1260 for ev in trace["causal_timeline"]))
        events = [(ev["time"], ev["signal"].split(".")[-1], ev["to"]) for ev in trace["causal_timeline"]]
        self.assertIn((1250, "soft_clr", "1"), events)
        self.assertIn((1260, "m_valid", "0"), events)

    def test_03_fsdb_samples_ingestion_and_dv_verdi_mcp_roundtrip(self) -> None:
        # 1) Direct FSDB sample ingestion
        samples = [
            {"time_ns": 1240, "soft_clr": 0, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef", "tie0": 0},
            {"time_ns": 1250, "soft_clr": 1, "m_valid": 1, "m_ready": 0, "m_data": "0xdeadbeef", "tie0": 0},
            {"time_ns": 1260, "soft_clr": 0, "m_valid": 0, "m_ready": 0, "m_data": "0x00000000", "tie0": 0},
        ]
        fsdb_db = self._tmp / "fsdb_slice.waves.db"
        st = ingest_fsdb_samples(samples, fsdb_db, scope="tb_top.u_dut")
        self.assertEqual(st["pruned_zero_toggle_signals"], 2)  # m_ready and tie0 never toggled
        fstore = WaveformStore(fsdb_db)
        self.assertEqual(fstore.get_value_at_time("m_ready", 1255, scope="tb_top.u_dut")["value"], "0")
        self.assertEqual(fstore.get_first_time_of_value("soft_clr", "1", scope="tb_top.u_dut")["first_time"], 1250)

        # 2) End-to-end dv-verdi MCP JSON-RPC roundtrip
        cfg = FrameworkConfig.load(project_root=self._tmp, overrides={"dry_run": False})
        srv = create_dv_mcp_server("dv-verdi", cfg)
        tool_names = {t["name"] for t in srv.list_tools()}
        self.assertTrue(
            {
                "index_vcd_to_sqlite",
                "find_signal_in_scope",
                "get_value_at_time",
                "get_first_time_of_value",
                "get_signal_toggles",
                "get_scope_toggle_coverage",
                "backward_trace_window",
            }.issubset(tool_names)
        )

        req_stream = io.StringIO(
            "\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "index_vcd_to_sqlite",
                        "arguments": {
                            "vcd_path": "fixtures/waveforms/axi_fifo_fail.vcd",
                            "db_path": ".dv_agent/waveforms/axi_fifo.waves.db",
                        },
                    },
                }),
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "backward_trace_window",
                        "arguments": {
                            "db_path": ".dv_agent/waveforms/axi_fifo.waves.db",
                            "violation_time": 1260,
                            "signals": ["soft_clr", "m_valid", "m_data"],
                            "window_before": 30,
                            "scope": "tb_top.u_dut",
                        },
                    },
                }),
            ]) + "\n"
        )
        out_stream = io.StringIO()
        srv.serve_stdio(req_stream, out_stream)
        responses = [json.loads(ln) for ln in out_stream.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(responses), 3)
        self.assertIn("indexed", responses[1]["result"]["content"][0]["text"])
        trace_text = responses[2]["result"]["content"][0]["text"]
        self.assertIn("<untrusted_dv_data", trace_text)
        self.assertIn("soft_clr", trace_text)
        self.assertIn("1250", trace_text)

        # 3) Path traversal outside project_root is rejected
        verdi = VerdiAdapter(cfg)
        with self.assertRaises(ValueError):
            verdi.index_vcd_to_sqlite("../../etc/passwd")


if __name__ == "__main__":
    unittest.main(verbosity=2)
