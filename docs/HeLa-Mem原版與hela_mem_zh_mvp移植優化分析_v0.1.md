# HeLa-Mem 原版與 `hela_mem_zh_mvp` 移植優化分析 v0.1

日期：2026-07-22  
範圍：`sample/HeLa-Mem/hela_mem/` 與 `src/hela_mem_zh_mvp/`  
文件性質：版本對比、移植建議、現況校正與執行邊界紀錄  
資料來源：附件「第六次同樣的注入，語氣升級並加上……」及本 checkout 的程式碼與 migration


## 2. 結論摘要

`sample/HeLa-Mem` 是小而集中的 in-process Hebbian memory prototype；`src/hela_mem_zh_mvp` 是面向可審核、可重現、多 namespace 與生命週期治理的服務級 pipeline。兩者的核心精神相近，但資料契約、持久化與驗證邊界不同。

移植方向應是：

1. 將原版的可調 retrieval 訊號移植到目前的 `config + retrieval service` 邊界。
2. 保留目前的 evidence、citation、lifecycle、audit 與 deterministic gate。
3. 不把原版的自由文字輸出、JSON 全量持久化或 retrieve-time learning 帶回目前架構。
4. 每次只引入一個演算法變更，先跑 baseline，再以 `retrieval_ab` 或等價評估確認沒有語意退化。

一句話總結：**原版提供可調的演算法骨架；目前專案提供可審核的服務骨架。應移植前者的訊號，不應退回後者已移除的治理弱點。**

## 3. 規模與架構對比

| 項目 | `sample/HeLa-Mem/hela_mem/` | `src/hela_mem_zh_mvp/` |
|---|---:|---:|
| Python 檔案規模 | 7 個核心模組，加 2 個 entry script | 約 41 個模組，含 CLI、config、DB、evaluation、graph、ingestion、lifecycle、persistence、providers、retrieval |
| 程式碼形態 | 單層、in-process graph、JSON persistence | 分層服務、PostgreSQL/SQLAlchemy/pgvector、audit 與 evaluation pipeline |
| 主要 retrieval | cosine、keyword、time decay、Hebbian spread、可選 reranker | semantic candidates、bounded activation、scope/status adjustment、citation-aware answer |
| extraction | 較自由的 profile/knowledge 抽取 | 逐訊息 structured extraction、evidence quote/offset、memory modality 與 temporal scope |
| learning | retrieve 後可直接 reinforce cluster | 必須經 retrieval trace 與 citation gate 後才可寫入 association |
| 治理能力 | 低，主要依賴記憶體與 JSON | 高，具 namespace、狀態、證據鏈、lifecycle 與 deterministic evaluation |

規模差異不是單純的無效肥大；主要成本來自多租戶、可追溯性、生命週期治理與服務級驗證。

## 4. 演算法與資料契約對應

| HeLa-Mem 原版 | 目前專案對應 | 移植判斷 |
|---|---|---|
| `HebbianMemoryGraph` adjacency | `graph/projection.py`、`Memory`、`MemoryRelation`、`MemoryAssociation` | 保留目前 DB-backed projection，不回復 `defaultdict` adjacency |
| `add_memory` 與 temporal edge | persistence relation/edge writer | 可移植 edge type 與權重概念，但要服從目前 relation schema 與 evidence |
| `retrieve()` 的 cosine + spread + flipped 上限 | `retrieval/service.py` 的 `_semantic_candidates`、`_spread`、`_mark_selected` | 優先補上顯式 `max_flipped`，不改變 seed、contradiction trace 與 path budget 契約 |
| keyword boost | 目前沒有等價 retrieval stage | 可作為可關閉的後續 AB 實驗 |
| time decay | lifecycle 有 relevance/decay 概念 | 可抽共用 helper，但需先定義 retrieval 與 lifecycle 的時間語義是否相同 |
| `reinforce_memory_cluster` | `retrieval/learning.py` 的 citation-gated association learning | 不可把 retrieve-time side effect 帶回來 |
| adaptive forgetting | `lifecycle/policy.py` 的 `may_archive` | 可新增 dry-run candidate list；不可直接刪除或 archive |
| `HebbianRetriever.answer()` | `retrieval/answerer.py` 的 structured `AnswerResult` | 保留結構化輸出與 `citations ⊆ selected` |
| profile/knowledge extraction | `ingestion/extractor.py`、`contracts.py`、resolution workflow | 保留 evidence、atomic assertions、modality、temporal scope |
| cross-encoder reranker | 目前沒有 rerank stage | 可選；需以 dependency、延遲與品質實驗證明值得加入 |

