# 逐訊息記憶抽取與跨訊息 Resolution 計畫

> 版本：v0.1  
> 日期：2026-07-21  
> 狀態：分析與實作計畫；本文件不代表 runtime 已完成

## 1. 結論

`data/input/conversations.jsonl` 的 live ingestion 應改為「逐筆隔離抽取、逐筆留下 coverage outcome，再跨訊息 resolution」，不再把 60 筆訊息放進同一個 extraction prompt 要模型整體整理。

固定資料流為：

```text
60 筆 SourceMessage 驗證與 durable intake
  ↓
Phase A：每次只把 1 筆 primary message 傳給 extractor
  ↓
每筆產生 0、1 或多筆 atomic memory candidates
  ↓
per-message coverage gate（60/60 訊息都有明確 outcome）
  ↓
Phase B：跨訊息 scoped candidate resolution
  ├─ CREATE
  ├─ MERGE_PROVENANCE
  ├─ SUPERSEDE
  ├─ CONTRADICT
  └─ DEFER / IGNORE
  ↓
claim/evidence、current state、factual relations、semantic/temporal edges
  ↓
retrieval → AI answer → deterministic answer gate → AI judge
```

這裡的 `60/60` 是 source message 處理覆蓋，不是要求剛好建立 60 筆 memory。一筆訊息可以：

- 沒有長期記憶價值，明確標記 `NO_MEMORY`。
- 產生一筆 atomic memory。
- 同時包含多個可獨立判真的主張，拆成多筆 atomic memories。
- 因 provider、schema 或證據問題失敗，標記 `FAILED`，使 ingestion fail closed。

## 2. 本次問題與現況證據

### 2.1 現行 extraction 是全量單次呼叫

目前 `ingestion.workflow.ingest()` 先載入全部訊息，再呼叫一次：

```python
messages = load_input_messages(input_path)
extraction = extract_messages(provider, messages)
```

`extract_messages()` 將整個 `messages` list 序列化到同一個 prompt。現行檢查只要求模型回傳的 `source_message_ids` 集合等於輸入集合，沒有驗證每筆 source message 是否實際產生 extraction outcome。

因此以下結果目前是合法的：

```text
input_message_count = 60
result.source_message_ids = 60 IDs
result.memories = 4 candidates
ingestion.created = 4
status = completed
```

本次 artifact 正是這種情況：temporary namespace 只建立 4 筆可檢索 memory，內容為人物特質與飲食偏好，GPU 訊息沒有進入 memory view。

### 2.2 現行資料庫沒有 per-message extraction audit

目前已有：

- `source_messages`：保存原始訊息。
- `ingestion_runs.message_count`：保存整批訊息數。
- `memory_candidates.extraction_payload`：只保存已產生的 candidates。
- `claim_evidence.source_message_id`：讓已建立 claim 回指來源。

目前缺少：

- 每個 `source_message_id` 是否已抽取。
- 該訊息產生幾筆 candidates。
- 零 memory 是否為模型明確判定，或只是漏抽。
- 每筆 provider attempt、錯誤與重試結果。
- 一次 ingestion 中尚未處理或遺失的 message IDs。

`ingestion_runs.error_count` 與 `memory_candidates.attempt_count` 雖已存在，但目前沒有形成完整的 per-message coverage contract。

### 2.3 現行 Phase B 只有部分能力真正接線

目前已存在的安全能力：

1. canonical key 完全相同時，`resolve_memory()` 自動執行 `MERGE_PROVENANCE`。
2. 候選搜尋會先限制在同 namespace、memory type、entity、topic，再使用 pgvector 排序。
3. `apply_resolution()` 可原子套用 `SUPERSEDE` 與 `CONTRADICT`。
4. `SUPERSEDE` 要求可比較時間順序，並防止 supersedes cycle。
5. `CONTRADICT` 在沒有其他已解析 active state 時，將兩側標成 `uncertain`。
6. 已有 AI resolver prompt 與 post-validation，可檢查 target snapshot、confidence、evidence quote 與 supersede order。

