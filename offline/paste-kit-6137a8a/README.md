# 完整 Gemini CLI DV 框架貼上部署包

固定來源版本：`6137a8a115c53bf70976d2d5e7044da17d3f046c`，共 **193 個來源檔案**。

包含完整 L1–L5、測試、文件和 Harness² 原始碼。排除歷史 offline/ 傳輸包以避免遞迴打包。
不含 Gemini CLI/EDA 可執行檔、license、憑證或 Harness² 額外 bridge/render 依賴。
Harness² 授權見 [LICENSE-HARNESS2](LICENSE-HARNESS2)，逐檔雜湊見還原後 TRANSFER_MANIFEST.json。

## 單檔貼上

1. 開啟 [ALL_IN_ONE.sh](ALL_IN_ONE.sh)，按 Raw，全選複製。
2. 在公司 server VS Code 新增 dv_restore.sh，貼入全部文字，以 UTF-8 / LF 儲存。
3. 在 server terminal 執行 `bash dv_restore.sh`。
4. 出現 `RESTORE OK: 193 files verified` 後，依 [驗證與安裝](100_VERIFY_AND_INSTALL.txt) 設定。
5. 編輯公司 profile，預覽安裝後套用；從 TB 專案根目錄啟動 `gemini`。

## 分段貼上

若剪貼簿限制長度，先進入 Bash，依下表把每個檔案的全部內容貼到同一個 terminal。
每段應顯示 Part n/N OK；失敗只需重貼同編號。終端機停在 > 時按 Ctrl+C，再完整重貼該段。

| 順序 | 檔案 |
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
| 11 | [11_PART_011.txt](11_PART_011.txt) |
| 12 | [12_PART_012.txt](12_PART_012.txt) |
| 還原 | [99_RESTORE.txt](99_RESTORE.txt) |
| 驗證與安裝 | [100_VERIFY_AND_INSTALL.txt](100_VERIFY_AND_INSTALL.txt) |

完整功能含 Harness² 請用 Python 3.12/3.13；核心框架與測試需 Python 3.11+。
解碼不需連網或 pip。Gemini CLI 仍須能存取公司核准的模型服務。
還原器先核對整包與逐檔 SHA-256，才寫入新目錄；同名目錄存在時會停止。
dry_sim 僅為合成流程檢查。真實 EDA、排程器與模型效果仍需現場驗證。
Windows 請用短暫存/輸出路徑以避開 WinError 206；本部署包主要供 Linux server 使用。

壓縮內容 SHA-256：`85152534f87fb6ce499ae4a7c58a44d0f3838982d5f2ed8915a4dd1dc16ee1eb`。