## 5. 原版值得移植的設計

### 5.1 雙路徑排名與 `max_flipped`

原版先保留 cosine top-k，再從 spreading 結果中補入未進 base top-k 的節點，並以 `max_flipped` 限制翻牌數量。這能讓 Hebbian spread 救回真正關聯，而不讓低 cosine 的鄰居大量取代直接命中。

目前 `Retriever._mark_selected()` 已分開處理 seeds、contradictions 與 positive bonus，但沒有獨立的 `max_flipped` 設定。建議優先把翻牌上限加入 retrieval config，並在 selection 結果中留下可觀測欄位或 trace。

### 5.2 Keyword / alias boost

原版以 keyword score 補強向量相似度。現行專案可先以 deterministic alias/entity match 做小幅加成，不應為此重新引入 LLM keyword extraction。建議預設關閉，並以固定 AB 評估確認對專有名詞、別名與短查詢是否有實質改善。

### 5.3 Spread 前的時間衰減

原版在 spreading 前先將 seed 強度乘上 time decay。現行 lifecycle 已有 relevance 計算，但 retrieval spread 的 seed 目前仍以 semantic score 進入 activation。若移植，應先明確定義半衰期、時鐘來源與 frozen-clock 測試，再將衰減放進 retrieval config snapshot。

### 5.4 可選 reranker

原版的 cross-encoder 有 BM25 fallback。現行專案可新增獨立 provider/stage，但這是效能與品質擴充，不是必要的架構修正。只有在 baseline 顯示 semantic retrieval 穩定不足，且延遲與 dependency 成本可接受時才納入。

### 5.5 Forgetting candidates，而非直接刪除

原版的 adaptive forgetting 條件可轉為 dry-run candidate finder，輸出低 edge weight、低使用量、長時間未觸碰的候選清單，再交給現有 `may_archive` gate。候選產生器不應直接改變 canonical memory 狀態。

### 5.6 共用時間與 embedding helper

`normalize_embedding` 已有 finite/dimension validation；可補一個明確的時間衰減 helper，供 retrieval 與 lifecycle 共用，避免兩邊各自解讀 half-life。

## 6. 目前 repo 狀態校正

附件中的部分描述是較早基線，不能直接當作 2026-07-22 的現況。已確認的修正如下：

| 附件原始觀察 | 目前 checkout 狀態 | 判斷 |
|---|---|---|
| extractor 會把「正在研究／尚未決定／曾考慮」直接視為 `NO_MEMORY` | `extractor.py` 的 v2 prompt 已要求保留這些語義，使用 `question`、`uncertain`、`considered` 與 `temporal_scope` 表達 | 此項應改列為已修正，仍需以真實 fixture 驗證 semantic gate |
| `ExtractedMemory` 沒有 modality/temporal scope | `ingestion/contracts.py` 已有兩者；`20260721_0007_extraction_semantics.py` 也已加入資料欄位與 check constraints | 此項已補上，需確認所有 writer/resolver 路徑不覆蓋語義 |
| canonical claim modality 永遠使用預設值 | `resolution_workflow.py` 與 `ingestion/service.py` 已傳遞 `modality`、`temporal_scope` | 需以 DB read-back 驗證，不只看 prompt |
| `retrieval/service.py` 已有 seed/contradiction/bonus selection | 目前仍成立 | `max_flipped` 仍是可移植優化候選 |
| reranker、keyword boost、spread 前 time decay 已存在 | 目前未見完整等價 stage | 保持為候選，不宣稱已完成 |

