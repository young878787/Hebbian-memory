# NO_MEMORY 語義保留、Atomic Completeness 與狀態化修正計畫 v0.1

日期：2026-07-21  
範圍：`D:\Hebbian-memory` live extraction → staging/resolution → canonical claim → retrieval/answer → deterministic evaluation  
本文件狀態：修正計畫；尚未修改 runtime code

## 1. 結論

目前失敗不是單一 prompt 誤判，而是四個契約缺口串在一起：

1. `NO_MEMORY` 被當成「不是確定偏好」的出口，導致 research intent、未決狀態、過去考慮與 durable event 被丟棄。
2. extraction 雖允許一則 message 產出多筆 memory，卻沒有「來源中有哪些 atomic assertions、每一筆如何處置」的完整性契約，因此 `msg-004` 只抽一筆仍可合法通過。
3. `ExtractedMemory` 沒有來源 modality／temporal scope；`write_ingestion()` 依賴 `Memory.status` 的 `active` 預設值，canonical `MemoryClaim.modality` 也永遠落到 `asserted` 預設值。
4. root E2E 只檢查每則訊息有 outcome、回答存在、citation 屬於 selected memories；它沒有檢查 fixture 所要求的 atomic facts、status、`must_include`、`must_not_primary` 與 answerability，因此語義遺失仍可得到流程 `PASS`，AI judge 也會替缺資料的回答誤判通過。

本次應做的是一個 extraction contract v2，而不是只把 `insufficient_assertion` 的 prompt 文字放寬。核心原則是：

> 不確定不是沒有記憶；歷史不是沒有記憶；研究問題不是已證實事實，但「使用者正在研究／關心」本身是 durable event。

## 2. 現況證據與失敗鏈

### 2.1 最新 artifact 快照

目前 `results/pipeline/extraction.json` 與 `extraction_coverage.json` 顯示：

- 60 則輸入均有 outcome。
- 37 則 `EXTRACTED`、23 則 `NO_MEMORY`、0 則 `FAILED`。
- 只產生 45 筆 memory candidates。
- `NO_MEMORY` 原因為：
  - `insufficient_assertion`: 14
  - `unsupported_role_content`: 5
  - `non_durable_chitchat`: 4

coverage 因為只驗 message ID 完整性而通過，但以下關鍵語義已遺失：

| Message | 原文 | 現況 | 應有結果 |
|---|---|---|---|
| `msg-003` | 最近開始研究 CMP 170HX，還不確定改裝風險。 | 整則 `NO_MEMORY / insufficient_assertion` | 研究事件 `active` + 風險未確認 `uncertain` |
| `msg-004` | 我需要大顯存…也想知道 CMP 170HX 能否解鎖… | 只抽「需要大顯存」 | 再抽一筆 CMP 170HX research question/event |
| `msg-045` | 規劃旅行時會拆成交通、住宿與備案 | `NO_MEMORY` | durable planning preference，`active` |
| `msg-046` | 曾考慮安排花蓮看海的旅行 | `NO_MEMORY` | past consideration，historical，fixture 預期 `archived` |
| `msg-050` | 尚未決定閱讀時要用紙本或電子書 | `NO_MEMORY` | unresolved decision，`uncertain` |

### 2.2 實際 runtime 丟失位置

```text
extract_message()
  → MessageExtractionOutcome
  → workflow.ingest()
      if outcome.status != EXTRACTED:
          continue
  → write_ingestion()
  → resolve / persist
```

`workflow.py` 的 skip 本身沒有錯；真正錯的是 upstream 將仍有 durable semantics 的訊息分類為 `NO_MEMORY`。真正的 `NO_MEMORY` 應繼續跳過，不應用 fallback 亂造 memory。

### 2.3 Prompt 與原計畫直接衝突

`ingestion/extractor.py` 現在明示「使用者尚未決定」要回 `NO_MEMORY`；但既有逐訊息計畫要求 uncertain／未決內容以 modality 或 confidence 表達，不可直接變 active preference。這代表設計意圖已存在，runtime contract 沒有接上。

### 2.4 狀態被預設值覆蓋

