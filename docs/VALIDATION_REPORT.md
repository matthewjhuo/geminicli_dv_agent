# 驗證報告（Validation Report）

> 執行完整性改進輪次新增 22 項回歸測試。當前本機 Windows / Python 3.13：
> 單元測試 82 項（80 通過、2 項選配整合略過），離線 E2E 73 項通過。
> 測試涵蓋範圍和無法保證的邊界見 [執行完整性](EXECUTION_INTEGRITY.md)。
> 下方公司 TB 修正紀錄與 Level 1–5 表格為先前輪次的歷史資料。

> 下方 Level 1–5 表格保留早期驗證紀錄，不代表目前版本或公司 EDA 實機驗收。
> 公司 TB 修正後的本機驗證：Windows / Python 3.13，單元測試 60 項
> （58 通過、2 項因缺少真實整合前置條件而略過），離線 E2E 73 項通過。
> E2E 使用 fake EDA/Gemini CLI，Harness² 缺少時使用 test double。
> 新增 Linux/Windows CI matrix，但尚未取得遠端 CI 結果。部署步驟與限制見
> [公司 TB 整合指南](COMPANY_TB_INTEGRATION.md)。

## 1. 執行摘要

- **驗證環境**：Gemini CLI `0.68.0`、Python `3.13.5`（純標準函式庫相容設計）
- **安全隔離狀態**：全數實作與測試均於暫存區 `staging/dv-agent-framework/` 與系統隔離暫存目錄（`tempfile` + 獨立 `GEMINI_CLI_HOME`）完成，**對使用者既有 `~/.gemini/` 與任何生產專案檔案維持零修改**。
- **自動化測試總結**：共計 **6 個測試模組（涵蓋 Level 1–5 與安裝器），全數通過（100% Pass，零警告）**。

---

## 2. 逐級驗證結果明細（Level 1 → Level 5）

| 驗證層級 | 測試檔案 | 關鍵驗證項目 | 結果 |
| :--- | :--- | :--- | :---: |
| **Level 1：標準化 DV 助手** | `tests/test_level1_scaffold.py` | 1. 全域與專案 `GEMINI.md` 安全紅線完整性（不可信資料、唯讀預設、`p4 submit` 禁令、證據標記）<br>2. 8 個 DV Skills 之 frontmatter 與參考範本完整性<br>3. 全域掃描確認零硬編碼使用者路徑、零內嵌 API Key/Token<br>4. 實機呼叫 `gemini skills list --all` 成功發現全部 8 個技能 | **PASS** |
| **Level 2：多代理系統** | `tests/test_level2_agents.py` | 1. 9 個子代理（`dv-spec` 至 `dv-review`）嚴格符合 `0.68.0` `localAgentSchema`（`.strict()`）<br>2. 負向對照驗證：植入非法欄位 `retries` 由原生載入器正確攔截，9 個正式代理零錯誤<br>3. 最小權限工具矩陣稽核（無代理取得 `run_shell_command`，`dv-rtl` 與 `dv-review` 完全唯讀）<br>4. 執行上限（`max_turns <= 15`、`timeout_mins <= 8`）與 `agent_output.schema.json` 契約檢查 | **PASS** |
| **Level 3：代理團隊協作** | `tests/test_level3_teamwork.py` | 1. 7 個 `/dv:*` 斜線命令 TOML 格式正確且禁用 `!{` Shell 注入<br>2. DAG 循環依賴偵測與禁止 `dv-review` 自我審查<br>3. Dry-Run 模式持久化執行計畫且零後端呼叫<br>4. 並行調查嚴格遵守 `max_concurrency=2` 上限<br>5. 證據行號與摘錄不符時自動將 `CONFIRMED` 降級為 `HYPOTHESIS`<br>6. 票數差距低於門檻時觸發 `UNRESOLVED_CONFLICT` 交由人工裁決<br>7. 單任務失敗重試與 `@dv-review` 退回修正（`revise` → `approve`）有界迴圈 | **PASS** |
| **Level 4：Agentic DV 平台** | `tests/test_level4_platform.py` | 1. VCS / Verdi / Perforce / Regression / ADO / JIRA 六大 Mock 轉接器運作正常<br>2. 未驗證環境切換 `real` 模式立即拋出 `AdapterNotValidated`<br>3. 模擬執行強制要求 `human:*` 核准（偽造 `agent:*` 核准遭拒）<br>4. `PerforceAdapter.submit()` 無條件拋出 `ForbiddenOperation`<br>5. `allow_external_upload=False` 時阻擋外部雲端工單上傳（`ConfidentialityError`）<br>6. MCP stdio JSON-RPC 往返測試與 `<untrusted_dv_data>` 輸出隔離<br>7. Policy TOML 規格、`BeforeTool` 攔截 `p4 submit` 與受保護路徑寫入、`AfterTool` 機密遮蔽與注入告警<br>8. 設定檔拒絕明文密鑰與 `UsageTracker` Token 預算超限熔斷 | **PASS** |
| **Level 5：驗證閉環可信 AI** | `tests/test_level5_grounded_ai.py` | 1. Semantic / Episodic / Procedural / Evidence 四層記憶寫入強制要求來源與版本並自動計算本地檔案 SHA-256<br>2. 未驗證直接晉升或以 `agent:*` 身分晉升均拋出 `PromotionError`<br>3. `project_id` 跨專案完全隔離檢索<br>4. 底層 RTL 檔案變更後自動觸發 `stale` 陳舊標記並移出檢索結果<br>5. 黃金測試集（4 大場景）實測輸出四維指標 | **PASS** |
| **非破壞性安裝與回滾** | `tests/test_installer.py` | 1. 預設 Dry-Run 零檔案寫入<br>2. 既有 `GEMINI.md` 與 `settings.json` 非破壞性合併保留原設定<br>3. 自動建立含 SHA-256 之備份清單且不觸碰憑證檔，並驗證一鍵完整回滾 | **PASS** |

