"""Behavior regressions for the post-3dcc9f5 correctness review; no live services."""
import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dv_agent_platform.config import FrameworkConfig
from dv_agent_platform.dv_tools import WaveformStore, ingest_vcd, ingest_fsdb_samples
from dv_agent_platform.dv_tools.waveform_store import _atomic_ingest
from dv_agent_platform.evaluation.mutation_oracle import run_mutation_oracle_ladder, run_mutation_oracle_file
from dv_agent_platform.errors import PromotionError
from dv_agent_platform.memory import MemoryStore, StatisticalToolMemory, classify_eda_error
from dv_agent_platform.adapters import VCSAdapter
from dv_agent_platform.adapters.base import SubprocessResult
from dv_agent_platform.mcp_servers import create_dv_mcp_server
from dv_agent_platform.mcp_servers.protocol import validate_arguments
from dv_agent_platform.harness2_bridge import evaluate_two_layer_checklist, evaluate_soft_hurt_gate

ROOT = Path(__file__).resolve().parents[1]


class CorrectnessRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dv-correctness-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def samples(self, rows, **kwargs):
        db = self.root / 'waves #%.db'
        ingest_fsdb_samples(rows, db, **kwargs)
        return WaveformStore(db)

    def test_waveform_uri_handles_reserved_characters(self):
        store = self.samples([{'time_ns': 0, 'a': '0'}, {'time_ns': 1, 'a': '1'}])
        self.assertEqual(store.get_value_at_time('a', 1)['value'], '1')

    def test_fractional_times_survive_database_and_queries(self):
        store = self.samples([{'time_ns': 0.1, 'a': '0'}, {'time_ns': 0.4, 'a': '1'}])
        self.assertIsNone(store.get_value_at_time('a', 0)['value'])
        self.assertEqual(store.get_value_at_time('a', 0.2)['value'], '0')
        self.assertEqual(store.get_value_at_time('a', 0.4)['sample_time'], 0.4)
        self.assertEqual(store.get_signal_toggles('a')['transitions'][0]['time'], 0.4)

    def test_constant_not_known_before_first_sample(self):
        store = self.samples([{'time_ns': 10.5, 'a': '1'}])
        self.assertIsNone(store.get_value_at_time('a', 0)['value'])
        self.assertEqual(store.get_value_at_time('a', 11)['sample_time'], 10.5)
        self.assertFalse(store.get_first_time_of_value('a', '1', end_time=10)['found'])
        self.assertEqual(store.get_first_time_of_value('a', '1')['first_time'], 10.5)

    def test_value_held_at_window_start_is_found(self):
        store = self.samples([{'time_ns': 0, 'a': '0'}, {'time_ns': 1, 'a': '1'}, {'time_ns': 10, 'a': '0'}])
        self.assertEqual(store.get_first_time_of_value('a', '1', start_time=5, end_time=7)['first_time'], 5)

    def test_same_time_last_event_wins(self):
        store = self.samples([{'time_ns': 0, 'a': '0'}, {'time_ns': 1, 'a': '1'}, {'time_ns': 1, 'a': '0'}])
        self.assertEqual(store.get_value_at_time('a', 1)['value'], '0')

    def test_high_activity_data_not_inferred_to_be_clock(self):
        store = self.samples([{'time_ns': i*i, 'payload_bit': str(i % 2)} for i in range(12)])
        self.assertIsNone(store.find_signal_in_scope('payload_bit')[0]['pruned_reason'])
        self.assertEqual(store.get_signal_toggles('payload_bit')['window_toggles'], 11)
        self.assertIsNone(store.stats()['active_toggle_coverage_preserved'])

    def test_hierarchy_scope_is_literal_not_sql_wildcard(self):
        store = self.samples([{'time_ns': 0, 'tb_a.x': '0', 'tbXa.x': '1'}])
        self.assertEqual([x['full_path'] for x in store.find_signal_in_scope(scope='tb_a')], ['tb_a.x'])
        self.assertEqual([x['scope'] for x in store.get_scope_toggle_coverage('tb_a')['scopes']], ['tb_a'])

    def test_clock_window_is_unknown_and_event_history_disclaims_causality(self):
        store = self.samples([{'time_ns': i, 'clk': str(i % 2)} for i in range(6)])
        self.assertIsNone(store.get_signal_toggles('clk')['window_toggles'])
        self.assertIn('no RTL driver', store.backward_trace_window(5, ['clk'])['limitations'])

    def test_existing_database_not_overwritten_and_failure_is_atomic(self):
        db = self.root / 'original.db'
        db.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            ingest_fsdb_samples([{'time_ns': 0, 'a': '0'}], db)
        def broken(path):
            path.write_bytes(b'partial')
            raise ValueError('interrupted')
        with self.assertRaises(ValueError):
            _atomic_ingest(broken, db, overwrite=True)
        self.assertEqual(db.read_bytes(), b'original')
        self.assertEqual(list(self.root.glob('.waves-*')), [])

    def test_source_change_during_two_pass_ingestion_is_rejected(self):
        source = self.root / 'source.vcd'
        source.write_text('old')
        def changed(path):
            path.write_bytes(b'newdb')
            source.write_text('new')
            return {}
        with self.assertRaisesRegex(ValueError, 'changed'):
            _atomic_ingest(changed, self.root / 'out.db', source=source)
        self.assertFalse((self.root / 'out.db').exists())

    def test_vcd_multiple_events_per_line_and_bad_tokens(self):
        src = self.root / 'input.vcd'
        header = '$timescale 1ns $end\n$scope module top $end\n$var wire 1 ! a $end\n$var wire 2 ? data $end\n$upscope $end\n$enddefinitions $end\n'
        src.write_text(header + '#0 0! b00 ?\n#1 1! b11 ?\n')
        db = self.root / 'vcd.db'
        ingest_vcd(src, db)
        self.assertEqual(WaveformStore(db).get_value_at_time('a', 1)['value'], '1')
        self.assertEqual(WaveformStore(db).get_value_at_time('data', 1)['value'], 'b11')
        src.write_text(header + '#0 INVALID\n')
        with self.assertRaises(ValueError):
            ingest_vcd(src, self.root / 'bad.db')
        self.assertFalse((self.root / 'bad.db').exists())

    def test_invalid_times_are_rejected_without_database(self):
        for t in [True, -1, float('nan'), float('inf'), '1']:
            with self.subTest(t=t), self.assertRaises(ValueError):
                ingest_fsdb_samples([{'time_ns': t, 'a': '0'}], self.root / 'invalid.db')
            self.assertFalse((self.root / 'invalid.db').exists())

    def test_index_tool_declares_write_and_fractional_mcp_query(self):
        db = self.root / 'waves.db'
        ingest_fsdb_samples([{'time_ns': 0.1, 'a': '0'}, {'time_ns': 0.4, 'a': '1'}], db)
        cfg = FrameworkConfig.load(project_root=self.root, env={})
        server = create_dv_mcp_server('dv-verdi', cfg, fence_outputs=False)
        tools = {x['name']: x for x in server.list_tools()}
        self.assertFalse(tools['index_vcd_to_sqlite']['annotations']['readOnlyHint'])
        msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'get_value_at_time', 'arguments': {'db_path': 'waves.db', 'signal_name': 'a', 'time': 0.2}}}
        result = server.handle_message(msg)['result']
        self.assertFalse(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text'])['value'], '0')
        msg['params']['arguments']['time'] = True
        self.assertTrue(server.handle_message(msg)['result']['isError'])
        self.assertEqual(server.handle_message({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'})['result'], {})

    def test_waveform_index_write_stays_in_managed_directory(self):
        from dv_agent_platform.adapters import VerdiAdapter
        cfg = FrameworkConfig.load(project_root=self.root, env={})
        adapter = VerdiAdapter(cfg)
        src = ROOT / "fixtures/waveforms/axi_fifo_fail.vcd"
        local = self.root / "input.vcd"; local.write_bytes(src.read_bytes())
        for target in ["new_tb.sv", "other.db", ".dv_agent/waveforms/new_tb.sv"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                adapter.index_vcd_to_sqlite("input.vcd", target)
            self.assertFalse((self.root / target).exists())
        result = adapter.index_vcd_to_sqlite("input.vcd", ".dv_agent/waveforms/new.db")
        self.assertEqual(result["status"], "indexed")
        with self.assertRaises(FileExistsError):
            adapter.index_vcd_to_sqlite("input.vcd", ".dv_agent/waveforms/new.db")

    def test_comment_only_sva_cannot_pass(self):
        report = run_mutation_oracle_ladder('// module dummy\n// A: assert property ((m_valid && !m_ready) |=> (m_valid && $stable(m_data)));\n// endmodule')
        self.assertFalse(report['stages']['1_syntax_and_structure']['passed'])
        self.assertFalse(report['signoff_ready'])
        self.assertFalse(report['synthetic_ready'])

    def test_good_synthetic_oracle_never_claims_eda_signoff(self):
        report = run_mutation_oracle_file(ROOT / 'fixtures/verif/axi_fifo_sva.sv', include_scoreboard_integrity=True)
        self.assertTrue(report['synthetic_ready'])
        self.assertFalse(report['signoff_ready'])
        self.assertTrue(report['synthetic'])
        self.assertNotEqual(report['stages']['5_signoff_readiness']['verdict'], 'SIGNOFF_READY')

    def test_mutation_invalid_threshold_or_empty_set_rejected(self):
        for kwargs in [{'min_kill_rate': float('nan')}, {'min_kill_rate': -1}, {'mutants': []}]:
            with self.assertRaises(ValueError):
                run_mutation_oracle_ladder('', **kwargs)

    def test_frozen_memory_does_not_create_or_write_stale_state(self):
        missing = self.root / 'missing' / 'memory.db'
        with self.assertRaises(FileNotFoundError):
            MemoryStore(missing, self.root, mode='frozen')
        self.assertFalse(missing.parent.exists())
        source = self.root / 'rtl.sv'; source.write_text('module a; endmodule\n')
        db = self.root / 'memory.db'; store = MemoryStore(db, self.root)
        eid = store.add_candidate('p', 'semantic', 'reset', 'reset rule', [{'source': 'rtl.sv', 'revision': 'r1'}])
        # Seed confirmed state for a read-only stale-query test; this is not a promotion.
        with sqlite3.connect(db) as c:
            c.execute("UPDATE memories SET status='confirmed' WHERE entry_id=?", (eid,))
        c.close()
        before = db.read_bytes(); source.write_text('changed')
        frozen = MemoryStore(db, self.root, mode='frozen')
        self.assertEqual(frozen.retrieve('p', ''), [])
        self.assertEqual(frozen.get(eid, 'p')['status'], 'confirmed')
        self.assertEqual(db.read_bytes(), before)

    def test_pass_dictionary_is_not_verification_artifact(self):
        store = MemoryStore(self.root / 'memory.db', self.root)
        with self.assertRaisesRegex(PromotionError, 'artifact'):
            store.distill_procedural_skill('p', 'rule', do_rules=['check reset'], avoid_rules=[], applies_when='reset occurs', provenance=[{'source': 'missing', 'revision': 'r1'}], eda_verification={'tool': 'vcs', 'passed': True})

    def test_artifact_hash_project_and_source_are_checked(self):
        store = MemoryStore(self.root / 'memory.db', self.root)
        src = self.root / 'rtl.sv'; src.write_text('rtl')
        path = self.root / 'result.json'
        record = {'record_version': 'dv-eda-result/v1', 'tool': 'vcs', 'project_id': 'p', 'check_name': 'reset', 'passed': True, 'returncode': 0, 'timed_out': False, 'execution_mode': 'real', 'revision': 'r1', 'source_hashes': {'rtl.sv': hashlib.sha256(src.read_bytes()).hexdigest()}}
        path.write_text(json.dumps(record))
        proof = {'tool': 'vcs', 'passed': True, 'check_name': 'reset', 'artifact': 'result.json', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.assertTrue(store._validate_eda_proof('p', proof)['integrity_checked'])
        with self.assertRaises(PromotionError): store._validate_eda_proof('other', proof)
        with self.assertRaises(PromotionError): store._validate_eda_proof('p', {**proof, 'sha256': 'bad'})
        src.write_text('changed')
        with self.assertRaises(PromotionError): store._validate_eda_proof('p', proof)

    def test_statistical_memory_frozen_and_zero_exit_uvm_failure(self):
        self.assertEqual(classify_eda_error('vcs', 0, 'UVM_ERROR : 0\nUVM_FATAL : 0'), 'none')
        self.assertEqual(classify_eda_error('vcs', 0, 'UVM_ERROR @ 10: mismatch'), 'uvm_error')
        db = self.root / 'stats.db'; StatisticalToolMemory(db)
        before = db.read_bytes(); frozen = StatisticalToolMemory(db, mode='frozen')
        frozen.record_retry_outcome('vcs', 'uvm_error', 1, False)
        self.assertEqual(db.read_bytes(), before)

    def test_default_adapter_records_nonzero_and_uvm_failure_advice(self):
        cfg = FrameworkConfig.load(project_root=self.root, env={})
        adapter = VCSAdapter(cfg, subprocess_runner=lambda *a, **k: SubprocessResult(0, 'UVM_ERROR @ 10: mismatch', '', ['vcs']))
        result = adapter._run_command(['vcs'])
        self.assertIsNotNone(result.advisory)
        self.assertEqual(result.advisory['err_class'], 'uvm_error')

    def test_human_label_alone_cannot_confirm_memory(self):
        source = self.root / "source.txt"; source.write_text("evidence")
        from dv_agent_platform.security.approval import ApprovalGate, DenyAllApprover
        store = MemoryStore(self.root / "memory.db", self.root, approval_gate=ApprovalGate(DenyAllApprover()))
        eid = store.add_candidate("p", "semantic", "rule", "evidence", [{"source": "source.txt", "revision": "r1", "line_start": 1, "line_end": 1}])
        store.verify_entry(eid, "p")
        with self.assertRaises(PromotionError):
            store.promote_to_confirmed(eid, "p", "human:invented", "invented-id")
        self.assertEqual(store.get(eid, "p")["status"], "verified")

    def test_memory_approval_exact_scope_action_expiry_and_external_file(self):
        from approval_helpers import grant_memory
        from dv_agent_platform.security.approval import ApprovalGate, ApprovalFileApprover
        source = self.root / "source.txt"; source.write_text("evidence")
        store = MemoryStore(self.root / "memory.db", self.root)
        eid = store.add_candidate("p", "semantic", "rule", "evidence", [{"source": "source.txt", "revision": "r1", "line_start": 1, "line_end": 1}])
        store.verify_entry(eid, "p")
        record_path = grant_memory(self, store, eid, "p", "human:operator", "A1")
        original = json.loads(record_path.read_text())
        for updates in [{"expires_at": float("nan")}, {"expires_at": 0}, {"scope": {"entry_id": "other", "project_id": "p"}}, {"action": "different"}]:
            record = json.loads(json.dumps(original)); record["approvals"][0].update(updates)
            record_path.write_text(json.dumps(record))
            with self.assertRaises(PromotionError):
                store.promote_to_confirmed(eid, "p", "human:operator", "A1")
        record_path.write_text(json.dumps(original))
        local = self.root / "forged.json"; local.write_text(json.dumps(original))
        store.approval_gate = ApprovalGate(ApprovalFileApprover(local))
        with self.assertRaises(PromotionError):
            store.promote_to_confirmed(eid, "p", "human:operator", "A1")
        store.approval_gate = ApprovalGate(ApprovalFileApprover(record_path))
        confirmed = store.promote_to_confirmed(eid, "p", "human:operator", "A1")
        self.assertEqual(confirmed["status"], "confirmed")

    def test_guard_invalid_predicates_scores_and_pinned_regression(self):
        with self.assertRaises(ValueError): evaluate_two_layer_checklist({'valid': 'false'}, {})
        with self.assertRaises(ValueError): evaluate_soft_hurt_gate({'a': 1}, {'a': float('nan')})
        with self.assertRaises(ValueError): evaluate_soft_hurt_gate({'a': 1}, {'b': 1})
        gate = evaluate_soft_hurt_gate({'a': 1, 'b': 0, 'c': 0}, {'a': .9, 'b': 1, 'c': 1})
        self.assertFalse(gate['accepted'])
        self.assertEqual(gate['pinned_regressions'], ['a'])
        with self.assertRaises(ValueError): validate_arguments(float('inf'), {'type': 'number'})


if __name__ == '__main__':
    unittest.main()