- `ExtractedMemory` 沒有 `modality`、`temporal_scope` 或 initial status 欄位。
- `write_ingestion()` 建立 `Memory` 時沒有傳 `status`，因此一律使用 `active`。
- cross-message `_create_memory()` 先設 `uncertain`，但 AI resolver 的 `CREATE` 又強制設為 `active`。
- `memory_claims.modality` 欄位已存在，但 `ensure_claim()` 未接收或寫入 modality，實際都使用 `asserted` 預設值。
- `ClaimEvidence` 已有 `evidence_start` / `evidence_end`，但目前 writer 沒有填，且 extractor 把每一筆 evidence 都覆寫成整則訊息，無法稽核哪一段支撐哪個 atomic fact。

### 2.5 評估 gate 無法阻止 false green

目前 root E2E 的 deterministic checks 只證明：

- 有 selected memory。
- 有 answer。
- citations 都在 selected 集合內。
- provider/judge contract 可解析。

它沒有證明「該抽出的記憶已抽出」。最新 artifacts 中共有 23 題 `answer.answerable` 與 reference expectation 不一致，其中 17 題仍被 AI judge 判為 `PASS`。因此 AI judge 只能是次級語意評分，不能取代 source-to-memory hard gate。

## 3. 修正邊界與不可妥協 invariant

### 3.1 本次要修正

- `NO_MEMORY` 的語義邊界與 reason code。
- 一則 message 多個 atomic assertions 的完整性與可稽核性。
- asserted、question、uncertain、considered 與 current／historical 的明確表示。
- initial memory status 的 deterministic mapping。
- resolver 對 source state 的保留，不得把 `uncertain`／`archived` 無條件升成 `active`。
- claim/evidence dual-write 的 modality、temporal scope 與 exact span。
- live extraction 與 60 題 E2E 的 deterministic semantic gates。
- fixed latest-only artifacts 的同一執行 lineage 與 atomic publish。

### 3.2 本次不做

- 不用 keyword regex 直接建立正式記憶。
- 不讓 AI 直接決定 `superseded`；它仍只能由 resolution 關係產生。
- 不把所有過去式一律 archive。
- 不以降低 retrieval threshold 掩蓋 upstream 記憶遺失。
- 不用 AI judge 的 PASS 覆蓋 deterministic failure。
- 不刪除既有 source/evidence 或舊 migration；採 additive、forward-only migration。

### 3.3 核心 invariant

1. `NO_MEMORY` 只表示「此 message 的所有 atomic assertions 都沒有 durable memory value」。
2. message 只要有一個 durable assertion，message outcome 就必須是 `EXTRACTED`；其他被略過的 clause 仍須留下 assertion-level reason。
3. uncertainty 是 claim state，不是 extraction failure。
4. research question 的「答案」可未知，但「使用者正在研究／關心」是可保存的 asserted event。
5. historical 是時間範圍；archived 是 lifecycle／retrieval visibility。兩者不得混為同一欄位。
6. `superseded` 只能由 resolver 建立，不得由 extractor 直接指定。
7. 每筆 memory 必須回到唯一 source message 的一段連續逐字 evidence；完整原文仍以 `source_messages` 為權威來源。
8. deterministic semantic gate 失敗時，整體 quality 必須 FAIL；AI judge 不得改寫此結果。

## 4. 目標 extraction contract v2

### 4.1 由 message-level 結果改為 assertion inventory

新增 `message-extraction-outcome-v2`，保留 message outcome，但加入來源內 atomic assertion inventory：

```json
{
  "schema_version": "message-extraction-outcome-v2",
  "source_message_id": "msg-004",
  "status": "EXTRACTED",
  "assertions": [
    {
      "assertion_id": "a1",
      "evidence_quote": "我需要大顯存來測試本機語言模型",
      "disposition": "EXTRACTED",
      "memory_candidate_ids": ["m1"],
      "reason_code": null
    },
    {
      "assertion_id": "a2",
      "evidence_quote": "也想知道 CMP 170HX 能否解鎖更大顯存",
      "disposition": "EXTRACTED",
      "memory_candidate_ids": ["m2"],
      "reason_code": null
    }
  ],
  "entities": [],
  "memories": [],
  "provider_attempts": 1,
  "error": null
}
```

新增型別：

