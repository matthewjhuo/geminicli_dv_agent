# Framework correctness remediation

Baseline: main 3dcc9f5. Branch: codex/framework-correctness. Existing TB and company settings were not changed.

## Implemented

- Integrated execution-integrity fixes: fail-closed task/reviewer completion, downstream invalidation on revise, persisted resume settings/usage, atomic estimated budget admission, current-content evidence verification and resilient MCP errors.
- Real ADO/JIRA explicitly unsupported rather than fabricated success.
- Waveform URI encoding, fractional ns, first-observed times, deterministic same-time ordering and held-value window matching; conservative named-clock pruning; explicit activity/causality limitations; atomic no-clobber publication and source-change detection; VCD multi-token lines and malformed-token rejection; literal scope matching.
- Waveform indexing declared as a write tool and confined to managed state_dir/waveforms/*.db; MCP rejects bool/nonfinite numeric times.
- Synthetic mutation evaluation strips comments and never grants EDA signoff; synthetic_ready is separate.
- Artifact-backed memory integrity checks, true read-only frozen mode, project/entry/action/expiry-scoped external approval required for confirmation. Human labels alone are insufficient.
- Statistical memory wired by default; zero-exit UVM errors are failure observations; frozen statistics stay read-only.
- Candidate safety/scoping enforced before execution, two-layer scoring, default mastered-rule pinning and score validation. Windows extended-length Harness2 output paths and cleanup verified.
- One canonical committed-snapshot paste-kit builder; legacy CLI delegates to it. Restore supports Windows extended paths and refuses overwrite; added a real restore subprocess test and a restored scaffold scan under a parent .dv_agent directory. Scanner exclusions are relative to the restored root, so parent directory names cannot disable the scan.

## Actual local validation

Python 3.13.5 on Windows. Unit suite: 121 tests, 120 passed, 1 skipped (live Gemini); this includes native Harness2 recursive execution using the SHA-256-verified bundled core. Without optional core discovery: 119 passed, 2 skipped. E2E: 74 passed. Custom correctness regressions: 25 passed. Native Harness2 dry_sim: 4 golden cases done, 0 failed; no model or EDA calls. Native recursive test: 2 cases across agg/seq, passed.

Commands: PYTHONPATH=src, PYTHONUTF8=1; python -m unittest discover -s tests -q; python -m unittest discover -s tests_e2e -q. For optional bundled core set HARNESS2_REPO_DIR to its restored root. scripts/run_harness2_dv.py --mode dry_sim --k 1 --recursion both --output .dv_agent/h2-check.

## Remaining environment and scope limits

No live Gemini/company EDA, scheduler or ticket API validation. SQLite REAL is floating-point time. SV/VCD indexing is a documented subset, not elaboration or full parsing. Mutation scores concern a built-in Python model only. EDA artifacts are integrity-checked operator records, not cryptographic tool attestations. Approval files need OS/gateway protection; the platform cannot authenticate arbitrary human labels itself. Optional guard promotion APIs do not replace the external Harness2 scheduler's adoption policy. Wiki files are ordinary local files, not immutable or transaction-safe across processes. Retry streak is local to an adapter instance. See docs/CORRECTNESS_REMEDIATION.md.

## Next action

Corrected snapshot 6137a8a: 193 files / 12 parts, archived with per-file SHA-256. Actual restore refused repeated overwrite. Restored unit/E2E/native dry_sim validation is recorded in docs/RELEASE_VALIDATION.md. Publish the reviewable branch/PR and inspect CI. Company installation remains preview-only until a real TB/profile is supplied.
