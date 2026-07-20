# Hebbian 記憶架構增強與 60 題評估計畫

> 版本：v0.1  
> 日期：2026-07-20  
> 狀態：implementation-ready plan；本文件定義後續架構切片與驗收方式，不把目前 fixture contract 綠燈視為新架構已完成。

## 1. 結論與目標

目前系統已具備 namespace、來源訊息、atomic memory extraction、candidate staging、state resolution、edge trace 與 citation-gated co-retrieval。這些能力足以支撐受限資料集的可追溯 retrieval，但 `Memory` 仍同時承擔事實、狀態、向量與圖節點，`MemoryEdge` 也同時承擔證據關係與可學習聯想。

下一階段目標是將系統收斂為：

```text
immutable source/evidence
        ↓
claim / event ledger
        ↓
state resolution ───────────→ canonical memory view
        ↓                              ↓
relation evidence             association statistics
        ↓                              ↓
rebuildable knowledge graph ← retrieval planner
```

完成後必須同時具備：

1. 可用：既有 32 題 baseline 不退化，來源、狀態與引用契約保持相容。
2. 聯想成長：學習只更新 association statistics，不改寫 claim 或 relation truth。
3. 適應性遺忘：先降低聯想可見性，再可逆 archive；來源與證據永不因遺忘被刪除。
4. 湧現知識圖譜：圖譜由已通過證據與狀態 gate 的 claim 投影，可清空後重建。
5. 可驗證：60 題 suite、動態場景測試與 machine-readable coverage 同時存在。

## 2. 範圍與非目標

本計畫納入：

- claim/event、relation evidence、association statistics、projection 與 lifecycle policy 的責任拆分。
- 受限二跳 activation、路徑預算與完整 activation trace。
- citation、明確回饋與查詢成功訊號的學習 gate。
- 時間、使用頻率、重要度、信心與負回饋共同決定的可逆遺忘。
- 60 題 retrieval/answer suite 與架構處理的 unit/integration/scenario tests。

本計畫不納入：

- 自動刪除 source message、evidence 或 superseded history。
- 把高頻 co-retrieval 當成 supports、contradicts 或 factual relation。
- 無界多跳圖搜尋或全 namespace LLM consolidation。
- 未經離線 report-only 與安全 gate 就對既有 namespace 做 destructive backfill。
- 直接 import `sample/HeLa-Mem` runtime code。

## 3. Canonical 與 derived 邊界

### 3.1 Canonical records

| 層 | 權威內容 | 允許更新方式 |
|---|---|---|
| source/evidence | 原始訊息、內容 hash、時間、來源角色 | immutable；修正需建立新版本 |
| claim/event | 可獨立判真的主張或事件及 evidence refs | append + resolution，不覆寫來源 |
| state decision | active/superseded/uncertain 與決策證據 | transaction + immutable audit |
| relation evidence | supports/contradicts/supersedes/temporal 的證據 | append/retract metadata，不由 retrieval 學習產生 |

### 3.2 Derived records

| 層 | 用途 | 可重建性 |
|---|---|---|
| semantic similarity | 候選搜尋與一跳關聯 | embedding/config 變更後重建 |
| association statistics | co-retrieval、正負回饋、最後活化與衰減狀態 | 由 learning events 重播 |
| canonical memory view | answer/retrieval 使用的 current/historical view | 由 claim + state decision 重建 |
| knowledge graph projection | entity/claim/relation graph | 由通過 gate 的 canonical records 重建 |
| consolidation proposal | cluster 摘要候選 | 可丟棄；核准前不得成為 active claim |

## 4. 建議資料模型

### 4.1 `memory_claims`

最低欄位：

```text
id, namespace_id, claim_type
subject_entity_id, predicate_key, object_value/object_entity_id
event_time, valid_from, valid_to
polarity, modality, confidence, importance
status, state_key
created_at, schema_version
```

`Memory` 在 migration 過渡期可繼續作 retrieval view，但新狀態判定應逐步轉向 `memory_claims`。不得一次改掉既有 public retrieval contract。

### 4.2 `claim_evidence`

```text
claim_id, namespace_id, source_message_id
evidence_text, evidence_start, evidence_end
extractor_model, extraction_schema_version
created_at
```

同一 claim 可有多份 evidence；consolidated claim 必須至少能回到一份原始 evidence，不接受只有另一段 AI 摘要作為唯一證據。

### 4.3 `relation_evidence`

```text
id, namespace_id, source_claim_id, target_claim_id
relation_type, direction
origin, evidence_refs, confidence
valid_from, valid_to, resolution_status
created_at
```

