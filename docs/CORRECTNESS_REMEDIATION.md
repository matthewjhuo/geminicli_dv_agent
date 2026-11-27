# 正確性修正與驗證邊界

基準為 main 3dcc9f5；本次整合 execution-integrity 修正並處理新版波形、記憶與評測問題。沒有呼叫 live Gemini 或公司 EDA，也沒有修改既有 TB。

## 執行與審查

agent 只有 ok 才算成功；needs_input、blocked、failed 會阻擋下游。必要 review 缺少或失敗不會預設 approve，所有 reviewer 必須 approve。revise 會使受影響任務及所有下游失效。resume 預設預覽，--execute 才呼叫 backend；恢復 retry、timeout 與已記錄用量。預算採原子 estimated reservation，實際超限會如實記帳。模型逾時且沒有回傳供應商用量時仍有未知使用量，不能當成帳務硬上限。

## 波形

- 使用正確 percent-encoded SQLite 唯讀 URI，支援路徑中的 #、%、空白與非 ASCII。
- FSDB samples 保留小數 ns；MCP time/start_time/end_time/violation_time 接受有限 number，拒絕 bool、NaN、Infinity 與負時間。
- SQLite REAL 是浮點時間；極大時間戳或極高精度需另接精確 tick 表示，不能宣稱任意精度。
- 只有明確具名 clock 才可能在啟用 prune_clocks 時剪除，不再依資料翻轉頻率猜 clock。需要 clock 邊緣時請 prune_clocks=false。
- 常數保留 first_time，首次觀測前回傳未知。相同時間的值查詢採最後輸入事件。值在搜尋窗開始已成立時可回傳 start_time。
- 匯入先寫同目錄暫存 DB，檢查 VCD 來源未變，再原子發布；預設拒絕覆寫，Python API 明確 overwrite=True 才可取代。MCP 不開放覆寫；adapter/MCP 索引寫入僅限 state_dir/waveforms 下的 .db，不能建立 RTL/TB 檔案。
- index_vcd_to_sqlite 標為寫入工具；它只索引本地資料，不會啟動 EDA。查詢工具維持唯讀。
- 活動率是「非 clock 訊號至少有一次 transition 的比例」，不代表 EDA bit toggle coverage。刪除無依據的 100% preservation 宣告。
- backward_trace_window 只回傳事件時間窗；causal_timeline 保留為舊欄位別名，新增 event_timeline 與限制說明，不提供因果證明。
- VCD 支援 scalar/vector/real token 與 dump/comment 指令、同一行多事件。enddefinitions 後的 body 須另起一行。不支援的 token 明確報錯；不是完整 IEEE parser。
- 舊 waves.db 沒有 first_time 欄位時請從原始波形重建到新檔案。

## 合成評測

mutation ladder 是自訂 Python FIFO demo，使用已知 SVA 文字特徵，不會模擬輸入 RTL、執行 SVA 或 formal proof。註解先移除；所有結果包含 synthetic=true、execution_mode=synthetic_python 與 limitations。synthetic_ready 表示 demo 門檻通過；signoff_ready 永遠 false，沒有 SIGNOFF_READY 成功標籤。內建 scoreboard 也是 Python 模型。Cover hits 是內建情境命中，並非求解使用者 cover expression。

## 記憶與核准

frozen 模式不建立 DB、不遷移 schema、不寫 stale 狀態；仍會檢查來源並排除 stale 檢索。新 schema 必須先由 evolving 模式遷移。

distill_procedural_skill 必須提供 artifact 與 sha256；JSON 紀錄須含 record_version=dv-eda-result/v1、project_id、tool、check_name、passed=true、returncode=0、timed_out=false、execution_mode=real、revision、source_hashes。核對本地檔案範圍、雜湊與來源當前內容；verify 和 stale 檢查會重核。這是紀錄完整性驗證，沒有工具簽章，不能證明紀錄是由 EDA 產生；仍需可信公司 wrapper 與人工確認。一般 procedural candidate 可保留未驗證想法，但不能因此宣稱 EDA-PASS。

human: 字串不再足以確認記憶。CLI 讀取 DV_APPROVAL_FILE；預設沒有核准即拒絕。記憶核准檔必須位於專案外，且記錄須包含：

```json
{"approvals":[{"approval_id":"APR-MEM-1","category":"memory_promotion","action":"confirm_memory","approver":"human:dv_lead","expires_at":1893456000,"scope":{"entry_id":"REPLACE_ENTRY_ID","project_id":"REPLACE_PROJECT_ID"}}]}
```

由操作人員或公司服務建立與保護此檔；不要由 agent 自行寫入。expiry 需為未來時間，scope 必須精確匹配 entry/project。OS 權限或公司 gateway 才是信任根；Python 與 hooks 不能保護對 agent 同樣可寫的任意檔案。`--approver` 只做核准結果一致性核對。

統計記憶由 adapter 預設建立在專案 state_dir/tool_stats.db，成功／失敗皆會記錄。零 exit code 中的 UVM_ERROR/FATAL 事件仍算失敗，零錯誤 summary 排除。建議是 advisory，不自動重試或切換公司工具；retry streak 目前是 adapter instance 的本地狀態，歷史事件保存在 SQLite。

## Guard、Wiki 與外部服務

每次 Harness² candidate execute 都檢查基本安全與新增 [iff] 規則；評分第一層失敗時不給語意分。Soft-HURT API 預設鎖定已掌握項目，拒絕非有限／越界分數與不匹配的評測集合。候選 promotion 與 regret 是選配 API，未替代外部 scheduler 的採用政策。Wiki 是普通本地檔案，沒有不可竄改、不可回滾或跨 process 並行交易保證；需要時由公司外部儲存提供。

ADO/JIRA real 模式明確 UnsupportedOperation，不回傳虛構 updated/created。adapters_validated 只代表現場驗證開關，不能把未實作服務變成可用。

allow_external_upload=false 只限制平台明確管理的外傳操作，不阻止 Gemini 已核准的模型請求。native Gemini 格式沿用先前相容性檢查，沒有聲稱已在公司版本重新驗證。

## 可重現離線檢查

```bash
export PYTHONPATH=src
export PYTHONUTF8=1
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 -m unittest discover -s tests_e2e -p 'test_*.py' -q
```

測試中的 EDA JSON 與 subprocess shim 都是合成 fixtures，不代表跑過真實 EDA。最新統計與離線包來源見 IMPLEMENTATION_STATUS.md 與 offline/README.md。
