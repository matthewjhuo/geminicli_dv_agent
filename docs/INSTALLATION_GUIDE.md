# 安裝與部署指南（Installation Guide）

本指南說明如何在 **Linux + VS Code Terminal** 環境中，以非破壞性、可回滾且零憑證暴露的方式部署 Agentic DV 框架。

---

## 1. 環境前置需求

1. **作業系統與終端機**：Linux（RHEL / Rocky / Ubuntu 等標準晶片研發工作站），於 VS Code 整合終端機操作。
2. **Gemini CLI**：建議版本 `>= 0.68.0`：
   ```bash
   gemini --version
   ```
3. **Python 執行環境**：`Python >= 3.11`（若需執行測試中的 `tomllib`；核心執行期僅依賴 Python 標準函式庫，無須額外 `pip install`）。
4. **API 金鑰設定（嚴禁寫入設定檔）**：
   透過 Shell 環境變數提供 `GEMINI_API_KEY`（請由組織核准之密鑰管理機制載入，切勿寫入 `settings.json`、`dv_project.json` 或版本控制）：
   ```bash
   export GEMINI_API_KEY="<YOUR_GEMINI_API_KEY_FROM_VAULT>"
   ```

---

## 2. 設定框架與專案環境變數

在專案 Shell 初始化腳本（例如工作區 `setup.sh`）中匯出路徑變數：

```bash
export DV_AGENT_FRAMEWORK_HOME="<PATH_TO_DV_AGENT_FRAMEWORK>"
export DV_AGENT_PLATFORM_PYTHONPATH="${DV_AGENT_FRAMEWORK_HOME}/src"
export DV_PROJECT_ROOT="<PATH_TO_YOUR_SOC_OR_IP_WORKSPACE>"
export DV_ADAPTER_MODE="mock"               # 未完成實機驗證前保持 mock
export DV_ALLOW_EXTERNAL_UPLOAD="false"     # 預設禁止向外部網路傳送設計資料
```

---

## 3. 非破壞性安裝流程（Dry-Run 優先 + 自動備份）

安裝腳本 `scripts/install.py` 預設為 **Dry-Run 唯讀預覽模式**，並且遵循以下保證：
- 若目標已存在 `GEMINI.md`，採用標記區塊（`<!-- BEGIN AGENTIC_DV_FRAMEWORK -->`）冪等合併，**保留您原有的規則內容**。
- 若目標已存在 `settings.json`，採用深度附加合併（Additive Merge），**保留既有 `mcpServers` 與 `hooks`**，並強制以無 BOM 之 UTF-8 寫入。
- 修改任何既有檔案前，自動於備份目錄產生副本與 `manifest.json`（含 SHA-256 雜湊），且絕不讀取或複製任何認證憑證檔。

### 步驟 3.1：安裝至驗證專案工作區（建議優先採用）

```bash
# 1) 預覽變更（不寫入磁碟）
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" --project-dir "${DV_PROJECT_ROOT}"

# 2) 檢視無誤後正式套用（自動建立備份並為 Linux hooks 設定 chmod +x）
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" --project-dir "${DV_PROJECT_ROOT}" --apply
```

### 步驟 3.2：（選用）合併全域 `~/.gemini/` 安全憲章

> **注意**：僅在您明確同意更新個人 `~/.gemini/GEMINI.md` 與 `~/.gemini/settings.json` 時執行：

```bash
# 1) 預覽全域合併
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" --global-gemini-dir "${HOME}/.gemini"

# 2) 核准後套用
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" --global-gemini-dir "${HOME}/.gemini" --apply
```

---

## 4. 啟用 Gemini CLI 工作區信任（Workspace Trust）

在 Gemini CLI `0.68.0` 中，基於安全性考量，**未受信任的工作區預設不會載入專案級 `.gemini/skills/`、`.gemini/agents/`、`.gemini/hooks/` 與 `.gemini/policies/`**。

首次進入 `${DV_PROJECT_ROOT}` 啟動 `gemini` 時，請於互動提示中選擇信任該工作區目錄（或確認 `${HOME}/.gemini/trustedFolders.json` 中對應路徑為 `"TRUST_FOLDER"`），隨後執行以下指令確認 8 個 DV 技能皆已就緒：

```bash
cd "${DV_PROJECT_ROOT}"
gemini skills list --all
```

---

## 5. 企業級不可覆寫安全政策部署（Admin Policy Tier）

Gemini CLI 的 Policy 優先序為：`預設 (Tier 1) < 擴充 (Tier 2) < 工作區 (Tier 3) < 使用者 (Tier 4) < 管理者 (Tier 5)`。
為確保 `p4 submit` 等永久禁令不會被個人層級的 `auto-saved.toml` 覆寫，建議 CAD / IT 管理團隊將硬性阻擋規則部署至系統層或透過啟動封裝傳入 `--admin-policy`：

```bash
sudo mkdir -p /etc/gemini-cli/policies
sudo cp "${DV_PROJECT_ROOT}/.gemini/policies/90-dv-hard-denies.toml" /etc/gemini-cli/policies/
```

---

## 6. 一鍵回滾（Rollback）

若需還原某次安裝修改過的既有檔案，直接指定該次安裝輸出的 `backup_dir` 執行：

```bash
python3 "${DV_AGENT_FRAMEWORK_HOME}/scripts/install.py" \
  --rollback "${DV_PROJECT_ROOT}/.dv_agent/backups/project-<YYYYMMDD-HHMMSS>"
```
