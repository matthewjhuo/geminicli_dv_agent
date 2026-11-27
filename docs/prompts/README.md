# Gemini CLI prompts for company servers

兩份英文 prompt，依序使用。開啟文件後按 **Raw**，只複製 `text` 區塊內的完整內容，貼到公司 server 的 Gemini CLI。

| 順序 | Prompt | 使用前提與用途 |
| --- | --- | --- |
| 1 | [從零建立完整 Framework](BUILD_FRAMEWORK_FROM_SCRATCH_EN.md) | Server 沒有框架檔案、不能連 GitHub；由 Gemini CLI 實作、測試、交付平台。 |
| 2 | [接入現有 Testbench 並開始驗證協助](INTEGRATE_EXISTING_TESTBENCH_EN.md) | Framework 已存在；保留既有 TB，接入專案設定並完成初次唯讀分析。 |

兩份 prompt 均要求使用本機能力、保護憑證、區分 mock/synthetic/real 結果，並在真實 EDA、額外 live backend 呼叫或來源修改前取得相應核准。

這些是給 Gemini CLI 的工作指令，不代表執行後必然完成、也不代表 repository 已具備所有指定功能。實際結果須以 server 上的程式、測試、相容性文件與狀態紀錄為準。第二份 prompt 要求讀取實際安裝版本，不能照猜測的 CLI/schema 安裝。

已發布的 offline kit 是固定快照，不會自動包含這次新增的 prompt 文件；可以直接從 GitHub 複製本文所連結的內容。
