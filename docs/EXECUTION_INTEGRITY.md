# 執行完整性與部署快照

這一輪改進針對「失敗卻回報成功」及「未驗證卻被當成已驗證」的邊界。
不增加無人值守改碼或自動提交功能。

## 任務、審查與續跑

- agent status=ok 才是成功；needs_input、blocked、failed 均保留原意。
- 任一必要任務未完成，run 不能 succeeded。被跳過或缺少的 review 不會自動 approve。
- 多個 reviewer 必須全部 approve；任一 reject 會拒絕本次結果。
- revise 會清空被要求修改的任務及其所有下游結果，防止沿用舊輸入的分析。
- task-specific timeout/retry 設定會寫入 graph；resume 會重試中斷的 running 任務。
- resume 保留已記錄的使用量，不恢復已中斷呼叫的預算保留。已成功的 run 不會重複呼叫模型。
- 未回傳使用量的逾時，或從 API 回應到持久化之間突然中止，仍可能有未知使用量；不宣稱可替代供應商帳務。

~~~bash
PYTHONPATH=src python3 -m dv_agent_platform resume <run_id>
# 上面只預覽，不呼叫模型。明確執行：
PYTHONPATH=src python3 -m dv_agent_platform resume <run_id> --execute
~~~

run/resume 只有 planned 或 succeeded 回傳 exit code 0。needs_input、blocked、
failed、needs_human、rejected_by_review 均回傳非零，供外部腳本可靠判斷。
現有 confirmed memory database 會透過 CLI 的 orchestrator provider 注入分析脈絡，
檢索仍受 project_id 和陳舊來源檢查約束。

## 證據與報告

同一 finding 的全部引文都會參與判斷。一筆有效引文不能掩蓋越界、缺失或不符引文。
有問題的 CONFIRMED finding 降為 HYPOTHESIS；越界引文使該 finding 權重為零。
報告顯示 epistemic_effective，保留 reported 標記在原始 state 以供追蹤。
再次驗證會讀取目前檔案內容，不會沿用同一 verifier 先前快取的舊內容。

引用存在與摘錄相符不代表推論必然正確；仍需 independent review 與實際 EDA 證據。

## Token 預算

每次模型呼叫前以原子 reservation 保留預估使用量。並行 admission 不會重複使用同一份
剩餘預算；呼叫成功或失敗都會釋放對應 reservation。真實使用量超限會如實記錄，
使 run 失敗並阻止後續任務。預算保留與實際帳務在 usage 裡分開顯示。

這是預估額度的 admission control，並非供應商 token 硬上限。
模型回覆、隱藏 context、工具呼叫成本可能高於預估；已在執行的其他呼叫也可能完成。
若需要帳務硬上限，必須另由公司模型 gateway/供應商配額限制。
預設 estimated output allowance 仍為 256 tokens，請依實際任務評估預算。

## MCP 與工單

工具呼叫會檢查必要參數與基本 JSON 型別；不存在的檔案、錯誤參數和工具例外會轉成
isError=true，錯誤文字遮蔽常見密鑰格式。後續 ping/tool call 可繼續，不終止整個 stdio server。
此 validator 是本專案 schema 使用的基本子集，不宣稱完整 JSON Schema 實作。

ADO/JIRA 真實服務連線尚未實作。已驗證旗標不是服務實作：real mode 查詢/寫入一律明確
UnsupportedOperation，不能回傳 mock 資料或虛構建立成功。dry-run 仍提供操作預覽，
mock mode 僅供流程測試。需要公司內部 API 時應另實作 site-specific adapter。

## 可重建的貼上部署包

scripts/build_paste_kit.py 從已提交的 git ref 打包完整來源，排除歷史 offline/ 傳輸內容，
避免新套件再次內嵌舊套件。包含原始碼、測試、文件與指定 Harness² 原始碼/授權，
每段、整包與每個檔案都有 SHA-256。可從已校驗的舊 kit 重用其 Harness² 來源快照。

~~~bash
python3 scripts/build_paste_kit.py \
  --framework-ref HEAD \
  --harness2-from-kit offline/paste-kit-d8b0c90 \
  --output offline/paste-kit-<new-source-sha>
~~~

來源 commit 與「發布部署包」commit 分開，來源版本可獨立追溯。發布後 source/main 更新
不會自動更新固定快照。用新版本部署時，請明確選用對應 kit 並重新檢視公司 profile。

## 驗證範圍

tests/test_execution_integrity.py 覆蓋阻擋、缺少審查、多審查、下游重算、
原子預算保留、超限紀錄、續跑使用量、混合引文、來源修改、MCP 存活、
未實作工單拒絕、契約型別與 CLI 行為。現有 E2E 使用離線替身，真實公司 EDA
與模型服務仍需現場驗證。