`supports`、`contradicts`、`supersedes` 與 `temporal` 存在此層。這些 relation 不使用 Hebbian decay，也不得由 co-retrieval 自動升格。

### 4.4 `association_events` 與 `association_stats`

```text
association_events:
  id, namespace_id, source_memory_id, target_memory_id
  event_type, delta, retrieval_run_id, evidence_refs
  occurred_at, policy_version

association_stats:
  namespace_id, source_memory_id, target_memory_id
  positive_strength, negative_strength
  activation_count, success_count, rejection_count
  last_activated_at, last_reinforced_at
  effective_weight, decay_policy_version
```

event types 第一版固定為：

```text
cited_together
explicit_positive_feedback
explicit_negative_feedback
answer_rejected
manual_link
decay_checkpoint
```

只有 event ledger 是學習權威；`association_stats` 是 materialized projection。

### 4.5 `graph_projection_runs`

保存：

```text
projection_version, source_snapshot_hash, config_snapshot
included_claim_count, excluded_claim_count
node_count, edge_count, blocker_counts
status, started_at, completed_at
```

每個 graph node/edge 都保存 `claim_ids` 或 `relation_evidence_ids`。無 provenance 的圖邊不得 publish。

## 5. Retrieval 與 learning pipeline

### 5.1 Retrieval

```text
query normalize/embed
  → scoped semantic candidates
  → state/lifecycle filter
  → seed selection
  → bounded graph activation
  → optional rerank
  → final selection + trace
  → answer + citation validation
```

二跳 activation 的必要限制：

- `max_depth = 2` 起步，正式 config 最大不得超過 3。
- 每個 seed 的每跳 neighbor 上限與整體 path budget 分開限制。
- 同一路徑不得重複節點；遇 supersedes cycle 直接 fail closed。
- `contradicts` 只帶入 context，不提供正向 association bonus。
- provenance 不足、archived 或低 confidence 節點可進 historical context，但不可因多跳成為 current primary。
- `activation_path` 保存每跳 edge kind、來源、衰減、contribution 與阻擋理由。

### 5.2 Association learning

learning 不直接修改 factual edges。流程固定為：

```text
retrieval trace committed
  → answer citation contract passed
  → optional explicit feedback
  → append association_events
  → recompute affected association_stats
```

同一次 run 不可重複學習；以 `retrieval_run_id + event_type + pair` 建 idempotency key。只有被 answer 引用的 pair 可獲得 `cited_together`，selected 但未引用的候選不自動強化。

### 5.3 Adaptive forgetting

第一版只做可逆的 retrieval forgetting：

```text
effective_relevance =
  base_importance
  × confidence_factor
  × recency_factor
  × usage_factor
  × feedback_factor
  × lifecycle_factor
```

規則：

1. source/evidence、state audit、factual relations 不 decay。
2. association strength 使用可重播的時間衰減；所有時間測試注入 frozen clock。
3. 低權重 association 先不參與 activation，再進 prune proposal；不直接刪 event ledger。
4. memory archive 必須由 lifecycle decision 記錄 before/after、policy version 與 reason。
5. superseded memory 在 historical query 仍可被取回；forgetting 不等於抹除歷史。
6. high importance、最近被明確引用、或仍是 current state 的 memory 不得只因時間久而 archive。

## 6. 模組解耦計畫

建議新增或調整：

```text
ingestion/preparation.py
  transaction 外完成 normalization、embedding 與 candidate payload

ingestion/application.py
  短 transaction 內做 CAS、state lock、claim/evidence 寫入

persistence/claim_store.py
persistence/relation_evidence.py
persistence/association_events.py
persistence/association_stats.py
persistence/projections.py

retrieval/planner.py
  semantic/state/activation/rerank 的 orchestration

retrieval/activation.py
  bounded multi-hop 純計算與 trace

retrieval/learning_policy.py
  把 answer/feedback 轉成 association events

lifecycle/policy.py
lifecycle/workflow.py
  decay、archive proposal、report-only/apply

graph/projection.py
graph/contracts.py
  只讀 canonical records 產生可重建圖譜
```

既有 `Retriever` 與 `write_ingestion()` 採 feature-slice 遷移，不建立長期 compatibility facade。每個 slice 完成後立即讓原呼叫方改用新 module，再刪除被取代邏輯。

## 7. Transaction 與並行契約

目前 ingestion transaction 內仍可能執行 embedding。新流程必須：

1. transaction 外完成 provider、embedding 與候選 payload 準備。
2. 短 transaction 建立/取得 durable candidate，讀取 scoped state snapshot。
3. 若需 AI resolver，關閉 transaction 後呼叫 provider。
4. 新 transaction 鎖定 state slot、重算 snapshot hash、套用 claim/state/relation/audit。
5. projection 與 association stats 透過 durable job/outbox 非同步重建。