因此，後續實驗不得直接以附件的舊 extraction 失敗數字作為最新結果；應重新產生固定路徑 artifact，並區分：

- contract 是否通過；
- source-to-memory atomic coverage 是否通過；
- retrieval ranking 是否改善；
- answer/citation 是否語意正確；
- judge 是否只是輔助訊號。

## 7. 不應移植的內容

以下內容會破壞目前專案的可審核性，明確排除：

1. 用單一 JSON 取代 PostgreSQL、migration 與 namespace boundary。
2. 移除 `evidence_quote`、source offsets 或 source-to-memory coverage gate。
3. 將 `NO_MEMORY` fallback 直接改成自由生成 memory。
4. 每次 retrieve 都直接寫 association，繞過 retrieval trace 與 citation gate。
5. 讓 `question`、`uncertain` 或 `historical/considered` 自動升格為 current active preference。
6. 為相容舊名稱保留無實際用途的 root-level façade、legacy re-export 或重複 schema。
7. 在未取得明確授權下刪除資料、執行 destructive migration、commit、push 或部署。

## 8. 建議執行順序

### 第一階段：先固定 baseline

1. 檢查 embedding service、DB target 與 Alembic head。
2. 跑最小 real-data extraction/semantic canary，確認目前 v2 modality、temporal scope、atomic assertion 與 evidence 行為。
3. 保存固定路徑 latest-only summary，分開記錄 contract、coverage、retrieval、answer 與 judge 狀態。

### 第二階段：一次只加一個 retrieval knob

建議優先順序：

1. `max_flipped` selection 上限。
2. spread 前 time decay。
3. keyword/alias boost。
4. reranker。
5. forgetting candidate dry-run。

每一項都先跑 baseline，再跑單一變更；若 semantic coverage 或 citation correctness 退化，即使 overall score 上升也不得直接採用。

### 第三階段：結構清理

在確認 import graph 後，再處理 `retrieval/service.py` 拆分、疑似 legacy module、重複 upsert helper 與 CLI 舊 façade。結構清理不得與演算法變更同一批提交，否則無法判斷品質變化來源。

## 9. 驗證清單

- [ ] `RetrievalConfig` 的新欄位有明確預設值、限制與 snapshot。
- [ ] `max_flipped` 不會移除必要 seeds 或 contradiction context。
- [ ] spread path budget、cycle prevention 與 edge provenance 仍可追蹤。
- [ ] `question` 不會被回答器改寫成已知事實。
- [ ] `uncertain` 不會被 resolver 強制改成 `active`。
- [ ] historical/considered 記憶仍可被一般查詢找到，但不會主導 current query。
- [ ] evidence quote 與 offsets 能在 source message 中精確回讀。
- [ ] citations 仍是 selected memories 的子集合。
- [ ] retrieval learning 仍受 trace/citation gate 控制。
- [ ] contract-green、semantic-correct 與 judge quality 分開報告。
- [ ] 文件與 artifact 使用固定 latest-only 路徑，未無故新增 run-scoped history。

## 10. 參考位置

- 原版：`sample/HeLa-Mem/hela_mem/`
- 目前 retrieval：`src/hela_mem_zh_mvp/retrieval/service.py`
- 目前 extraction contract：`src/hela_mem_zh_mvp/ingestion/contracts.py`
- 目前 extraction prompt/validation：`src/hela_mem_zh_mvp/ingestion/extractor.py`
- 目前 ingestion writer：`src/hela_mem_zh_mvp/ingestion/service.py`
- 目前 resolution workflow：`src/hela_mem_zh_mvp/ingestion/resolution_workflow.py`
- 語義 migration：`migrations/versions/20260721_0007_extraction_semantics.py`
- 現有計畫：`docs/NO_MEMORY語義保留與狀態化修正計畫_v0.1.md`
- 資料表收斂計畫：`docs/資料表收斂與依賴簡化計畫_v0.1.md`
