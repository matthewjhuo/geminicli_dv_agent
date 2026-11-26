# Agentic Design Verification (DV) Framework for Gemini CLI

一套專為 **SoC / IP 設計驗證（Design Verification）** 打造、與具體專案解耦、具備嚴格安全護欄與證據溯源能力的 **Agentic DV 框架**，建構於 **Gemini CLI (`v0.68.0+`)** 與標準 Python 3 之上。

---

## 核心工程特性

- **五層漸進式架構（Level 1 → Level 5）**：從單一標準化 DV 助手平滑擴展至多代理分工、DAG 團隊協作、MCP 工具鏈平台與具備人類核准晉升閘門的四層驗證記憶系統。
- **嚴格區分「Gemini CLI 原生能力」與「自訂平台擴充」**：充分利用原生 `GEMINI.md`、`SKILL.md`、`.gemini/agents/*.md`、`.gemini/commands/*.toml`、Policy Engine 與 Lifecycle Hooks，避免綁死非公開內部 API；Python 平台層完全零第三方套件依賴。
- **晶片研發級資安與治理紅線**：
  - **預設唯讀（Read-Only by Default）**：源碼修改與 VCS/Verdi/Formal 執行一律經過 Policy `ask_user` 與 Python `ApprovalGate` 雙重核准。
  - **Perforce 永久禁提**：`p4 submit`、`p4 push`、`p4 obliterate`、`p4 revert` 於 Policy Engine（`priority = 999`）、`BeforeTool` Hook 與轉接器程式碼三層永久阻斷。
  - **不可信資料防火牆（Prompt-Injection Immunity）**：所有 RTL、UVM、日誌、規格書、ADO/JIRA 工單皆視為被動不可信資料（以 `<untrusted_dv_data>` 隔離並自動掃描注入語句）。
  - **零硬編碼路徑與零內嵌憑證**：全面透過環境變數（`$DV_PROJECT_ROOT`、`$GEMINI_PROJECT_DIR` 等）解析；日誌與狀態自動遮蔽敏感 Token/Key；外部上傳預設關閉（`DV_ALLOW_EXTERNAL_UPLOAD=false`）。

---

## 原生 Gemini CLI 功能 vs. 自訂框架實作對照表

| 架構層級 | Gemini CLI `v0.68.0` 原生機制（Native） | 本框架自訂實作（Custom Implementation） |
| :--- | :--- | :--- |
| **Level 1：標準化 DV 助手** | 階層式 `GEMINI.md` 自動載入；`.gemini/skills/*/SKILL.md` 自動發現與按需啟用 | 全域 DV 安全憲章、參數化專案 `GEMINI.md` 範本、8 大 DV 領域技能與檢查表（Testplan / UVM / SVA / Debug / Coverage / Formal / CDC-RDC / RAL） |
| **Level 2：多代理系統** | `.gemini/agents/*.md` 嚴格 Schema 載入、`@dv-*` 委派、`tools` 白名單、`max_turns` / `timeout_mins` 限制 | 9 個專業 DV 子代理角色定義、最小權限工具矩陣、輸入需求與 `agent_output.schema.json` 結構化輸出契約 |
| **Level 3：代理團隊協作** | `.gemini/commands/dv/*.toml` 自訂斜線命令（`/dv:triage`、`/dv:plan` 等） | Python `Orchestrator`（DAG 拓撲排程、有界並行池、本地檔案行號證據核驗與自動降級、衝突裁決、`@dv-review` 獨立審查與有界重試迴圈、原子化狀態持久化與恢復、Dry-Run） |
| **Level 4：Agentic DV 平台** | `.gemini/policies/*.toml` 權限規則、`BeforeTool` / `AfterTool` Hooks、`settings.json` `mcpServers` | 無依賴 JSON-RPC stdio MCP 伺服器範本；VCS、Verdi、Perforce、Regression、ADO、JIRA 六大 Mock 優先轉接器（含 `adapters_validated` 真實環境門控）；`ApprovalGate`；JSONL 稽核日誌與 Token 預算追蹤器 |
| **Level 5：驗證閉環可信 AI** | 工作區目錄信任（`trustedFolders.json`）與 MCP 查詢整合 | SQLite 四層記憶庫（Semantic / Episodic / Procedural / Evidence）、`project_id` 專案隔離檢索、檔案 SHA-256 與版本溯源、`Candidate → Verified → Human Confirmed` 晉升狀態機、陳舊（Stale）偵測、黃金評測集與四維指標評估器 |

---

## 目錄結構總覽