資料庫另加 partial unique constraint：同 `namespace_id + state_key` 最多一筆 `active`。application lock 是並行控制，unique constraint 是最終資料不變量，兩者不可互相取代。

## 8. 60 題資料集 contract

`data/fixtures/character_memories.jsonl` 現為 60 筆記憶，其中新增 20 筆 `lifestyle` 類別；`data/fixtures/test_queries.jsonl` 現為 60 題。新增題目全部以生活／學習／旅行／飲食脈絡驗證架構能力，避免把新增案例集中在 GPU 主題。

`memory_edges.jsonl` 保留作為研究／投影參考資料，但不再是預設 fixture loader、seed 或 evaluation 的輸入。Hebbian memory 的 runtime 仍可使用動態學習產生的 edge；本次調整只移除手寫 fixture edge 對目前流程的隱含依賴。

`data/fixtures/test_queries.jsonl` 現為 60 題：

| suite | 題數 | 用途 |
|---|---:|---|
| `baseline` | 32 | 鎖定既有 direct、alias、candidate、supersedes、contradicts 與 topic 行為 |
| `architecture_v1` | 28 | 驗收聯想成長、遺忘、多跳、provenance、圖譜與 consolidation 安全性 |

複雜度分布（baseline 題目保留既有格式，未標記者由 contract 保守視為 `basic`）：

| complexity | 題數 | 定義 |
|---|---:|---|
| `basic` | 32 | 既有 baseline 題目，單一明確記憶或 legacy oracle |
| `intermediate` | 5 | 一個 scope/state/alias 判斷，最多一跳 |
| `advanced` | 13 | 多記憶整合、時間或 relation 推理，可能需要二跳 |
| `adversarial` | 10 | 噪音、錯誤來源升格、不確定性、過期狀態與 abstention 壓力 |

每題新增欄位：

```json
{
  "suite": "architecture_v1",
  "complexity": "advanced",
  "architecture_targets": ["multi_hop_activation", "provenance"],
  "required_hops": 2
}
```

`architecture_targets` 目前覆蓋：

```text
direct_recall, alias_resolution, association_growth,
adaptive_forgetting, graph_projection, multi_hop_activation,
temporal_reasoning, state_resolution, contradiction_safety,
provenance, noise_resistance, consolidation, abstention
```

60 題只有 retrieval/answer oracle，不足以單獨證明 learning 與 forgetting 正確；因此仍需下一節的動態場景測試。

## 9. 新架構處理測試

### 9.1 Unit tests

- claim/evidence schema、opaque refs、namespace FK 與 immutable evidence。
- relation evidence 不接受 `co_retrieval` origin 升格為 factual relation。
- association event idempotency、positive/negative aggregation 與 weight bounds。
- frozen clock 下的 decay 計算、current/high-importance protection。
- 二跳 activation path、path budget、cycle prevention 與 contradiction zero bonus。
- projection 同 snapshot 重跑 deterministic，輸出 hash 相同。
- lifecycle decision 的 before/after 與 rollback payload 完整。

### 9.2 Integration tests

至少建立下列固定場景：

1. `association-growth`：兩筆 cited-together 後權重增加；evaluation run、未引用 selection 與 provider failure 都不增加。
2. `negative-feedback`：明確負回饋降低 association，不改寫 claim 或 evidence。
3. `time-decay`：時間前進只降低 association visibility；historical source 仍存在且可查。
4. `state-protection`：舊但仍為 current/high-importance 的 claim 不會被自動 archive。
5. `graph-rebuild`：清空 projection 後由同 snapshot 重建，node/edge/provenance checksum 一致。
6. `multi-hop-safety`：外部低信心噪音可作 context，但不可經二跳成為 current primary。
7. `concurrent-state`：兩個 transaction 同時更新同 state slot，最多一筆 active，失敗方留下可診斷結果。
8. `consolidation-proposal`：cluster 可產生 proposal；evidence coverage 不足時不可 publish。

### 9.3 60 題評估輸出

artifact 必須同時回報：

- overall、by-suite、by-complexity、by-architecture-target pass rate。
- must-include、must-not-primary、citation membership、answerable expectation。
- 多跳題實際 path depth、平均 explored nodes、path budget violations。
- current/historical 混淆率、archived-primary violation、provenance promotion violation。
- answer judge 與 deterministic contract 分開，不以 AI judge 取代 hard gate。

## 10. 實作階段

### Phase 0：基線與測試 contract

本次已完成：

