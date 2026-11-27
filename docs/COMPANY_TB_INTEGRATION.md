# Company testbench integration

Use this framework with the existing company build/run scripts. The Python workflow
CLI executes model tasks in read-only plan mode; it does not implement an autonomous
edit/compile/retry loop. Interactive authoring still requires human approval.

## Offline deployment

Transfer a complete repository snapshot through the company's approved channel.
The core requires Python 3.11+ for the complete test suite and no pip dependencies.
Gemini CLI must already be installed and able to access the approved model service.
Not being able to access GitHub does not prevent local installation.

Harness² is optional and requires a separately transferred, compatible harness2
checkout and its dependencies. Set HARNESS2_REPO_DIR when using that feature.
Do not install the E2E test double as a production Harness² dependency.

Set DV_AGENT_FRAMEWORK_HOME to the transferred framework directory and
DV_PROJECT_ROOT to the existing testbench workspace, then:

```bash
export DV_AGENT_PLATFORM_PYTHONPATH="${DV_AGENT_FRAMEWORK_HOME}/src"
export PYTHONPATH="${DV_AGENT_PLATFORM_PYTHONPATH}"
export DV_ADAPTER_MODE=mock
export DV_ALLOW_EXTERNAL_UPLOAD=false
cp "${DV_AGENT_FRAMEWORK_HOME}/project-template/company_profile.example.json" company_profile.json
# Edit company_profile.json using your own relative paths and wrapper commands.
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" \
  --project-dir "${DV_PROJECT_ROOT}" --profile company_profile.json \
  --strict-render --emit-vscode --emit-env-example
# Review the preview; repeat with --apply to install with backups.
```

The upload flag applies to explicit framework outbound operations, not Gemini CLI
model requests or arbitrary company scripts. Use the organization's approved model
service and code handling policy.

## Commands are argv templates, not shell programs

Prefer arrays so executable paths and paths containing spaces remain single arguments:

```json
{
  "testbench": {
    "compile_cmd": ["make", "-C", "${DV_PROJECT_ROOT}/verif/sim", "comp", "TEST=${TEST}"],
    "sim_cmd": ["make", "-C", "${DV_PROJECT_ROOT}/verif/sim", "run", "TEST=${TEST}", "SEED=${SEED}", "DUMP_FSDB=${DUMP_FSDB}"],
    "regression_cmd": ["make", "-C", "${DV_PROJECT_ROOT}/verif/sim", "regress", "SUITE=${SUITE}", "SEEDS=${SEEDS}"],
    "read_only_roots": []
  },
  "adapters": {
    "vcs": {"cwd": ".", "log_path": "sim_out/${TEST}_${SEED}/simv.log"},
    "regression": {"timeout_s": 3600}
  }
}
```

Adapter compile_cmd/sim_cmd override testbench commands. VCS default_flags are only
used by the built-in vcs command builder; explicit wrapper commands supply their own
flags. BUILD_TARGET is also available to compile templates. Scheduler prefixes are
argv arrays and must wait for completion (for example a site-approved blocking wrapper).
Do not configure asynchronous submission as if it were synchronous execution.

Templates are tokenized before environment expansion; unknown variables and shell
metacharacters are rejected. Shell startup, module loading, pipelines and redirects
belong inside an independently reviewed site wrapper, not in a command string.
Dry-run, approval details and real execution use the same argv and cwd.

Simulation status executed and regression status completed describe process completion,
not a verification pass. Check the simulator log, test completion and scoreboard.
No log path is returned unless the configured path exists. The wrapper is responsible
for creating logs and waiting for its child jobs.

Real mode requires the corresponding adapters_validated flag and human approval.
Use a protected DV_APPROVAL_FILE with narrow scope and expiry; do not grant an
unbounded simulation approval. Validate each adapter against the actual Linux tool
version before enabling it. No real EDA tools were exercised by the offline tests.

## Read-only analysis and MCP

dv-vcs exposes compile, parse_log, parse_compile_log, run_simulation, parse_filelist,
and inspect_sv_context. dv-regression exposes scan_sim_out as well as cluster_failures,
coverage_summary and launch. Real cluster_failures reads the configured local sim_out
directory; it never substitutes mock results.

Filelist parsing supports quoted paths, variable substitution, continuations, -f/-F,
+incdir+, +define+, -y and -v. Unsupported switches are reported. It is an indexer,
not a simulator parser. SV inspection uses regex and ignores comments/strings; it does
not preprocess macros, resolve types, or elaborate hierarchy.

All paths default to project_root containment. testbench.read_only_roots explicitly
allows shared VIP/library directories for the two read-only indexers, including
resolved symlinks. It does not widen command working directories or write access.
Use only directories approved for model access, not a broad filesystem root.

Subprocess stdout/stderr are drained with bounded in-memory retention and truncation
metadata. Linux timeouts terminate the spawned process group. Windows terminates the
direct child; production scheduler cleanup remains the site wrapper's responsibility.
FSDB text output must match the installed fsdbreport version. Fractional time is
preserved and fs/ps/ns/us/ms headers are converted to ns.

## Upgrade and rollback

The installer tracks generated file hashes in .dv_agent/install-state.json.
Unmodified managed assets can be upgraded. Customized assets and existing
dv_project.json are preserved with warnings. --replace-customized explicitly replaces
them with backups. Review the resulting configuration after a framework upgrade.

GEMINI.md uses a single managed section. Gemini settings merge additively.
Same-name MCP servers/hooks remain preserved by default; use
--refresh-framework-settings to refresh the framework's named entries with backup.
Other servers/hooks remain intact. VS Code JSONC (comments and trailing commas) is
accepted, but the merged file is written as ordinary JSON and comments are not retained.

Use the installation's printed backup_dir with scripts/install.py --rollback.
Rollback verifies backup hashes, restores overwritten files and removes created files.
It restores that installation snapshot, so save subsequent edits before rolling back.

## Validation boundaries

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests_e2e -p 'test_*.py' -v
```

Native Gemini discovery is opt-in with DV_TEST_LIVE_GEMINI=1. The real Harness²
integration tests require its external checkout; missing prerequisites are skipped,
not reported as successful real integration. E2E tests use fake EDA/Gemini commands
and may stage a Harness² test double. They check software behavior, not EDA compatibility.

Harness² reports label dry_sim results as synthetic. Even gemini_cli currently uses a
deterministic offline proposer; do not call this autonomous model-driven evolution.
Real-mode scores require structured, matching line/excerpt evidence for expected
sources. This checks citation validity, not semantic root-cause correctness.
The historical R3 report describes partial real rollouts interrupted by quota limits;
paid quota alone does not establish that all candidates will complete or improve.