目前尚未完成：

1. `resolution_prompt()` 與 `validate_ai_decision()` 沒有接進 live ingestion orchestration。
2. 語意相近但非 canonical exact match 的候選，目前一律 `DEFER`。
3. 現行 extractor relation 只能引用同一個 `ExtractionResult` 內的 candidate IDs。
4. 改為逐訊息 extraction 後，不同訊息的 candidates 不會出現在同一個 extractor response，跨訊息 relation 必須完全交給 Phase B。
5. 尚無固定 scenario 證明 paraphrase merge、偏好更新、無法判定矛盾與不同偏好共存。

所以目前不能聲稱「已能自動整理 merge 並解矛盾」。準確狀態是：底層資料結構與安全套用 primitive 已存在，但跨訊息 AI resolution runtime 尚未接通。

## 3. 選定方案

### 3.1 採用：每次一筆 primary message

Phase A 每次 provider request 只能包含一筆 `SourceMessage`：

```json
{
  "message_id": "msg-001",
  "session_id": "fixture-gpu-01",
  "role": "user",
  "content": "我曾想買 RTX 3090，但擔心價格和風險。",
  "occurred_at": "2026-01-10T12:00:00+08:00",
  "metadata": {}
}
```

不提供同 session 的其他訊息、不提供現有 memories、不提供 fixture oracle。Phase A 只負責從單一來源拆出 atomic candidates，不負責跨訊息 merge、狀態更新或矛盾判定。

選擇理由：

- 可直接證明哪一筆訊息被漏抽。
- provider failure 只影響單筆，不會使整個 60 筆 response 不完整。
- evidence 必須落在唯一 primary message，驗證簡單且可重現。
- 不會讓模型在大 prompt 中挑選少量顯眼事實而忽略其他訊息。
- 跨訊息語義集中由 Phase B 處理，責任清楚。

代價：

- 60 筆資料需要最多 60 次 extraction provider calls。
- latency 與 provider quota 成本上升。
- supersedes／contradicts 無法靠 extractor 同批輸出，Phase B 必須真正完成。

本計畫接受這個代價；正確性、來源追蹤與可驗證性優先於第一版吞吐量。後續若需優化，可改成小型 micro-batch，但 schema 仍必須逐訊息回傳獨立 outcome，不能退回全批摘要。

## 4. Phase A：隔離上下文的逐訊息 extraction

### 4.1 職責

Phase A 只回答：

```text
這一筆來源訊息包含哪些可獨立保存的記憶主張？
```

允許輸出：

- entity mentions。
- atomic memory candidates。
- memory type：`character_fact`、`event`、`preference`、`decision`。
- topic、attribute key、primary entity。
- occurred_at、importance、confidence。
- 逐字 evidence。

不允許輸出：

- DB UUID、namespace 或 SQL。
- 指向其他訊息 candidate 的 relation。
- 根據未提供上下文推測「後來」「以前」的關係。
- 自行判定既有 memory 應被 supersede 或 merge。
- fixture `M1...M60` external IDs。

### 4.2 Per-message outcome contract

建議新增獨立 contract：

```json
{
  "schema_version": "message-extraction-outcome-v1",
  "source_message_id": "msg-001",
  "status": "EXTRACTED",
  "entities": [],
  "memories": [],
  "reason_code": null,
  "provider_attempts": 1
}
```

`status` 固定為：

```text
EXTRACTED
NO_MEMORY
FAILED
```

規則：