```text
AssertionDisposition = EXTRACTED | NO_MEMORY

AtomicAssertion
  assertion_id
  evidence_quote
  disposition
  memory_candidate_ids[]
  reason_code?
```

cross-field validators：

- 每一筆 memory candidate 必須且只能被至少一個 `EXTRACTED` assertion 引用。
- `EXTRACTED` assertion 至少引用一筆 memory，不得有 reason code。
- `NO_MEMORY` assertion 不得引用 memory，且 reason code 必填。
- message `status=EXTRACTED` iff 至少一個 assertion 為 `EXTRACTED`。
- message `status=NO_MEMORY` iff assertions 非空且全部為 `NO_MEMORY`。
- `FAILED` 不得帶 assertions/entities/memories。
- 每個 `evidence_quote` 必須是 source content 的連續逐字片段。
- assertion IDs、entity candidate IDs、memory candidate IDs 在單一 outcome 內唯一。

這個 inventory 不保證 AI 絕不漏列 clause，但它讓漏列變得可觀測，並允許 fixture oracle 對 expected assertion count 與 semantics 做 hard gate。

### 4.2 `ExtractedMemory` 新增來源語義欄位

建議使用兩個正交欄位，不讓一個 `status` 同時承擔真假、時間與 lifecycle：

```text
modality:
  asserted     # 來源明確陳述事實、偏好、事件或意圖
  question     # 使用者正在研究／關心一個尚無答案的問題
  uncertain    # 來源明確表示尚未確認、尚未決定或信念不確定
  considered   # 曾考慮，但未表達為目前承諾

temporal_scope:
  current
  historical
  unknown
```

另加：

```text
evidence_quote       # exact source span，provider 提出、server 驗證
```

`memory_type` 繼續表示 `event / preference / decision / character_fact`；它不取代 modality。

### 4.3 Initial status 必須由 application policy 推導

AI 不直接輸出 database lifecycle status。新增純函式，例如：

```text
derive_initial_state(modality, temporal_scope, memory_type)
  → status
  → lifecycle_action?
  → reason
```

基準 mapping：

| modality | temporal_scope | 初始 status | 語義 |
|---|---|---|---|
| `asserted` | `current` | `active` | 當前明確狀態／事件／偏好 |
| `question` | `current` | `active` | 「正在研究／關心」為 active event；不得把問題答案當真 |
| `uncertain` | `current`/`unknown` | `uncertain` | 未確認、未決定、互斥選項未解析 |
| `considered` | `historical` | `archived` | 已過去的考慮，保留歷史但不當 current primary |
| `asserted` | `historical` | `active` + historical scope | 已發生的可靠歷史事件，不因過去式自動 archive |

特別規則：

- `question` 的 content 應寫成「使用者關心／正在研究 X 是否 Y」，不能寫成「X 可以 Y」。
- `considered + historical → archived` 必須同時寫入 `lifecycle_decisions`，reason 固定為 `source_explicit_past_consideration`，不能只有 status mutation。
- 一般 historical event（例如「曾排查 PostgreSQL」）仍可作可靠歷史事實；不得因 `曾` 字一律 archive。
- `superseded` 不在此 mapping 中。

## 5. `NO_MEMORY` 新邊界

### 5.1 v2 可產生的 reason codes

```text
non_durable_chitchat
external_noise
unsupported_role_content
non_propositional_fragment
```

處理原有 reason：

- `insufficient_assertion`：v2 停止產生；只保留 v1 artifact 讀取相容。以更窄的 `non_propositional_fragment` 取代。
- `duplicate_surface_form`：移出 extraction。逐訊息 extractor 沒有 DB context，不能知道是否 duplicate；重複應由 deterministic resolver 轉為 `MERGE_PROVENANCE`。

### 5.2 禁止判為 `NO_MEMORY` 的正例

以下只要主詞邊界清楚，必須抽取：

- 已開始／正在進行的研究、比較、排查、規劃。
- 想知道、關心、正在確認的研究問題。
- 尚未決定、尚未確認、仍有風險的 unresolved state。
- 曾考慮、曾規劃、已完成、已取消等歷史事件。
- 可重複使用的工作方法、規劃原則與偏好。