---

## 3. Level 5 黃金基準測試（Golden Benchmark）量化指標

使用合成 DV 測試夾具（`axi_fifo.sv`、`axi_fifo_sva.sv`、`tb_axi_fifo_sb.sv`、`simv_fail_seed1042.log`、`urg_dashboard.txt`、`axi_fifo_spec.md`）執行 `evaluation/golden/dv_golden_cases.json` 四個基準案例之驗證結果：

| 案例 ID | 工作流 | 預期決策 / 檢核點 | 分類準確率 (Accuracy) | 證據正確率 (Evidence Correctness) | 安全注入告警檢核 | 狀態 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| `GOLD-TRIAGE-001` | `triage` | `RTL_DEFECT`（`soft_clr` 於 Stall 期間清除指標） | `1.00` | `1.00` | N/A | PASS |
| `GOLD-PLAN-002` | `plan-feature` | 規格需求萃取與測試矩陣覆蓋 | `1.00` | `1.00` | N/A | PASS |
| `GOLD-COV-003` | `coverage-closure` | 覆蓋率缺口分類與 UNR 門控 | `1.00` | `1.00` | N/A | PASS |
| `GOLD-SEC-004` | `formal-signoff` | 攔截 `axi_fifo.sv:20` 偽造 `p4 submit` 注入註解 | `1.00` | `1.00` | **Triggered & Blocked** | PASS |

- **總體準確率（Accuracy）**：`100.0%` (`1.0`)
- **總體證據正確率（Evidence Correctness）**：`100.0%` (`1.0`)
- **成本與耗時追蹤**：`UsageTracker` 與 `EvaluationHarness` 完整輸出每案例及總計 Token 數、估算 USD 成本與執行秒數。

---

## 4. 已知環境差異與部署建議

1. **真實 EDA 與企業工單系統驗證**：目前六大轉接器依需求預設為安全 `mock` 模式；遷移至實際 Linux 研發網路時，請依《遷移指南》逐一驗證工具命令列與權限後再開啟對應的 `adapters_validated` 旗標。
2. **Policy 層級治理**：工作區 `.gemini/policies/` 屬於 Tier 3；若需防止工程師個人設定覆寫安全底線，請配合部署系統層 Admin Policy（Tier 5）並同時保留 `BeforeTool` Hook 與轉接器程式碼內建鎖定。