- `EXTRACTED`：至少一筆 memory candidate。
- `NO_MEMORY`：memories 必須為空，且 `reason_code` 必填。
- `FAILED`：不得視為 coverage PASS；整批 ingestion 最終 FAIL。
- 每個 outcome 只能對應一個、且必須是本次輸入的 `source_message_id`。
- 每筆 memory 的 `evidence_message_ids` 必須剛好是 `[source_message_id]`。
- evidence 必須是該 message content 的連續逐字片段。
- 一筆 message 可產生多筆 atomic memories，但 candidate IDs 在該 outcome 內唯一。

`NO_MEMORY.reason_code` 使用受限 enum，例如：

```text
non_durable_chitchat
external_noise
duplicate_surface_form
insufficient_assertion
unsupported_role_content
```

AI 不得自由輸出長篇理由來掩蓋漏抽；reason 只作簡短 audit 補充。

### 4.3 人物特質、想法與處理方式

目前 input 混合：

- assistant 描述的角色特質。
- user 第一人稱偏好與經歷。
- 工作方法與處理習慣。
- 外部人物、論壇或群組噪音。
- 尚未決定的想法。

Phase A 必須保留 subject 邊界：

```text
角色喜歡無糖熱茶
≠ 使用者喜歡無糖熱茶
≠ 朋友喜歡無糖熱茶
```

最低要求：

- `primary_entity_candidate_id` 不得省略於 preference／character_fact。
- entity 必須保存可區分的 subject，例如 `user`、`character`、`friend/external`。
- `attribute_key` 對可更新狀態必填，不再視為純 optional。
- uncertain／未決定內容應以 modality 或 confidence 表達，不可直接當成 active preference。

偏好相關建議 attribute keys：

```text
room_noise
room_lighting
beverage_sweetness
beverage_temperature
reading_audio
communication_language
decision_pace
work_decomposition
uncertainty_disclosure
food_cilantro
breakfast_flavor
reading_medium
```

若 attribute key 不穩定，Phase B 無法區分「同一狀態更新」與「兩個可同時成立的偏好」。

### 4.4 Coverage gate

整批 extraction 的 deterministic gate：

```text
input_message_ids == outcome_message_ids
duplicate_outcome_ids == 0
unreported_message_ids == 0
unexpected_message_ids == 0
failed_outcomes == 0
invalid_evidence == 0
```

Artifact 至少輸出：

```json
{
  "input_message_count": 60,
  "outcome_count": 60,
  "extracted_count": 0,
  "no_memory_count": 0,
  "failed_count": 0,
  "memory_candidate_count": 0,
  "unreported_message_ids": [],
  "duplicate_message_ids": []
}
```

不得以 `memory_candidate_count == 60` 作 gate。

## 5. Phase B：跨訊息 resolution

### 5.1 輸入與責任

Phase B 每次處理一筆已通過 Phase A 的 candidate，取得：

- candidate atomic content。
- source message evidence 與時間。
- normalized subject/entity。
- memory type。
- topic key。
- attribute key／state key。
- 同 namespace、同 subject、同 type/topic/state slot 的既有 candidates。

Phase B 不看完整 60 筆原始對話，只看必要的 candidate snapshot 與 evidence refs，避免再次變成全量摘要器。

### 5.2 決策順序

固定順序：

1. **Exact canonical merge**  
   canonical key 完全相同時，直接 `MERGE_PROVENANCE`，不呼叫 AI。

2. **No scoped candidate**  
   沒有相同 state scope 的既有候選時，`CREATE`。

3. **Safe deterministic exclusion**  
   subject、memory type、topic 或 attribute 不同，視為可共存，不做 merge／contradict。

4. **Ambiguous scoped candidate**  
   有語意相近且 state scope 相同的候選時，建立 immutable snapshot，transaction 外呼叫 AI resolver。

5. **Post-validation**  
   檢查 opaque target refs、snapshot membership、evidence quote、confidence、effective order、state key 與 action shape。

6. **Atomic apply**  
   新 transaction 重新取得 state lock、確認 snapshot 未漂移，再套用決策與 audit。