### 5.3 合法 `NO_MEMORY` 例子

- 純問候、語助詞、無可保存命題的短句。
- 只有外部論壇／朋友的內容，且沒有形成使用者自己的研究意圖或決策脈絡。
- 無法辨識主詞，且保存後會錯誤歸屬給 user／character。
- 純殘句，沒有事件、狀態、意圖、偏好或可追蹤問題。

## 6. 五個關鍵訊息的目標輸出

### 6.1 `msg-003`

必須產生兩筆：

1. `使用者開始研究 CMP 170HX`
   - type: `event`
   - modality: `asserted`
   - temporal_scope: `current`
   - status: `active`
2. `使用者尚未確認 CMP 170HX 的改裝風險`
   - type: `event`
   - modality: `uncertain`
   - temporal_scope: `current`
   - status: `uncertain`

### 6.2 `msg-004`

必須產生兩筆：

1. `使用者需要大顯存來測試本機語言模型`
   - type: `preference`
   - modality: `asserted`
   - status: `active`
2. `使用者關心 CMP 170HX 能否解鎖更大顯存`
   - type: `event`
   - modality: `question`
   - status: `active`

第二筆的 active 表示 research interest 目前成立，不表示 CMP 170HX 已被證實可以解鎖。

### 6.3 `msg-045`

- `使用者規劃旅行時會拆成交通、住宿與備案`
- type: `preference`
- modality: `asserted`
- temporal_scope: `current`
- status: `active`

### 6.4 `msg-046`

- `使用者曾考慮安排花蓮看海的旅行`
- type: `event`
- modality: `considered`
- temporal_scope: `historical`
- status: `archived`
- 必須有 source-derived lifecycle audit；current query 不得 primary，historical query 可取回。

### 6.5 `msg-050`

- `使用者尚未決定閱讀時要用紙本或電子書`
- type: `decision`
- modality: `uncertain`
- temporal_scope: `current`
- status: `uncertain`
- 回答不得選紙本或電子書任一側當確定偏好。

## 7. Persistence 與 migration 設計

### 7.1 優先重用既有 canonical 欄位

`memory_claims.modality` 已存在，不另建重複欄位。修正 writer 讓它不再永遠使用 `asserted` 預設。

建議新增 migration `20260721_0007_extraction_semantics.py`：

- `memories.modality VARCHAR(16) NOT NULL DEFAULT 'asserted'`
- `memories.temporal_scope VARCHAR(16) NOT NULL DEFAULT 'unknown'`
- `memory_claims.temporal_scope VARCHAR(16) NOT NULL DEFAULT 'unknown'`
- modality check constraint：`asserted/question/uncertain/considered`
- temporal scope check constraint：`current/historical/unknown`
- 依 retrieval 查詢需求建立 `(namespace_id, temporal_scope, status, occurred_at)` index。

`MemoryCandidate.extraction_payload` 與 `MessageExtractionOutcome.payload` 已是 JSONB，可直接保留 v2 assertion inventory，不需要為每個 staging 欄位加 column。

### 7.2 Legacy backfill

backfill 必須保守，不從舊 content 猜狀態：

- 既有 rows：`modality='asserted'`、`temporal_scope='unknown'`。
- 既有 `Memory.status` 原樣保留。
- 既有 `uncertain` 不自動標成 `modality='uncertain'`，因為它可能來自 contradiction resolver；標成 `unknown` provenance 或只保留 status。
- 既有 `archived` 不自動宣稱是 `considered`；archive 可能來自 lifecycle policy。
- backfill 先輸出 report，再在 dedicated test namespace 驗證；不改 production-like namespace。

### 7.3 Claim/evidence writer

調整 `ensure_claim()`：

- 明確接收 `modality`、`temporal_scope`、`evidence_quote`、`evidence_start`、`evidence_end`。
- 建立 claim 時寫入 modality/temporal scope/status，不依 DB default。
- merge provenance 時只新增 evidence，不偷偷把原 claim status 升為 active。
- `ClaimEvidence.evidence_text` 寫 exact quote；完整來源仍由 `(namespace_id, source_message_id)` 回到 `source_messages.content`。
- quote 必須先由 server 對原文計算 offset；找不到、越界或不連續即整個 message outcome validation fail。

