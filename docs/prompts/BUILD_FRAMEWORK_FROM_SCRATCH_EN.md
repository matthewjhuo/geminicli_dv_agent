# Build the framework from scratch — English prompt

Use this when the company server has no framework files and cannot access GitHub. Copy the entire text inside the block into Gemini CLI. This is an implementation request, not a claim that the requested features already exist.

```text
Build a complete, testable Agentic Design Verification Framework from scratch on this company server, with support for integrating an existing testbench.

This is an implementation task. Create files, implement functionality, run offline tests, fix failures, and deliver reproducible results. Do not stop at architecture, documentation, a checklist, or empty scaffolding.

Continue until the locally verifiable implementation is complete. External integrations that cannot be implemented or verified must explicitly return unsupported/unavailable. Never present mock success as a completed real operation.

1. ENVIRONMENT AND PRINCIPLES

- I use VS Code and Gemini CLI.
- Gemini CLI can already access the company's approved model service.
- This server cannot access GitHub or download source code, packages, or tools.
- Do not assume any framework files, deployment scripts, or external projects already exist.
- Use only an existing Python 3.11+ interpreter and the Python standard library for the core platform.
- Do not run pip install, download tools, access GitHub, or upload project data.
- Keep the framework separate from the existing testbench.
- Communicate in Traditional Chinese. Keep code identifiers, schema keys, and tool parameters in English.
- Do not claim file-for-file equivalence with an external repository, paper, or product.
- External names may describe design inspiration, but must not imply integration with unavailable source code.
- Clearly distinguish mock results, synthetic workflows, real model execution, and real EDA execution.

2. AUTHORIZATION AND WORKSPACE

Within a verified, newly created framework directory, you are authorized to:

- Create and modify framework code, configuration, documentation, tests, and synthetic fixtures.
- Run offline unit tests, integration tests, and local subprocess demonstrations.
- Use test-owned temporary directories to verify installation, upgrades, backups, restoration, and rollback.
- Perform these actions without requesting permission for each file.

Without separate authorization, do not:

- Modify existing RTL, testbench code, Makefiles, or company configuration.
- Run real compilation, simulation, regression, formal, Verdi, URG, or other EDA operations.
- Make model API calls or launch another live Gemini session for testing.
- Modify global Gemini settings, shell startup files, authentication, or credentials.
- Write to tickets or work items.
- Run Perforce submit, push, revert, or obliterate.
- Install software, download dependencies, or transmit project data externally.

Implementation work within this current Gemini CLI conversation is authorized. The restrictions above apply to additional live tests, EDA execution, and external operations.

First inspect the current directory and applicable project instructions. Do not build the platform inside the existing testbench.

Prefer:
$HOME/tools/agentic-dv-framework

If that directory exists, inspect it without overwriting anything and choose a new versioned directory.

Validate resolved write paths, including symlinks. Do not recursively scan the entire HOME directory. Do not read credential files or display API keys, tokens, passwords, or login data.

3. VERIFY LOCAL CAPABILITIES FIRST

1. Find an existing Python 3.11+ interpreter and record its executable path and version.
2. Inspect gemini --version, gemini --help, and available local documentation or schemas.
3. Verify the actual formats for skills, agents, commands, policies, hooks, MCP, and noninteractive invocation.
4. Do not invent configuration fields, hook events, agent schemas, or CLI flags from memory.
5. For native features whose formats cannot be verified locally:
   - Record the limitation in docs/GEMINI_COMPATIBILITY.md.
   - Do not install unverified configuration.
   - Do not claim the feature is enabled.
   - Provide a Python CLI or documented prompt equivalent.
6. Inspecting help/version does not constitute live model validation.
7. Enable GeminiCliBackend only when the local noninteractive and read-only invocation interface can be verified.
   Otherwise return an explicit unavailable result while keeping MockBackend and the Python core independently usable.

4. MINIMUM DIRECTORY STRUCTURE

agentic-dv-framework/
├── README.md
├── IMPLEMENTATION_STATUS.md
├── global/GEMINI.md
├── project-template/
│   ├── GEMINI.md
│   ├── company_profile.example.json
│   ├── dv_project.example.json
│   └── .gemini/
│       ├── settings.json
│       ├── skills/
│       ├── agents/
│       ├── commands/dv/
│       ├── policies/
│       ├── hooks/
│       └── dv/contracts/
├── src/dv_agent_platform/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── templates.py
│   ├── errors.py
│   ├── orchestrator/
│   ├── adapters/
│   ├── mcp_servers/
│   ├── memory/
│   ├── security/
│   ├── telemetry/
│   ├── evaluation/
│   └── dv_tools/
├── scripts/
│   ├── install.py
│   └── build_paste_kit.py
├── fixtures/
├── evaluation/golden/
├── tests/
├── tests_e2e/
├── docs/
└── offline/

Directories for unverified native formats may contain explanatory documentation. Do not treat invalid configuration as working functionality.

5. L1: EIGHT DV SKILLS

Create substantive SKILL.md files and supporting references for:

1. dv-testplan
   Specification → requirements → testplan → stimulus/checkers/coverage.
2. dv-uvm
   Sequences, drivers, monitors, scoreboards, TLM, reset, objections, and DPI.
3. dv-sva
   Sampling, reset semantics, bind, assertions, covers, and vacuity.
4. dv-regression-debug
   First failure, failure clustering, and competing RTL/TB/SVA hypotheses.
5. dv-coverage-closure
   Stimulus gaps, mode gaps, coverage model defects, unreachability, and waivers.
6. dv-formal
   Assumptions, overconstraint, witnesses, counterexamples, and bounded proofs.
7. dv-clock-reset
   Clock/reset behavior, CDC/RDC, mid-transaction reset, and recovery checks.
8. dv-ral-register
   Predictors, access policies, byte enables, reset domains, and HW/SW races.

Each skill must include:

- Applicability and required inputs.
- Concrete execution steps.
- Evidence requirements and output format.
- Conditions that must not be assumed.
- A checklist and at least one substantive example.
- How to stop or qualify conclusions when specifications or evidence are missing.

Adapt technical rules to the project. Do not impose one UVM architecture as universally correct.

Explain when explicit predictors, auto-prediction, clocking blocks, reset exceptions, and coverage targets are appropriate. Do not mechanically require changes to every existing environment.

6. L2: NINE SPECIALIST AGENTS AND OUTPUT CONTRACT

Create:

dv-spec
dv-architect
dv-rtl
dv-uvm
dv-sva
dv-debug
dv-coverage
dv-formal
dv-review

Define for every agent:

- Responsibilities, required inputs, steps, outputs, and execution limits.
- Minimum necessary tool permissions.
- dv-rtl and dv-review must be entirely read-only.
- dv-uvm and dv-sva may modify source only after explicit approval.
- Do not expose arbitrary shell execution to subagents.
- Reviewers must not review work they authored.
- Renaming an author's output does not constitute independent review.

Implement the dv-agent-output/v1 JSON contract:

contract_version
agent
task_id
status
summary
classification
findings
open_questions
proposed_actions
security_alerts

status:
ok
needs_input
blocked
failed

classification:
RTL_DEFECT
TB_DEFECT
SVA_DEFECT
SPEC_AMBIGUITY
ENV_OR_TOOL_ISSUE
INTENTIONAL_NEGATIVE_TEST
NOT_APPLICABLE

Each finding:
id
claim
epistemic
confidence
evidence

epistemic:
CONFIRMED
HYPOTHESIS
UNKNOWN

Evidence fields:
source
line_start
line_end
revision
excerpt
sim_time

Define required fields and types according to evidence kind. Do not require fabricated simulation timestamps.

dv-review additionally requires:
review_verdict=approve|revise|reject
review_issues

Implement contract validation using the standard library:

- Missing fields, wrong container types, and invalid enums.
- Booleans must not masquerade as integers or numbers.
- confidence must be finite and between 0 and 1.
- Reject NaN and Infinity.
- Validate every evidence entry, not just the first.
- Convert input violations into a manageable ContractViolation.
- Unexpected TypeError or AttributeError must not accidentally crash the entire workflow.

7. L3: COMMANDS, DAG, BACKENDS, STATE, AND RESUME

Provide these Gemini entry points, or clearly documented equivalents when native syntax cannot be verified:

/dv:testplan
/dv:plan
/dv:debug
/dv:triage
/dv:review
/dv:team
/dv:coverage
/dv:formal
/dv:rtl-trace
/dv:ral-audit
/dv:memory
/dv:evaluate

A prompt template is an entry point, not an already executed Python workflow.

Implement at least these Python workflows:

triage
plan-feature
coverage-closure
formal-signoff
rtl-trace
ral-audit

Each workflow must contain substantive tasks and independent review, rather than different names for an empty shared flow.

Example triage DAG:

collect failure
→ parallel RTL/TB/SVA investigation
→ deterministic evidence aggregation
→ independent review

Implement:

- DAG validation, cycle detection, and dependency management.
- Default concurrency of 2, hard maximum of 4.
- Bounded retries, timeouts, and review revisions.
- MockBackend supporting synthetic success, missing input, blocked tasks, failures, and review revisions.
- GeminiCliBackend using only locally verified invocation syntax.
- Read-only plan mode by default for automated analysis.
- Atomic state persistence with explicit schema versions.
- Resume and Markdown reports.

Execution correctness:

1. Only agent status=ok counts as succeeded.
2. needs_input, blocked, and failed must not produce overall success.
3. Missing, skipped, failed, or unfinished reviews must never default to approve.
4. Every required reviewer must approve.
5. After revise, invalidate affected tasks and all descendants, including stale outputs and aggregates.
6. Resume interrupted running tasks while preserving retry settings, timeouts, and recorded usage.
7. Resuming a successful run must not call the backend again.
8. Reject untrusted or unsupported state formats explicitly.
9. Return nonzero CLI exit codes for:
   failed, blocked, needs_input, needs_human, rejected_by_review.

CLI commands must include:

workflows
run
runs
status
report
resume
memory
evaluate

Provide an explicit mock execution entry point.
Resume must preview by default; --execute is required before invoking a model.
Live Gemini tests must be disabled by default.

8. EVIDENCE VALIDATION AND CLASSIFICATION

- Verify source existence, path boundaries, symlinks, line ranges, and excerpts.
- Read current source content on every verification; do not reuse stale text caches.
- Validate each citation within each finding independently.
- One valid citation must not conceal another invalid, missing, or out-of-scope citation.
- Downgrade unsupported CONFIRMED findings to HYPOTHESIS.
- Invalid or out-of-scope evidence must not increase classification weight.
- Reports must display effective epistemic status after verification.
- Distinguish:
  A. The citation exists.
  B. The citation supports the claim.
- A does not automatically establish B.
- Mark unverifiable external sources as external/unverified.
- Preserve competing hypotheses and use needs_human when appropriate.
- Retain downgrade reasons and individual evidence validation results.

9. L4: ADAPTERS AND SHARED SUBPROCESS EXECUTION

Create profiles for:

VCS
Verdi
Perforce
Regression
ADO
JIRA

All default to mock.
Real mode requires individual prerequisites and validation status.
adapters_validated=true must not substitute for an unimplemented connection.

VCS:

- Parse compilation and simulation logs.
- Exclude zero-error summaries such as UVM_ERROR : 0 and UVM_FATAL : 0.
- Separate total matches from retained excerpts.
- Use company-profile argv templates for compile/run.
- Preview, approval details, and execution must use the same rendered argv/cwd plan.
- Reject unknown variables and unsafe arguments.
- Return log_path only when the file exists.
- Exit code 0 does not establish test pass.
- Handle UVM errors/fatals, assertion failures, timeouts, and missing required completion markers separately.
- Return unknown when pass/fail cannot be determined.

Verdi:

- Suggest waveform time windows.
- Require approval for real fsdbreport wrapper execution.
- Explicitly reject unsupported output formats.
- Never fabricate signal values.
- Preserve fractional timestamps and their units.

Perforce:

- Read-only status, changes, describe, diff, filelog, and annotate.
- Require approval for preparation operations such as edit/add/change.
- Permanently reject submit, push, revert, and obliterate.
- Reject equivalent destructive flags or wrapper-based bypasses.

Regression:

- Scan local logs, normalize errors, and cluster failures.
- Real execution must use an approved company wrapper that waits for completion.
- Never return queued or invent a job ID without actual submission.
- Explicitly report unsupported asynchronous scheduler integration.

ADO/JIRA:

- Provide mock implementations and extension interfaces.
- Without company API specifications and a validation environment, real operations must raise UnsupportedOperation.
- Never fabricate created, updated, or ticket IDs.

Shared subprocess layer:

- shell=False, argv arrays, and explicit cwd.
- No arbitrary shell strings or shell wrappers that bypass validation.
- Bounded timeouts.
- Linux process-group cleanup to prevent orphaned children.
- Drain stdout and stderr concurrently.
- Bound retained memory per stream while continuing to drain excessive output.
- Redact secrets and report truncation.
- Separate timeout, returncode, and tool-level verdict.
- Test with local Python child processes, never EDA.
- Statistics or audit failures must be explicit and must not convert a tool failure into success.

10. WAVEFORM SQLITE AND EVENT QUERIES

Implement standard-library SQLite waveform indexing:

- VCD → SQLite.
- Parsed FSDB sample rows → the same data model.
- Do not pretend Python can directly decode arbitrary binary FSDB files.

Provide at least:

index_vcd_to_sqlite
find_signal_in_scope
get_value_at_time
get_first_time_of_value
get_signal_toggles
get_scope_toggle_coverage
backward_trace_window

Required correctness:

1. Use correctly encoded read-only SQLite URIs.
   Support spaces, non-ASCII characters, #, and % in paths.
2. Define time units and representation explicitly.
   Do not round 0.1 ns and 0.4 ns into the same integer nanosecond.
   If using floating point, document precision limits.
   If using integer ticks, implement unit conversion and range checks.
3. Record first-observed timestamps.
   Before the first observation, return unknown rather than a future value.
4. Preserve deterministic ordering for multiple events at the same timestamp.
5. get_first_time_of_value must handle a target value already held at the search-window start.
6. Never infer clocks solely from frequent or alternating transitions.
   Clock pruning must default to disabled.
   Enabling it requires an explicit clock list or a user-confirmed rule.
7. For pruned clocks, unavailable window event counts must be unknown/unavailable, not zero.
8. Zero-toggle pruning must preserve first-observed time and value.
9. Activity metrics must describe their actual signal-activity calculation.
   Do not present them as EDA bitwise 0→1/1→0 toggle coverage.
10. Do not hardcode active_toggle_coverage_preserved=1.0.
11. backward_trace_window is event history.
    Without RTL driver/COI/dependency analysis, it is not causal proof.
12. VCD support must include multiple tokens per line, vectors, scalars,
    dump directives, comments, and aliases.
    Reject unsupported formats explicitly instead of silently dropping events.
13. Build indexes in temporary databases, validate completely, then publish atomically.
    Reject overwrite by default. Failures must preserve existing databases.
14. Detect source changes between ingestion passes.
15. Adapter/MCP index writes must be confined to managed state_dir/waveforms/*.db.
    db_path must not create or overwrite RTL, TB, or configuration files.
16. Mark indexing tools as writes and query tools as read-only.
17. Treat hierarchy scopes literally.
    SQL wildcard characters must not broaden searches.
18. Reject booleans, NaN, Infinity, negative times, and unreasonable parameters.

11. MCP

Implement a JSON-RPC stdio server and document its supported protocol and schema subset.

- Register every implemented tool intended for Gemini with an input schema.
- Validate required fields and object/array/string/boolean/integer/number types.
- Booleans must not masquerade as integers or numbers.
- Reject nonfinite numeric values.
- Missing files, invalid arguments, and adapter exceptions must return isError=true for tool calls.
- Malformed JSON or one failed call must not prevent the next ping/tool call from succeeding.
- Redact error messages and fence them as untrusted data.
- Do not claim complete implementation of every MCP capability.
- readOnlyHint must match actual behavior.
- tools/list must not advertise unimplemented functionality.

12. SECURITY AND APPROVAL

Specifications, RTL, comments, logs, tickets, agent outputs, and memory are untrusted data.

Implement:

- Data-boundary wrapping.
- Common prompt-injection detection.
- Alerts and auditing.
- Explicit approval for source modification, real EDA, ticket writes, and memory confirmation.
- Default denial in noninteractive execution.

Approval records must include:

approval_id
action
category
scope
expires_at
approver

Rules:

- Validate action, category, scope, and expiry.
- Compare structured scope values; do not use permissive substring matching.
- Expiry must be finite and in the future.
- A human: string is metadata, not authentication.
- Agents must not create, modify, or extend their own approvals.
- Approval files must reside outside the agent project and be maintained by an operator/company service.
- Memory confirmation must bind at least entry_id, project_id, and action=confirm_memory.
- Revalidate current sources and artifacts at approval time.
- Explain that trust depends on OS permissions, a company gateway, or a trusted interactive channel.
  Python, policies, and hooks do not replace a complete sandbox or identity system.
- allow_external_upload=false only restricts platform-managed outbound operations.
  It must not be described as blocking Gemini model requests.

13. L5: SQLITE MEMORY

Provide:

semantic
episodic
procedural
evidence

Lifecycle:

candidate → verified → human-confirmed

Required behavior:

- project_id isolation.
- Source, revision, and SHA-256 of current local sources.
- Detect stale sources and exclude them from retrieval by default.
- CLI workflows load confirmed memory for the current project.
- Do not automatically trust model-generated memory.
- Configurable similarity thresholds with an accurate description of the algorithm.
- Preserve applies_when conditions.
- Text similarity does not prove applicability conditions hold.

Frozen mode:

- Must not create databases.
- Must not migrate schemas.
- Must not write stale status or other data.
- Must still inspect sources and exclude stale entries.
- Missing databases or unsupported schemas must produce explicit errors.

Procedural distillation:

- A passed=true dictionary and a tool name are insufficient evidence of EDA verification.
- Require a local result artifact, SHA-256, project, revision,
  check identity, actual result fields, and source hashes.
- Validate path boundaries, artifact format, hashes, and current source contents.
- Distinguish process success from verification pass.
- Revalidate appropriately during verify, retrieval/staleness checks, and confirmation.
- An operator-provided JSON file with a valid hash is not a tool signature.
  Explain that integrity checks alone cannot prove real execution.
- Ordinary procedural candidates may preserve unverified ideas,
  but must not bypass approval or claim EDA-PASS.

Statistical tool memory:

- Record actual tool successes/failures and error classifications.
- May provide Bayesian retry/recovery advice.
- Connect it to the real adapter execution path, not only manual unit-test injection.
- Isolate projects.
- Zero exit code with UVM errors/fatals is still a failure observation.
- Exclude zero-error summaries.
- Advice must not automatically retry, switch queues, or execute EDA.
- Frozen statistical memory must also remain read-only.

14. USAGE, BUDGET, AND AUDIT

- Atomically reserve estimated budget before each backend call.
- Concurrent tasks must not reuse the same remaining capacity.
- Settle or release reservations after success, failure, and timeout.
- Record actual overruns truthfully and stop subsequent work.
- Preserve recorded usage on resume.
- Unknown actual usage must not be presented as zero usage.
- Explain timeout, crash, and estimation limitations.
- Do not claim a provider billing hard cap.
- Provide JSONL audit, execution time, tokens, configurable pricing, and cost estimates.
- Do not hardcode prices as current official pricing.
- Redact audit data and avoid logging complete sensitive prompts.

15. GOLDEN EVALUATION AND SYNTHETIC MUTATION DEMO

Provide at least four substantive synthetic cases:

1. An RTL defect involving backpressure and soft reset.
2. Specification → testplan.
3. Coverage-hole classification.
4. Prompt injection in RTL comments/logs.

Evaluate classification, evidence, cost, and time.
Filenames or keyword matches alone must not count as correct root-cause analysis.
A matching citation does not establish a correct inference.

You may implement a custom FIFO Python mutation demo with independent defects and weak checkers.

Requirements:

- execution_mode=synthetic_python.
- synthetic=true.
- Use synthetic_ready or equivalent for demo thresholds.
- A synthetic demo must never set signoff_ready=true.
- Do not use SIGNOFF_READY as a synthetic success label.
- State that user RTL is not compiled/simulated and real SVA is not executed.
- Remove comments before regex-based inspection.
- Fake module/assert/cover declarations inside comments must not pass.
- Unsupported property semantics must be unsupported/unknown.
- A built-in Python scoreboard does not verify the user's scoreboard.
- Built-in scenario hits do not establish that the user's cover expression fired.
- Synthetic scores must not imply model improvement or company-design signoff.

16. STATIC DV CONTEXT TOOLS

Implement .f filelist indexing with:

- Environment variables and quoted paths.
- Correct relative-path semantics for -f/-F.
- +incdir+, +define+, -y, and -v.
- Include-cycle, path-traversal, and symlink checks.
- Explicit allowlists for external read-only VIP/library roots.
- Errors for missing paths and unresolved variables.

Provide preliminary SV/UVM indexing for:

module
interface
class
bind
RAL

Ignore fake declarations in comments.
State that this is not complete preprocessing, type resolution, or elaboration.
Do not infer completed CDC, connectivity verification, or formal proof from regex indexing.

17. OPTIONAL EVOLUTION INTERFACES

Provide project-local implementations of:

- Candidate safety/scoping checks.
- Two-layer checklists: execution/security/evidence failure must prevent semantic success credit.
- Baseline/candidate comparisons on the same evaluation cases.
- Finite 0..1 score validation; reject booleans, NaN, and Infinity.
- Default protection against regressions in mastered criteria.
- Bounded candidate/revision counts.
- Local wiki, proposal, and rejection records.
- Explicit [iff <condition>] scope for newly introduced rules.

If a guard is described as enabled, it must actually run before candidate execution or promotion.
A callable helper alone must be labeled optional API, not an end-to-end guarantee.

Ordinary Markdown/JSON wiki files are not immutable, rollback-proof, or transaction-safe across processes.
Document external storage/OS prerequisites if those guarantees are needed.

Harness²:

- If external source is unavailable, provide an optional interface and prerequisite checks.
- Missing dependencies must return unavailable/unsupported without breaking the core.
- Do not download source.
- Do not fabricate a package named Harness².
- Label custom propose/probe/compare demonstrations with this project's own name.
- Clearly identify test doubles; they do not establish genuine external integration.

18. COMPANY PROFILE AND INSTALLER

company_profile.json must describe at least:

project_id, DUT, methodology
RTL/TB/UVM/SVA/spec/filelist/log paths
interfaces, clocks, resets
compile/run/regression argv templates
adapter cwd, timeout, output limits
Python executable and platform path
mock, dry_run, per-adapter validation
external-transfer settings and external read-only roots

scripts/install.py must:

- Default to dry-run and list changes without writing to the target.
- Require --apply for installation.
- Validate the entire plan before writing.
- Merge a dedicated GEMINI.md block without duplication on reruns.
- Preserve existing MCP, hooks, and custom settings.
- Warn explicitly about conflicts.
- Use hashes to distinguish unchanged managed files from user modifications.
- Preserve custom files by default.
- Require an explicit replacement option, with backup, before replacing customized files.
- Accept VS Code JSONC without damaging // or /* inside strings.
- Write UTF-8 and LF.
- Back up before modification and include SHA-256 in manifests.
- Verify backup integrity before rollback.
- Record completed operations, incomplete operations, and recovery instructions after failure.
- Never touch credentials.
- Never modify global Gemini or shell configuration.

Install only these into the TB:

GEMINI.md
.gemini/
project configuration

Keep the Python platform in the tools directory.
Without a real TB path/profile, use synthetic projects only and do not invent a successful company installation.

19. COMPLETE COPY/PASTE BACKUP KIT

Implement scripts/build_paste_kit.py to package the locally created framework.

The server may not have Git metadata:

- Support packaging from an explicit framework directory without requiring Git.
- If a Git commit is used, record the actual commit.
- Otherwise identify the snapshot by source-manifest SHA-256.
- Exclude historical offline payloads, .git, runtime state, logs, caches,
  credentials, company TB files, and unrelated data.
- Package only allowlisted framework files.
- Do not include nonexistent Harness² source.
  Explicitly state when external source is not included.

Produce:

ALL_IN_ONE.sh
00_START.txt
numbered PART files
99_RESTORE.txt
restore.py
package-info.json
per-file SHA-256 manifest
verification and installation instructions

Requirements:

- Standard-library compression, Base64, and restoration.
- Verify each part and the complete archive.
- Reject duplicate ZIP entries, traversal, absolute paths, and symlink entries.
- Validate everything before writing to a new restoration directory.
- Refuse overwrite by default.
- A failure must not leave a partial directory that appears successful.
- Preserve file contents and LF.
- Actually reconstruct another directory from the generated text,
  compare every file hash, and run tests from the restored framework.
- Core tests must work without .git.
- Scanner exclusions must use paths relative to the framework root.
  An outer directory named .dv_agent, runs, or another excluded name must not disable the entire scan.
- Do not hardcode file counts, part counts, test totals, or “verified” claims.

20. IMPLEMENTATION ORDER

Complete these stages in order and run relevant tests after each:

A. Configuration, contracts, security, and mock fixtures.
B. Skills, agents, commands, and local Gemini compatibility.
C. DAG, backends, evidence, review, state, and resume.
D. Adapters, subprocess, MCP, static context, and waveforms.
E. Memory, approvals, budget, audit, evaluation, and optional evolution.
F. Profiles, installer, rollback, documentation, and paste-kit builder.
G. Full E2E, mock triage, and restored-kit verification.
H. Existing-TB installation preview only; do not apply without separate approval.

Never make tests pass by removing checks, relaxing path restrictions,
accepting fake evidence, changing unsupported to success,
or marking unexecuted functionality PASS.

21. REQUIRED ACCEPTANCE TESTS

Test behavior, not just constants or function existence:

- needs_input/blocked/failed upstream tasks cannot produce success.
- Missing/failed reviews, multiple reviewers, and revise descendant invalidation.
- Interrupted running-task resume, successful resume without repeated calls,
  and preserved settings/usage.
- Mixed valid/invalid evidence downgrade and verification after source changes.
- Concurrent budget competition, failure release, and truthful actual overrun accounting.
- MCP missing files, malformed JSON, and wrong types followed by successful ping/calls.
- Booleans, NaN, and Infinity rejected as numeric inputs.
- Unimplemented real adapters cannot fabricate success.
- Zero-error summaries and total-match/excerpt limits.
- Preview, approval, and actual argv/cwd equality.
- Real local subprocess stdout/stderr draining, timeout, and child cleanup.
- Paths, symlinks, filelist cycles, and external read-only roots.
- Waveform special-character paths, fractional timestamps, and unknown pre-observation values.
- Same-timestamp ordering and target values held at window start.
- Irregular high-activity data not pruned as a clock.
- Atomic ingestion, preservation of existing DB on failure, and source-change rejection.
- Index tools cannot write RTL/TB or arbitrary files.
- Comment-only SVA cannot pass; synthetic demos cannot grant signoff.
- Frozen memory does not create, migrate, or write stale state.
- project_id isolation and stale exclusion.
- A passed=true dictionary alone is insufficient EDA proof.
- Artifact/project/source/hash mismatches are rejected.
- human: labels and arbitrary approval IDs cannot confirm memory.
- Expired approvals, wrong scope/action, and in-project approval files are rejected.
- Installer dry-run zero writes, custom-file preservation, reruns, backups, and rollback.
- Complete mock triage → evidence → independent review → Markdown report.
- Complete text kit → restore → hashes → restored tests.
- Missing external Harness² does not break the core or appear as a successful integration.

Live Gemini, real EDA, company scheduler, and API tests must remain disabled by default.
If additional approval is necessary, complete all independent authorized work first.

22. CONTINUITY AND IMPLEMENTATION STATUS

Maintain IMPLEMENTATION_STATUS.md with:

- Completed modules.
- Actual test commands, results, and skip reasons.
- Incomplete or unverified functionality.
- Important decisions, compatibility changes, and limitations.
- The next concrete action.

Give a brief update after each stage, then continue.
Before context compaction, update the status file.
Resume from unfinished work afterward.

Do not stop because architecture, documentation, or a few modules are complete.

Do not use empty functions, TODOs, or NotImplementedError to impersonate required core functionality.
Explicit UnsupportedOperation is acceptable for unavailable external integrations,
but not as a substitute for locally implementable core functionality.

If the execution environment forces an interruption:
save code, test results, and status; list remaining work;
provide a directly usable continuation prompt.
Do not claim the entire framework is complete.

23. FINAL DELIVERABLES

Provide:

1. Actual platform location and complete file tree.
2. Feature status:
   implemented / offline-verified / requires onsite validation / unsupported.
3. Reproducible test commands and real pass/fail/error/skip totals.
4. Mock triage command and report location.
5. Waveform indexing/query demonstration, time units, and limitations.
6. Company-profile setup instructions.
7. TB installation preview, apply, and rollback commands.
8. Environment configuration and a continuation prompt for starting Gemini from the TB root.
9. Approval-record ownership, format, and usage for memory confirmation.
10. Paste-kit location, actual file/part counts, SHA-256, and restoration results.
11. Locally verified native Gemini compatibility.
12. All remaining limitations, especially models, EDA, schedulers, external APIs,
    precision, causal inference, tool attestation, and OS trust boundaries.

Start inspecting the environment and implementing now.
Do not merely restate these requirements and stop.
```