7. **Fail closed**  
   snapshot 已變、證據不足、低 confidence 或無法判定時 `DEFER`；不得猜測 merge 或 current state。

### 5.3 Actions 的語義

#### `MERGE_PROVENANCE`

同一事實或偏好的重述，只增加 evidence，不新增另一個 active claim。

範例：

```text
我喜歡喝無糖熱茶。
我偏好不加糖的熱茶。
```

預期：一個 active preference claim、兩份 claim evidence。

#### `SUPERSEDE`

同一 subject + state slot，後來訊息明確更新先前狀態，且時間順序可靠。

範例：

```text
2026-01-01：閱讀時我喜歡播放輕音樂。
2026-02-01：現在閱讀時我偏好完全安靜，不再播放音樂。
```

預期：後者 active、前者 superseded，存在有方向的 supersedes relation。current query 回答目前偏好；historical query 可同時說明過去狀態。

#### `CONTRADICT`

同一 state slot 的內容不可同時成立，但沒有可靠順序或證據不足以判斷誰取代誰。

範例：

```text
我只喜歡紙本書。
我只喜歡電子書。
```

若時間相同或順序不可靠，預期建立 contradicts，兩側為 uncertain。回答不得任選一側當成目前偏好。

#### `CREATE`

不同 state slot、可同時成立，或沒有安全 scoped candidate。

範例：

```text
我不吃香菜。
我喜歡清淡早餐。
```

這兩筆不可 merge，也不是 contradiction，應各自建立 active preference。

#### `DEFER`

可能相關但無法安全判斷。DEFER candidate 不可進 active retrieval view；必須保留 audit 與後續 review 能力。

### 5.4 AI resolver 接線要求

現有 `resolution_prompt()`、`validate_ai_decision()` 與 `apply_resolution()` 可作基礎，但需補上 application workflow：

```text
stage candidate + snapshot
  ↓ commit / close transaction
AI resolver(candidate + opaque candidate refs)
  ↓
validate_ai_decision()
  ↓
new transaction + state lock
  ↓
recompute snapshot hash
  ↓
apply_resolution() / persist DEFER
```

Provider 不得取得 session、UUID、namespace、SQL 或可自行查詢 DB 的能力。

## 6. 偏好回答驗收

目標不是只證明 resolver 有寫 edge，而是證明 AI 最後能根據 current/historical memory view 正確回答使用者偏好。

### 6.1 固定小型 scenario corpus

建議先新增一組獨立於 60 題大型 corpus 的 8～12 筆 scenario input，至少包含：

1. **Exact/paraphrase merge**：無糖熱茶重述。
2. **Preference supersede**：閱讀播放音樂 → 現在偏好安靜。
3. **Unresolved contradiction**：紙本書 vs 電子書，無可靠順序。
4. **Coexisting preferences**：不吃香菜 + 喜歡清淡早餐。
5. **Character/user separation**：角色偏好與使用者偏好不可混成同一 subject。
6. **External noise**：朋友或論壇的偏好不可升格成使用者偏好。
7. **Uncertain thought**：尚未決定不得被回答成確定偏好。
8. **Duplicate input**：同 message id 重跑 idempotent，不增加重複 claim。

### 6.2 查詢與預期

| Query | Scope | 必要行為 |
|---|---|---|
| 我喜歡喝什麼？ | current | 回答無糖熱茶，引用合併後 active claim |
| 我閱讀時喜歡播放音樂嗎？ | current | 回答目前偏好安靜，不把舊音樂偏好當 current |
| 我以前閱讀時會播放音樂嗎？ | historical | 回答曾播放輕音樂，並說明後來已更新 |
| 我偏好紙本書還是電子書？ | current | 明確保留不確定，不任選一側 |
| 我吃香菜嗎？ | current | 回答不吃香菜 |
| 我早餐偏好如何？ | current | 回答清淡早餐，不受香菜 state slot 干擾 |
| 朋友的偏好也是我的偏好嗎？ | general | 回答不能如此推論 |
| 我是否已決定閱讀媒介？ | current | 若來源是尚未決定，answerable=false 或明確回答尚未決定 |