### 7.4 Legacy `Memory` 與 canonical `MemoryClaim` 一致性

dual-write 階段每次 transaction 結束前檢查：

```text
Memory.status == MemoryClaim.status
Memory.modality == MemoryClaim.modality
Memory.temporal_scope == MemoryClaim.temporal_scope
Memory.source_message_ids 包含全部 ClaimEvidence.source_message_id
```

任何 mismatch 應 rollback 該 candidate，不可只寫其中一側。

## 8. Runtime 接線修正

### 8.1 `ingestion/contracts.py`

- 新增 `MemoryModality`、`TemporalScope`、`AssertionDisposition`、`AtomicAssertion`。
- 建立 v2 outcome 與 validators。
- v1 僅保留 artifact/replay 讀取，不再作 live provider response schema。
- `extraction_coverage()` 增加 assertion 與狀態統計。

### 8.2 `ingestion/extractor.py`

- prompt 改為先列 atomic assertions，再逐筆決定 extract/skip。
- 移除「尚未決定 → NO_MEMORY」。
- 加入五個正反例，但不放 fixture external IDs。
- provider 提出 `evidence_quote`；server 驗證並解析 offset。
- `_attach_source_evidence()` 不再把每筆 atomic evidence 覆寫成全文；全文 provenance 留在 source message。

### 8.3 `ingestion/workflow.py`

- 真正 message-level `NO_MEMORY` 仍跳過 writer。
- `EXTRACTED` 即使同時包含 assertion-level `NO_MEMORY` 也要寫入所有 extracted memories。
- `FAILED` 或 assertion contract invalid 必須令 coverage FAIL。
- 先完整 persist outcome audit，再進 candidate staging，維持故障可追蹤性。

### 8.4 `ingestion/service.py`

- 每筆 candidate 先呼叫 `derive_initial_state()`。
- 建立 `Memory` 時明確傳 `status/modality/temporal_scope`。
- normalized payload 保存 state policy version 與 derivation reason。
- archived source state 同 transaction 追加 lifecycle decision。

### 8.5 `ingestion/resolution_workflow.py`

- `_create_memory()` 不再先 hard-code `uncertain` 再由 `CREATE` 強制改 active。
- `CREATE` 保留 deterministic initial state。
- `MERGE_PROVENANCE` 不得因新 evidence 自動將 existing `uncertain/archived` 升 active。
- 若同 canonical claim 出現不同 modality/temporal scope：
  - 完全相容才 merge。
  - 可判定新舊狀態才走 SUPERSEDE。
  - 無法判定則 DEFER，不能用 max confidence 掩蓋狀態差異。

### 8.6 `persistence/resolutions.py`

- `SUPERSEDE` 可建立 active current state，但只能在 validated state relation 下執行。
- `CONTRADICT` 維持 uncertain 邏輯。
- source-derived archived memory 不因普通 `CREATE` 變 active。
- 每次 state change 同步 claim view 與 audit ledger。

## 9. Retrieval 與回答語義

### 9.1 Scope-aware retrieval

`status` 與 `temporal_scope` 一起參與 score/policy：

| Query scope | active/current | active/historical | uncertain | archived |
|---|---:|---:|---:|---:|
| current | 正常 | 降權 | 可作 uncertainty context | 不得 primary；必要時只作背景 |
| historical | 正常 | 正常 | 可取回並標示不確定 | 可取回 |
| general | 正常 | 小幅降權 | 可取回但不得改寫成確定 | 強降權且不得因 Hebbian bonus 成為 primary |

這一階段不先調整向量 threshold；先確保正確 memory 存在且狀態正確，再校準 retrieval。

### 9.2 Answer contract

- `modality=question`：回答「使用者曾／正在研究該問題」，不可回答問題內容已成立。
- `modality=uncertain`：答案必須保留「尚未確認／尚未決定」。
- `status=archived`：current query 不得表述為目前計畫；historical query 可說明曾考慮。
- selected memory 只有 uncertain/archived context 時，answerable 可為 true，但回答必須是 state-aware，而不是錯誤 no-answer 或確定事實。

## 10. Deterministic evaluation 修正

