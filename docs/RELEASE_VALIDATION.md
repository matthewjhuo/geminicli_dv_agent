# Corrected offline snapshot validation

Source snapshot: 6137a8a115c53bf70976d2d5e7044da17d3f046c. Publication commit adds the kit/index/validation record only; the archived Python source is pinned to this source snapshot.

## Local evidence

Windows / Python 3.13.5. No network, model API or EDA calls during these checks.

| Check | Observed result |
| --- | --- |
| Source unit suite with genuine bundled Harness2 core | 121 tests: 120 passed, 1 skipped (live Gemini) |
| Source offline E2E with genuine bundled Harness2 available | 74 passed |
| New correctness regressions | 25 passed |
| Reconstructed text payload | 193 files verified against manifest SHA-256 |
| Repeated restore | Refused existing destination; no overwrite |
| Restored unit suite | 121 tests: 118 passed, 3 skipped (2 Git-only packaging tests, live Gemini) |
| Restored offline E2E | 74 passed |
| Restored genuine Harness2 dry_sim | 4 done, 0 failed; GOLD-TRIAGE-001, GOLD-PLAN-002, GOLD-COV-003, GOLD-SEC-004 |
| Native recursive unit scenario | 2 tasks across agg/seq, passed |

Kit: offline/paste-kit-6137a8a, 12 text parts, 193 sources. Payload SHA-256: 85152534f87fb6ce499ae4a7c58a44d0f3838982d5f2ed8915a4dd1dc16ee1eb.

The native Harness2 source was reused only after validating the prior archive and every file digest; license is included. dry_sim and all Python mutation/scoreboard outputs are synthetic checks, not evidence of model improvement or company RTL correctness. Mutation signoff_ready remains false.

## Reproduce after restore

Follow the kit's 100_VERIFY_AND_INSTALL.txt. Set PYTHONPATH to the restored framework src, PYTHONUTF8=1 and HARNESS2_REPO_DIR to the sibling restored harness2 root; do not pip install anything.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m unittest discover -s tests_e2e -p 'test_*.py' -q
python3 scripts/run_harness2_dv.py --mode dry_sim --k 1 --recursion both --output .dv_agent/harness2-selftest
```

Use existing approved Gemini authentication; no credentials are included or modified. Installer tests use temporary synthetic projects; no existing company TB was installed or altered. Read CORRECTNESS_REMEDIATION.md for API/approval changes and external prerequisites. Remote OS/Python CI results are reported on the PR; they are separate from the local evidence above.