### 6.3 Deterministic hard gates

在 AI judge 前先驗證：

- 每題 required canonical keys／source evidence refs 已進 selected memories。
- current query 的 primary memory 不得是 superseded。
- contradiction unresolved 時不得輸出單一確定偏好。
- answerable 必須符合 expectation。
- citations 必須屬於 selected memories。
- user query 不得引用 character／friend 作為 user preference。
- historical query 必須允許 superseded evidence。
- 無答案或 uncertain 題不得生成不存在的確定偏好。

AI judge 只負責自然語言品質、association completeness 與說明是否清楚，不得取代上述 hard gates。

## 7. Fixture 與 live input 邊界

目前 `character_memories.jsonl` 與 `conversations.jsonl` 都有 60 筆，但不能因此假設它們是逐筆一對一 oracle。

目前檢查結果：

- 60 筆 fixture memories 共引用 60 個 source message refs。
- 只有 48 筆 fixture content 與所引用 message content 存在直接包含關係。
- `M2...M12` 多筆 source refs 與現行 input 內容不相符。
- 例如 `M3` 引用 `msg-003`，但 `msg-003` 實際是 CMP 170HX 研究；「最後沒有購買 RTX 3090」實際較接近 `msg-002`。

因此分開兩套 oracle：

1. **Retrieval fixture oracle**  
   `character_memories.jsonl` + `test_queries.jsonl`，使用穩定 `M1...M60` 驗證預先建立的 canonical corpus。

2. **Live extraction oracle**  
   建議新增 `data/fixtures/extraction_expectations.jsonl`，以 source message 為中心：

```json
{
  "source_message_id": "msg-001",
  "expected_outcome": "EXTRACTED",
  "expected_claims": [
    {
      "subject": "user",
      "memory_type": "event",
      "attribute_key": "gpu_purchase_intent",
      "required_evidence": "我曾想買 RTX 3090"
    },
    {
      "subject": "user",
      "memory_type": "event",
      "attribute_key": "gpu_purchase_risk",
      "required_evidence": "擔心價格和風險"
    }
  ]
}
```

Live extraction 不用動態 `mem-*` 與 fixture `M*` 比 ID，而是比較 source coverage、subject/type/attribute、evidence 與 canonical claim semantics。

## 8. 固定 artifacts

沿用 latest-only overwrite，不增加 run-id 目錄：

```text
results/pipeline/extraction.json
results/pipeline/extraction_coverage.json
results/pipeline/resolution.json
results/pipeline/retrieval.json
results/pipeline/answers.json
results/pipeline/judge_input.json
results/summary.json
```

`results/summary.json` 是唯一的流程摘要位置；`results/pipeline/` 只保留逐階段明細，不再產出重複的 `summary.json`。摘要中的 `qa` 將 AI judge 統計與逐題結果合併；每題固定提供 `question`、`ai_answer`、`correct_answer`、`judge` 與引用。

`extraction.json` 每筆 source message 一個 outcome；`resolution.json` 每個 candidate 一個 immutable decision record，含 before/after、snapshot hash、action、targets、evidence 與 validation status。

## 9. 實作順序

### Phase 0：資料與契約修正

1. 新增 per-message extraction outcome schema。
2. 新增 extraction coverage artifact 與 hard gate。
3. 新增 live extraction oracle，修正或明確隔離目前不對齊的 fixture source refs。
4. 加入 subject 與 preference attribute-key contract。

Gate：純 contract/unit tests 通過，不呼叫 provider、不改 DB schema。

### Phase 1：逐訊息 extraction workflow