### 10.1 新增 source-to-memory extraction oracle

新增 `data/fixtures/live_extraction_expectations.jsonl`，以 `source_message_id` 為 key，不依賴 runtime memory UUID 或 provider candidate ID。

每個 expected atomic claim 至少描述：

```text
source_message_id
expected_outcome
expected_atomic_count
required_content_terms
required_entity_names
memory_type
modality
temporal_scope
expected_status
forbidden_assertions[]
```

第一批 hard gate 固定涵蓋 `msg-003/004/045/046/050`，之後擴到全部 60 則 durable/NO_MEMORY 邊界。

### 10.2 Extraction hard gates

```text
message outcome coverage == 60/60
failed outcomes == 0
expected atomic claims missing == 0
unexpected active claims == 0
evidence span violations == 0
state mapping mismatches == 0
claim/view dual-write mismatches == 0
```

對關鍵訊息的明確 gate：

- `msg-003` memory count 至少 2，含 CMP research active 與 risk uncertain。
- `msg-004` memory count 至少 2，CMP entity/topic 不得遺失。
- `msg-045` 必須 active。
- `msg-046` 必須 historical + archived，且有 lifecycle audit。
- `msg-050` 必須 uncertain。

### 10.3 E2E query hard gates

把 `standard.py` 已有的 checks 接入 root `single_e2e.py`：

- `must_include`
- `must_not_primary`
- `citation_membership`
- `answerable_matches_expectation`
- archived-primary violation
- uncertain-as-certain violation
- required source-message coverage

live run 的 runtime IDs 必須先透過 provenance `source_message_ids` 映射到 fixture expectations，不能要求 UUID 等於 `M4/M46`。

AI judge 仍保留，但最終狀態分開：

```text
contract_status       # deterministic schema/runtime
semantic_gate_status  # fixture/source oracle
judge_status          # AI quality review
overall_status = all required deterministic statuses PASS
```

AI judge `PASS` 不得覆蓋 semantic gate `FAIL`。

### 10.4 Artifact lineage 與 latest-only publish

保留固定 `results/` 路徑，但每個 artifact 都帶同一份：

```text
execution_id
generated_at
temporary_namespace
input_sha256
query_fixture_sha256
extraction_schema_version
state_policy_version
producer
```

產出先寫 temporary files，整條流程完成後 atomic replace；root summary 最後發布。若中途失敗，保留 failure summary，但不得留下新舊 execution 混合且看似同一 run 的 artifacts。

## 11. 分階段實作順序

### Phase 0：鎖定 regression oracle

變更：

- 新增 `live_extraction_expectations.jsonl`。
- 新增五個關鍵訊息的 contract tests。
- 測試先以目前 runtime 跑出預期 failure，證明測試真的抓得到問題。

Gate：測試能精確指出 `msg-003/004/045/046/050` 的 missing claim/status，不只報總數不同。

### Phase 1：Contract v2 與 exact evidence span

變更：

- contracts v2、assertion inventory、modality、temporal scope。
- extractor prompt 與 server-side quote validation。
- coverage artifact 新欄位。

Gate：unit tests 覆蓋 mixed extracted/skipped assertions、全 NO_MEMORY、invalid quote、duplicate candidate、msg-004 兩筆 memory。

### Phase 2：Persistence 與 state policy

變更：

- additive migration 0007。
- `derive_initial_state()` 純函式。
- `Memory` / `MemoryClaim` dual-write modality、temporal scope、status、offset evidence。
- source-derived archived lifecycle audit。

Gate：PostgreSQL integration test 證明五種 mapping、rollback、claim/view equality；migration offline SQL 與 live dedicated DB 均通過。

### Phase 3：Resolver 保留來源狀態

變更：

- CREATE 不覆蓋 initial state。
- MERGE/SUPERSEDE/CONTRADICT/DEFER 加入 modality/temporal compatibility policy。
- claim sync 與 decision audit 完整。

Gate：既有 CREATE/SUPERSEDE/CONTRADICT/DEFER baseline 無 regression，另新增 uncertain CREATE、archived CREATE、question CREATE。

### Phase 4：Retrieval、answer 與 root hard gates

變更：

