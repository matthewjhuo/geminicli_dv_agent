# 專案遷移與真實工具鏈接軌指南（Migration Guide）

本指南協助 SoC / IP 驗證團隊將既有驗證專案、自訂腳本與真實 EDA / 基礎設施環境（VCS、Verdi、Perforce、Regression、ADO、JIRA）平滑遷移至本框架。

---

## 1. 既有驗證專案導入步驟（零侵入式）

1. **建立專案設定檔**：
   將 `project-template/dv_project.example.json` 複製為 `${DV_PROJECT_ROOT}/dv_project.json`，填入專案識別碼 `project_id`（例如 `"pcie_ctrl_v2"`）與 Token 預算上限。
2. **對應專案目錄結構至 `GEMINI.md`**：
   編輯 `${DV_PROJECT_ROOT}/GEMINI.md` 中的相對路徑表格（`$RTL_ROOT`、`$UVM_ENV_ROOT`、`$SVA_ROOT`、`$SIM_OUT_DIR`）與介面/時脈/重置網域表，**切勿填入個人絕對路徑或伺服器密碼**。
3. **加入版本控制忽略清單（Perforce `.p4ignore` / `.gitignore`）**：
   將執行期狀態與稽核日誌目錄排除於版本庫之外：
   ```text
   .dv_agent/
   .gemini/policies/auto-saved.toml
   ```

---

## 2. 從傳統單一 Prompt / 舊式腳本遷移至分層架構

| 舊有做法 | 遷移目標層級 | 遷移做法 |
| :--- | :--- | :--- |
| 長篇幅共用 System Prompt | **Level 1 (`GEMINI.md` + `SKILL.md`)** | 只將全專案共通安全與命名規範留在 `GEMINI.md`；將 UVM、SVA、RAL、CDC、Formal 等專業細則放入對應 `SKILL.md`，由模型按需載入以節省 Context Token |
| 單一萬能 Agent 同時讀 RTL 與改 TB | **Level 2 (`dv-rtl` + `dv-uvm` + `dv-review`)** | 拆分為唯讀 RTL 分析代理、受核准管制的 UVM 撰寫代理與獨立審查代理，實現職責分離 |
| 手動複製貼上多步驟除錯指令 | **Level 3 (`/dv:triage` 等 TOML 命令 + `Orchestrator`)** | 改用 `.gemini/commands/dv/*.toml`（禁止使用 `!{...}` Shell 注入語法），複雜跨模組任務交由 Python DAG 編排器管理狀態與重試 |
| 直接開放 Agent 執行任意 Shell | **Level 4 (MCP + Adapters + Policy + Hooks)** | 將常用唯讀查詢封裝為 MCP 工具並設為 `allow`；將模擬與改碼納入 `ask_user` 與 `ApprovalGate` |

---

## 3. 將 Mock 轉接器切換至真實 Linux 工具鏈（逐步驗證法）

框架預設 `adapter_mode = "mock"`，若未完成環境驗證即切換為 `"real"`，基底類別 `BaseAdapter` 會拋出 `AdapterNotValidated` 以防止誤觸生產環境。請依單一工具逐一接軌並驗證：

### 步驟 3.1：實作真實命令呼叫子類別或擴充分支
於 `src/dv_agent_platform/adapters/` 對應模組中（當 `self.is_mock is False` 時），透過參數陣列形式的 `subprocess.run([...], shell=False, timeout=...)` 串接內部工具：
- **VCS (`vcs.py`)**：`parse_log` 直接解析 `${SIM_OUT_DIR}` 下的真實 `simv.log`（無須外部 API）；`run_simulation` 在通過 `ApprovalGate` 後呼叫專案標準 `Makefile` 或作業排程器（LSF `bsub` / Slurm `srun`）。
- **Verdi (`verdi.py`)**：在通過核准後呼叫 `fsdbreport` 或 Verdi batch TCL 擷取指定時間窗訊號，並限制輸出行數。
- **Perforce (`perforce.py`)**：僅實作唯讀 `p4 -ztag status / changes / describe / filelog / annotate` 及經核准的 `p4 edit`；**保留 `submit()` 拋出 `ForbiddenOperation` 永不實作**。
- **ADO / JIRA (`ado.py`, `jira.py`)**：透過環境變數名稱（例如由執行環境讀取 `$JIRA_API_TOKEN`）連接企業內部 On-Premise 伺服器；若目標為外部雲端服務，必須先取得組織資安核准才可設定 `allow_external_upload: true`。

### 步驟 3.2：逐項開啟 `adapters_validated`
在目標 Linux 工作站完成單一轉接器的整合測試後，於 `dv_project.json` 僅開啟已驗證項目：

```json
{
  "adapter_mode": "real",
  "adapters_validated": {
    "vcs": true,
    "perforce": true,
    "verdi": false,
    "regression": false,
    "ado": false,
    "jira": false
  }
}
```

---

## 4. Gemini CLI 版本升級注意事項

升級 `@google/gemini-cli` 後，請在部署前於暫存目錄執行框架測試套件：
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
```
重點檢查 `gemini skills list --all`、`.gemini/agents/*.md` 嚴格 frontmatter 載入，以及 Policy Engine TOML 規則是否與新版本完全相容。