1. 將 `extract_messages(provider, messages)` 拆成 single-message extractor。
2. 逐筆呼叫、逐筆驗證、逐筆留下 outcome。
3. provider error 可依既有 policy 重試；最終失敗使整批 FAIL。
4. 先持久化 immutable source intake，再準備 candidate payload；不得因 provider 失敗遺失 source audit。

Gate：60 筆 input 得到 60 筆 outcomes；故意讓第 N 筆失敗時 artifact 精準指出該 ID。

### Phase 2：跨訊息 Phase B 接線

1. Exact merge 保持 deterministic。
2. scoped ambiguous candidates 真的呼叫 AI resolver。
3. provider call 與 DB transaction 分離。
4. snapshot hash 重查、state lock、post-validation、atomic apply 全接通。
5. DEFER 保留候選但不進 active retrieval view。

Gate：CREATE、paraphrase MERGE、SUPERSEDE、CONTRADICT、DEFER 五種固定 scenario 全部通過。

### Phase 3：偏好回答整合驗收

1. 用固定小型 scenario ingest 到 temporary namespace。
2. current／historical／uncertain／noise 問題全部跑 retrieval + answer。
3. 先跑 deterministic hard gates，再跑 AI judge。
4. 檢查 temporary namespace cleanup 與 fixed artifacts。

Gate：所有偏好題 required evidence、state、answerability、citation 與 subject boundary 通過；AI judge 結果僅作第二層品質報告。

### Phase 4：60 筆完整 proof

1. 跑完整 60 筆逐訊息 extraction。
2. 檢查 coverage、candidate／resolution action 分布與 provider errors。
3. 跑 60 題 retrieval／answer suite。
4. 分開報告 extraction、resolution、retrieval contract、answer contract 與 AI judge。

Gate：不得以單一 `status=PASS` 取代各階段 gate。

## 10. 測試矩陣

### Unit

- single-message prompt 不包含其他 message content。
- outcome message ID 必須等於 primary message ID。
- evidence 必須出現在 primary content。
- 一筆訊息可拆多筆 atomic memories。
- `NO_MEMORY` 必須有合法 reason code。
- coverage 缺 ID、重複 ID、unexpected ID、FAILED 均 fail closed。
- exact canonical merge 不呼叫 AI。
- AI target 不在 snapshot、低 confidence、缺 evidence quote 被拒絕。
- unknown order 不可 SUPERSEDE。
- contradiction 不提供正向 Hebbian bonus。

### Integration

- 逐訊息 extraction 後可建立完整 claim evidence。
- paraphrase 合併只增加 provenance。
- preference update 正確 supersede 舊狀態。
- 無可靠順序的互斥偏好建立 contradiction 並標 uncertain。
- 不同 attribute keys 的偏好可共存。
- resolver provider failure 留下 DEFER／error audit，不產生 active guess。
- snapshot 漂移時拒絕套用舊決策。
- 重跑同 message id 不建立重複 claim。

### Live proof

- 先用 8～12 筆偏好 scenario 做真實 provider proof。
- 再跑完整 60 筆 input。
- Artifact 必須能從錯誤答案一路追到 selected memory、resolution、candidate outcome 與原始 source message。

## 11. 完成條件

只有同時滿足以下條件，才可聲稱「AI 能根據 Hebbian-memory 回答使用者偏好」：

1. 每筆 source message 都有明確 extraction outcome。
2. 關鍵偏好來源沒有漏抽。
3. merge、supersede、contradict 與 coexistence 由固定 scenario 證明。
4. current/historical retrieval 尊重狀態。
5. 回答引用正確 subject 與 evidence。
6. uncertain／conflicting preference 不被回答成確定事實。
7. deterministic hard gates 與 AI judge 分開報告。
8. temporary namespace 清理成功，且 fixed artifacts 保留完整診斷。

在 Phase A coverage 與 Phase B runtime 接線完成前，現有 `created=4`、answer provider 成功或 AI judge PASS 都不能證明 memory ingestion 或偏好回答正確。