- 保留原 32 題並擴到 60 題。
- 增加 20 筆 `lifestyle` 預設記憶與 4 組 aliases；新增 28 題全部改用新類別，並以 nice-to-have oracle 覆蓋新增記憶。
- 將 `memory_edges.jsonl` 改為 opt-in reference projection；預設 fixture seed 不建立手寫 edge。
- 加入 suite、complexity、architecture targets、required hops。
- evaluator artifact 帶出 coverage metadata。
- 預設完整 E2E query limit 改為 60。
- unit tests 鎖定 32/28 分組、複雜度分布與能力覆蓋。

Gate：fixture validation、unit tests、Ruff 全綠；不宣稱 architecture_v1 retrieval 已通過。

### Phase 1：Claim/evidence 與 relation evidence

- 新增 additive migration 與 persistence primitives。
- ingestion 雙寫現有 Memory view 與新 claim ledger。
- 建 shadow comparison artifact，核對數量、state、evidence coverage。

Gate：namespace leakage、missing evidence、state mismatch 均為 0；既有 32 題無 regression。

### Phase 2：Association event/statistics

- 將 co-retrieval 從 factual edge table 分離。
- citation/feedback 轉 event，materialize stats。
- 提供 replay、checksum 與 report-only migration。

Gate：evaluation immutability、idempotency violation、uncited reinforcement 均為 0。

### Phase 3：Bounded multi-hop retrieval

- 抽出 activation engine 與 planner。
- 實作二跳、path budget、edge policy 與 trace。
- 先只在 `architecture_v1` feature flag 啟用。

Gate：至少 5 題 required_hops >= 2 有完整 path；cycle/budget/provenance hard gates 全通過。

### Phase 4：Adaptive forgetting

- frozen-clock policy、association decay、visibility threshold。
- lifecycle report-only、archive proposal 與 rollback。
- 先測試 namespace allowlist，不改 production-like namespace。

Gate：source/evidence loss = 0、current-state accidental archive = 0、historical recall 保留。

### Phase 5：Graph projection 與 consolidation proposal

- 從 canonical records 重建 graph projection。
- 加 projection run、blockers 與 checksum。
- consolidation 僅產生 proposal，通過 evidence/state/provenance gate 才 publish。

Gate：projection 可重建、無 provenance edge = 0、未核准 proposal publish = 0。

### Phase 6：60 題完整驗收

- 先跑 deterministic retrieval，不呼叫 answer provider。
- 再跑 60 題 answer + citation + judge。
- 最後跑 learning/forgetting scenario tests；三種結果分開出 artifact。

Gate：baseline 不退化，architecture target hard gates 全通過；品質分數未達標時不得只以 contract green 宣稱完成。

## 11. Migration 與 rollback

- migration 採 additive → shadow write → compare → switch read → remove legacy 的順序。
- 不直接重命名或刪除既有 `memories` / `memory_edges`。
- 所有 backfill 預設 `--report-only`，輸出 proposed row counts、collision、unknown mapping 與 checksum。
- association stats 可由 events 重建；projection 可由 canonical ledger 重建。
- lifecycle rollback 只反向狀態／可見性，不刪 source、claim evidence 或 learning event。
- schema rollback 若涉及已寫入 canonical records，使用 forward fix 或備份還原，不提供會遺失資料的自動 downgrade。

## 12. 驗證命令

本機 contract：

```powershell
uv run pytest
uv run ruff check src tests
uv run python -c "from hela_mem_zh_mvp.evaluation.fixtures import load_fixture_bundle; print(len(load_fixture_bundle('data/fixtures').queries))"
```

完整服務 gate：

```powershell
uv run python -m hela_mem_zh_mvp.cli smoke
uv run alembic upgrade head
uv run python main.py run --query-limit 60
```

60 題完整流程會觸發 provider answer/judge 呼叫，且受 `answer_request_interval_seconds` 限制；執行時應保留進度與 provider error artifact，不應以縮短 rate-limit 間隔掩蓋服務限制。

## 13. 完成定義

只有下列條件全數成立才可稱為「新記憶架構完成」：

1. source/evidence、claim/state、relation evidence、association stats 與 graph projection 權威邊界可由 schema 與 trace 證明。
2. co-retrieval 或高頻查詢無法自動升格 factual relation。
3. forgetting 不刪 source/evidence，且 current/high-importance protection 有 deterministic tests。
4. graph projection 清空後可重建，所有 publish edge 都有 provenance。
5. 32 題 baseline 無 regression，28 題 architecture_v1 依 complexity/target 回報結果。
6. 動態 learning/forgetting scenarios、並行 state 測試與故障 rollback 全通過。
7. unit、integration、60 題 deterministic、answer/judge 與 live service 結果分開記錄，不混為單一 PASS。