- scope-aware temporal/status policy。
- root E2E 接入 must-include、must-not-primary、answerability 與 state safety。
- artifacts 加 execution lineage 並 atomic publish。

Gate：任何一筆關鍵記憶缺失都必須令 `semantic_gate_status=FAIL`，AI judge PASS 也不能改變。

### Phase 5：小型 live canary

只跑五則關鍵訊息與相關查詢：

```text
msg-003, msg-004, msg-045, msg-046, msg-050
```

Gate：

- expected atomic claims 全部存在。
- status/modality/temporal scope 全部正確。
- CMP 問題可回答研究脈絡，但不發明解鎖結果。
- 花蓮 historical 可取回，current 不得當主要計畫。
- 閱讀媒介維持 uncertain。
- temp namespace cleanup 前先保存完整 DB/artifact proof。

### Phase 6：完整 60-message / 60-query E2E

Gate：

- extraction、resolution、retrieval、answer、judge 分層報告。
- 60/60 deterministic query checks 通過。
- 0 answerability expectation mismatch。
- 0 archived-primary violation。
- 0 uncertain-as-certain violation。
- 0 artifact lineage mismatch。
- AI judge 結果另外呈現，不與 contract PASS 混寫。

## 12. 測試矩陣

### Unit

- `NoMemoryReason` v2 whitelist 與 legacy read compatibility。
- assertion inventory cross-field validation。
- exact evidence quote 與 Unicode offset。
- `derive_initial_state()` 全 mapping table。
- historical asserted 不自動 archived。
- question content 不升格成問題答案。
- CREATE 保留 uncertain/archived。
- MERGE 不升級狀態。

### Integration

- 一則訊息兩個 candidates 都寫入 Memory、MemoryClaim、ClaimEvidence。
- quote/offset 找不到時整個 candidate transaction rollback。
- source-derived archive 有 lifecycle decision。
- claim/view status/modality/temporal scope 一致。
- archived/uncertain retrieval scope 行為。

### Regression

- 真正 chitchat／external noise 仍為 `NO_MEMORY`。
- preference subject boundary 不退化；朋友偏好不得寫成 user preference。
- 既有 live resolver 四種 action 仍通過。
- 既有 baseline 32 題不得因狀態欄位新增而 regression。

### Live semantic canary

- 五則關鍵 message。
- CMP、花蓮、閱讀媒介相關 queries。
- artifact 與 DB row 雙重檢查，不只看 summary boolean。

## 13. 完成定義

只有同時符合以下條件才算修正完成：

1. `msg-003` 兩個 atomic facts 均存在，分別為 active 與 uncertain。
2. `msg-004` 不再只抽大顯存需求；CMP 170HX research question 也存在。
3. `msg-045` 不再被 `insufficient_assertion` 丟棄。
4. `msg-046` 可在 historical 查詢取回，current 查詢不得 primary，且 archive 有 audit。
5. `msg-050` 以 uncertain 保存，回答不選定紙本或電子書。
6. v2 live extraction 不再產生 `insufficient_assertion` 或 extractor-side `duplicate_surface_form`。
7. 所有 memories 都有 validated exact evidence span。
8. Memory 與 MemoryClaim 的 status/modality/temporal scope mismatch 為 0。
9. root E2E 對 `must_include`、`must_not_primary`、answerability/state safety 使用 hard gate。
10. artifact execution lineage 一致，且固定路徑以 atomic overwrite 發布。
11. targeted unit/integration、五訊息 live canary、完整 60 題 E2E 都有實際執行證據。

## 14. 回退與風險控制

- migration 為 additive、forward-only；舊欄位與 v1 artifacts 保持可讀。
- live provider 切換 v2 前先以 feature flag／test namespace canary 驗證。
- 若 v2 provider structured output 不穩定，fail closed 成 `FAILED`，不可降級為 v1 後靜默遺失狀態。
- 若 retrieval scope policy regression，可回退 ranking policy；已寫入的 source/modality/history 不需刪除。
- 若 source-derived archive 規則誤判，只能透過有 audit 的 lifecycle reversal 修正，不直接改 row 或刪除 evidence。
- 不對現有未提交檔案做 reset；實作時應在目前 dirty worktree 上以獨立小 patch 分階段落地。

