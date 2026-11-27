# 公司 server：完整 Gemini CLI DV 框架貼上部署包

適用於公司 server 無法連 GitHub、不能直接傳檔，但可以從外部電腦複製文字到 VS Code 的情境。所有程式資料已包含在文字內，server 不需要下載任何檔案。

這是固定的 `d8b0c906f212dcd59b6ce0fe06c09a25c5e1a3a3` 框架快照，含完整 L1–L5、skills、agents、commands、MCP、Python 平台、測試、文件和 Harness² 核心原始碼，共 174 個來源檔案。未來 main 的修改不會自動進入這份固定快照。

Harness² 來自本機原始碼快照；其 Apache-2.0 授權見 [LICENSE-HARNESS2](LICENSE-HARNESS2)，逐檔 SHA-256 在還原後的 TRANSFER_MANIFEST.json。這份 bundle 不包含 Gemini CLI、商用 EDA 工具、license 或憑證，也不包含 Harness² render/bridge 額外選配依賴。

## 建議方式：複製一個檔案

1. 在可連 GitHub 的電腦開啟 [ALL_IN_ONE.sh](ALL_IN_ONE.sh)，按 **Raw**，全選並複製完整文字。
2. 用 VS Code Remote SSH 連公司 server，在非生產專案目錄新建 `dv_restore.sh`，貼入文字，以 **UTF-8 / LF** 儲存。
3. 在公司 server terminal 執行：

   ```bash
   bash dv_restore.sh
   ```

4. 成功時會顯示 `RESTORE OK: 174 files verified`。還原器會驗證段落、整包 SHA-256 和每個來源檔案，不覆寫既有同名還原目錄。
5. 開啟 [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt)，依序設定環境、跑離線測試、修改公司 profile、預覽安裝，再套用到既有 TB。

整套含 Harness² 請用 Python 3.12 或 3.13；核心框架和完整測試需 Python 3.11+。沿用公司已核准且可正常使用的 Gemini CLI 登入，不要把憑證寫進任何設定檔。

## 若剪貼簿限制單次文字長度：分 10 段

先在公司 terminal 執行 `bash`，以下各段都貼到同一個 terminal。每個檔案請複製全部文字，不只複製 Base64 部分。

| 順序 | 要複製貼上的內容 |
| --- | --- |
| 開始 | [00_START.txt](00_START.txt) |
| 1 | [01_PART_001.txt](01_PART_001.txt) |
| 2 | [02_PART_002.txt](02_PART_002.txt) |
| 3 | [03_PART_003.txt](03_PART_003.txt) |
| 4 | [04_PART_004.txt](04_PART_004.txt) |
| 5 | [05_PART_005.txt](05_PART_005.txt) |
| 6 | [06_PART_006.txt](06_PART_006.txt) |
| 7 | [07_PART_007.txt](07_PART_007.txt) |
| 8 | [08_PART_008.txt](08_PART_008.txt) |
| 9 | [09_PART_009.txt](09_PART_009.txt) |
| 10 | [10_PART_010.txt](10_PART_010.txt) |
| 還原 | [99_RESTORE.txt](99_RESTORE.txt) |
| 驗證與安裝 | [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt) |

每段應顯示 `Part n/10 OK`。若某段失敗，重新貼同編號即可。若終端機停在 `>`，按 Ctrl+C，重新貼該段完整內容。先前成功的段落不需要重貼。

## 還原後的位置

```text
$HOME/agentic-dv-paste-d8b0c90/agentic-dv-offline-d8b0c90/
├── dv-agent-framework/
├── harness2/
└── TRANSFER_MANIFEST.json
```

環境變數設定後，修改 company_profile.json 的 DUT、TB/RTL 路徑、filelist、clock/reset 和 compile/run 指令。用 scripts/install.py 先預覽，再加 --apply。最後進入 **TB 專案根目錄**啟動 `gemini`，讓它載入安裝後的 GEMINI.md 與 .gemini/。

第一次可以請 Gemini：

```text
先閱讀本專案 GEMINI.md，確認可用的 DV skills、agents 與 MCP 工具。
唯讀檢查現有 testbench，整理 top、interface、agent、sequence、scoreboard、filelist
和編譯流程。先列出缺少的資訊，不修改檔案。
```

## 驗證結果與範圍

已從這些貼上文字還原所有 174 個來源檔案並核對雜湊。還原後 Windows / Python 3.13 測試：單元測試 60 項，59 通過、1 項原生 Gemini 發現測試略過；離線 E2E 73 項通過。使用隨包真正 Harness² 核心（非 test double）的 dry_sim 完成 4/4 黃金案例。

Windows 沙箱的深 TEMP 路徑曾觸發 WinError 206；改用一般短暫存/輸出路徑後通過。部署目標為公司 Linux server。

dry_sim 是合成流程驗證，不呼叫模型。未在公司真實 EDA/排程器驗證。安裝後保持 mock/dry_run，逐一驗證 adapter 再啟用。Python 自動編排預設唯讀 plan 模式，互動式 TB 修改仍需核准。

完整步驟亦見 [使用說明.txt](使用說明.txt)。