```text
dv-agent-framework/
├── global/                        # 使用者層 ~/.gemini/ 範本（透過安裝器非破壞性合併）
│   ├── GEMINI.md                  # [L1] 全域 DV 安全與證據溯源憲章
│   └── settings.template.json     # [L4] 全域安全預設值（啟用 folderTrust）
├── project-template/              # 任意 SoC / IP 驗證專案範本
│   ├── GEMINI.md                  # [L1] 專案無關參數化 DV 上下文範本
│   ├── dv_project.example.json    # [L3-L5] 並行上限、Token 預算、轉接器模式設定範本
│   └── .gemini/
│       ├── settings.json          # [L4] Hooks 與 6 個 DV MCP 伺服器註冊
│       ├── skills/                # [L1] 8 個標準化 DV Agent Skills + references/
│       ├── agents/                # [L2] 9 個專業子代理（dv-spec .. dv-review）
│       ├── commands/dv/           # [L3] 12 個自訂斜線命令（/dv:testplan, /dv:debug, /dv:review, /dv:team, /dv:coverage, /dv:memory, /dv:evaluate 等）
│       ├── policies/              # [L4] 唯讀預設、核准閘門與永久禁止 TOML 規則
│       ├── hooks/                 # [L4] before_tool_guard.py 與 after_tool_audit.py
│       └── dv/contracts/          # [L2-L3] 子代理結構化輸出 JSON Schema
├── src/dv_agent_platform/         # [L3-L5] 模組化 Python 平台核心套件（僅用標準函式庫）
├── fixtures/                      # 合成 DV 測試夾具（AXI FIFO RTL、SVA、UVM、VCS 日誌、URG 覆蓋率、Mock Infra）
├── evaluation/golden/             # [L5] 黃金評測資料集（dv_golden_cases.json）
├── scripts/install.py             # 支援 --dry-run、自動備份與回滾的非破壞性安裝腳本
├── tests/                         # L1–L5 與安裝器完整單元及整合測試套件
└── docs/                          # 安裝指南、遷移指南與驗證報告
```

---

## 快速開始（Linux + VS Code Terminal）

1. **預覽安裝（預設 Dry-Run，不修改任何檔案）**：
   ```bash
   python3 scripts/install.py --project-dir "$DV_PROJECT_ROOT"
   ```
2. **執行完整測試套件驗證框架健康度**：
   ```bash
   PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
   ```
3. **在 Gemini CLI 互動模式中使用 DV 技能、子代理與斜線命令**：
   - **核心標準斜線命令**：
     - `/dv:testplan` — 自動產生 Verification Plan（亦可相容使用 `/dv:plan`）
     - `/dv:debug` — 分析 Simulation Failure 與根因定位（亦可相容使用 `/dv:triage`）
     - `/dv:review` — 獨立審查 RTL、UVM、SVA 與報告證據
     - `/dv:team` — 啟動 Multi-Agent Teamwork（DAG 分工 + 獨立審查）
     - `/dv:coverage` — 進行 Coverage Gap Analysis 與 Waiver 治理
     - `/dv:memory` — 搜尋已驗證的專案歷史 DV 知識
     - `/dv:evaluate` — 執行 Agent Benchmark 與四維指標評測
     - 專項延伸命令：`/dv:formal`、`/dv:rtl-trace`、`/dv:ral-audit`
   - 指定子代理：`@dv-debug`、`@dv-rtl`、`@dv-uvm`、`@dv-sva`、`@dv-coverage`、`@dv-formal`、`@dv-spec`、`@dv-architect`、`@dv-review`
4. **使用選配的 Python 編排器、記憶檢索與評測 CLI（預設 Dry-Run）**：
   ```bash
   PYTHONPATH=src python3 -m dv_agent_platform workflows
   PYTHONPATH=src python3 -m dv_agent_platform run triage -P log_path=fixtures/logs/simv_fail_seed1042.log -P test=axi_fifo_soft_clr_stress_test
   PYTHONPATH=src python3 -m dv_agent_platform memory --query "soft_clr"
   PYTHONPATH=src python3 -m dv_agent_platform evaluate
   ```
5. **（選配）透過 Harness² 進行遞迴 Harness 演化與優化**：
   ```bash
   # 離線驗證完整 Propose-Probe-Compose 與 Experience Library 流程（免 API Key）
   python3 scripts/run_harness2_dv.py --mode dry_sim --k 1 --recursion both

   # 連接真實 Gemini CLI（唯讀 plan 模式）對單一黃金案例執行演化
   python3 scripts/run_harness2_dv.py --mode gemini_cli --k 1 --tasks GOLD-TRIAGE-001
   ```
   > 內建 `validate_dv_harness_safety()` 安全閘門，確保 Harness² 演化過程中不會移除 Prompt-Injection 防火牆、`[APPROVAL REQUIRED]` 閘門、8 大核心 DV 技能，或寫入硬編碼路徑與密鑰。

進階部署與調校請參閱：
- [安裝指南 (`docs/INSTALLATION_GUIDE.md`)](./docs/INSTALLATION_GUIDE.md)
- [遷移與真實工具鏈接軌指南 (`docs/MIGRATION_GUIDE.md`)](./docs/MIGRATION_GUIDE.md)
- [完整驗證報告 (`docs/VALIDATION_REPORT.md`)](./docs/VALIDATION_REPORT.md)
