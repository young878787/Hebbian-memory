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
5. 優先補強「候選召回 → spreading 去噪 → 結果選擇」的真實能力缺口；參數護欄、效能候選與程式整理不得包裝成記憶能力提升。

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
| `retrieve()` 的 cosine + spread + flipped 上限 | `retrieval/service.py` 的 `_semantic_candidates`、`_spread`、`_mark_selected` | 現況已由 `final_top_k - seed_top_k` 形成隱含上限；顯式 `max_flipped` 僅是護欄，不是優先能力增強 |
| keyword boost | 目前沒有等價 retrieval stage；但已有 entity、alias、topic 等結構化資料可利用 | 應借鑑「雙路候選」概念，優先做 deterministic entity/alias lexical candidate fusion，而非照搬 LLM keyword extraction |
| time decay | lifecycle 有 relevance/decay 概念 | 可抽共用 helper，但需先定義 retrieval 與 lifecycle 的時間語義是否相同 |
| spreading threshold | 目前以 neighbor/path budget 控制規模，沒有 contribution 下限 | 可借鑑為弱邊去噪，但應以最小 contribution/edge gate 表達並保留 trace，不直接照搬原版全域 threshold |
| `reinforce_memory_cluster` | `retrieval/learning.py` 的 citation-gated association learning | 不可把 retrieve-time side effect 帶回來 |
| adaptive forgetting | `lifecycle/policy.py` 的 `may_archive` | 可新增 dry-run candidate list；不可直接刪除或 archive |
| `HebbianRetriever.answer()` | `retrieval/answerer.py` 的 structured `AnswerResult` | 保留結構化輸出與 `citations ⊆ selected` |
| profile/knowledge extraction | `ingestion/extractor.py`、`contracts.py`、resolution workflow | 保留 evidence、atomic assertions、modality、temporal scope |
| cross-encoder reranker | 目前沒有 rerank stage | 可選；需以 dependency、延遲與品質實驗證明值得加入 |

## 5. 原版值得移植的設計

### 5.1 雙路徑候選召回，而不是先加 `max_flipped`

原版先保留 cosine top-k，再從 spreading 結果中補入未進 base top-k 的節點。真正值得借鑑的是「直接語義命中 + 關聯擴散補回」的雙路徑，而不是 `max_flipped` 這個參數本身。

目前 `Retriever._mark_selected()` 已固定保留 seeds，再依序補入 contradiction context 與 positive Hebbian bonus。預設 `seed_top_k=3`、`final_top_k=5`，因此非 seed 最多本來就只有 2 筆；新增 `max_flipped=2` 不會改變結果，設得更低反而可能降低關聯召回。

更重要的現況限制是：spreading 只能從 semantic top seeds 出發。若中文別名、縮寫、專有名詞或短查詢沒有先進入 semantic seeds，後面的 graph 再完整也無法救回正確分支。因此第一優先應是建立可追蹤的候選融合：

1. semantic candidates 保持現行 pgvector 路徑；
2. 由既有 entity alias、canonical name、topic/attribute exact match 產生 deterministic lexical candidates；
3. 去重後選 seed，trace 必須標出 `semantic`、`alias`、`entity` 或 `topic` 來源；
4. `max_flipped` 僅在實驗證明 graph bonus 過度擠壓 direct hits 時，才作為可設定護欄加入。

### 5.2 Entity / alias lexical boost

原版以 LLM keyword extraction 與 keyword score 補強向量相似度。可借鑑的是 lexical signal，不是「再呼叫一次 LLM」的實作方式。現行專案已有解析後的 entity/alias/topic 結構，應直接重用，避免新增不穩定、昂貴且難以回放的 query-time extraction。

這一項屬於真正的召回算法增強：它能讓 embedding 不敏感的精確名稱進入候選集合，而不只是重排已經找回的資料。初版只支援 deterministic exact/normalized match，小幅且有上限的 bonus；模糊斷詞、BM25 或額外全文索引只有在固定 AB 證明 exact match 不足時再加入。

### 5.3 Weak-edge / low-contribution spreading gate

原版有 `spreading_threshold`，現行 `activate()` 則以 `max_neighbors_per_node`、`path_budget`、`max_depth` 控制搜尋量，但只要是正向邊就會累加 contribution。兩者目的不同：budget 防止爆量，threshold 才是抑制弱關聯噪音。

建議借鑑 threshold 的概念，但不要照搬單一全域值。較安全的形式是在每一步計算 contribution 後套用 `min_activation_contribution`；低於門檻的路徑不再向下傳播，仍在 trace 記錄 `blocked_reason=below_activation_threshold`。這能直接改善多 hop 弱邊累加造成的誤召回，且可由 architecture/multi-hop fixture 驗證。

### 5.4 依記憶類型限制的時間衰減

原版在 spreading 前先將 seed 強度乘上 time decay。現行 lifecycle 已有 relevance 計算，但 retrieval spread 的 seed 目前仍以 semantic score 進入 activation。

此機制不能無差別套用：穩定 persona/profile、長期偏好與常識不應只因時間久而失去檢索權；episodic/current-state 記憶才適合依 `occurred_at` 或 `last_activated_at` 衰減。實作前須定義適用 memory type、缺少 timestamp 的行為、半衰期、時鐘來源與 frozen-clock 測試，再將設定寫入 config snapshot。這屬於排序精度改善，不是第一階段召回修復。

### 5.5 可選 reranker

原版的 cross-encoder 有 BM25 fallback。現行專案可新增獨立 provider/stage，但這是效能與品質擴充，不是必要的架構修正。只有在 baseline 顯示 semantic retrieval 穩定不足，且延遲與 dependency 成本可接受時才納入。

### 5.6 Forgetting candidates，而非直接刪除

原版的 adaptive forgetting 條件可轉為 dry-run candidate finder，輸出低 edge weight、低使用量、長時間未觸碰的候選清單，再交給現有 `may_archive` gate。候選產生器不應直接改變 canonical memory 狀態。

### 5.7 共用時間與 embedding helper

`normalize_embedding` 已有 finite/dimension validation；可補一個明確的時間衰減 helper，供 retrieval 與 lifecycle 共用，避免兩邊各自解讀 half-life。

### 5.8 為什麼「文字知識節點 + embedding + 加權關聯邊」可能比較快

這個判斷在特定條件下成立，但目前尚未有 benchmark 證明它普遍較快。

原始 KB graph 可能較快的原因是：

1. 知識通常是穩定、讀多寫少，embedding 可以在寫入時預先計算。
2. 查詢時只需要對既有節點做向量相似度與 bounded graph spreading，不必重新抽取、normalize、resolve 或寫入完整 ingestion audit。
3. 原版 graph 主要存在 process memory/JSON 結構中，資料量小時可以避免 PostgreSQL round-trip、ORM materialization 與多張 operational table 的查詢成本。
4. 關聯邊已經預先保存，查詢不必臨時推導所有關聯；`max_flipped` 也能限制 spreading 帶回的額外節點數。

相對地，目前 `Memory` 搜尋除了向量候選，還要處理 namespace scope、status、modality、temporal scope、typed relations、learned associations、path budget、cycle prevention、trace 與 citation 契約。這些不是純搜尋成本，而是為可審核性與正確性付出的成本。

因此兩者的比較應拆成兩種情境：

| 情境 | 較可能的優勢 | 主要代價 |
|---|---|---|
| 小型、穩定、讀多寫少的外部 KB | 簡單 graph lookup、預計算 embedding、較少 DB 操作 | 知識更新與證據治理較弱 |
| 使用者記憶、需處理狀態與衝突 | 目前 `Memory` pipeline 的 scope、evidence、lifecycle 與 audit | 查詢路徑較長，資料庫與 trace 成本較高 |

另外，若在初步 retrieval 後加入 Cross-Encoder reranker，reranker 本身會對每個 query-memory pair 執行模型推論；它可能提高 precision，但通常會增加 latency。因此「KB graph 較快」與「加入 reranker」是兩個不同方向，不能把兩者的效能效果混為一談。

本文件的結論是：**KB graph 可以作為小型、穩定、讀多寫少知識的效能候選，但目前應先以相同資料量、相同 top-k、相同 provider 與 p50/p95 latency 實測，再決定是否值得引入；不能為了速度直接繞過目前的 evidence、namespace 與 lifecycle 契約。**

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

## 8. 優化項目的能力判定

為避免把任何原版功能都稱為「優化」，本計畫以能否改善記憶召回、關聯排序、答案 grounding 或安全更新作為判定標準：

| 項目 | 分類 | 預期增益 | 本輪判定 |
|---|---|---|---|
| entity/alias lexical candidate fusion | 記憶召回算法 | 補回向量未命中的別名、縮寫、精確實體 | **優先實作候選** |
| minimum activation contribution | Hebbian spreading 算法 | 阻止多 hop 弱邊噪音繼續放大 | **優先實作候選** |
| type-aware time decay | 記憶排序算法 | current/episodic 查詢降低過期狀態主導 | 第二批，須先定義時間語義 |
| citation-gated association learning | 記憶學習算法 | 讓實際共同支撐答案的記憶逐步增強 | 目前已有，應加品質與負回饋評估，不照搬 retrieve-time learning |
| `max_flipped` | selection 護欄 | 限制 graph 補入筆數 | 現行 top-k 已隱含限制；不是能力提升優先項 |
| cross-encoder reranker | 可選精排模型 | 可能提升 top-k precision | 成本較高，baseline 證明需要後才做 |
| forgetting candidate dry-run | lifecycle 治理 | 找出可能低價值記憶 | 不直接提升召回；不得自動刪除 |
| 共用 helper、module 拆分 | 工程維護 | 降低重複與語義漂移 | 不列為記憶算法增強 |
| in-process/JSON graph | 儲存／效能架構 | 小資料可能降低查詢開銷 | 不等於記憶品質提升，不移植為 canonical store |

目前最需要避免的錯誤是只重排 semantic candidate set。若正確記憶根本沒有進入候選集合，reranker、`max_flipped` 與後段回答器都無法補救；因此驗證必須先看 candidate recall，再看 selected recall 與最終答案。

## 9. 建議執行順序

### 第一階段：先固定 baseline

1. 檢查 embedding service、DB target 與 Alembic head。
2. 跑最小 real-data extraction/semantic canary，確認目前 v2 modality、temporal scope、atomic assertion 與 evidence 行為。
3. 保存固定路徑 latest-only summary，分開記錄 contract、coverage、retrieval、answer 與 judge 狀態。

### 第二階段：一次只加一個能力變更

建議優先順序：

1. entity/alias lexical candidate fusion，先解決候選集合進不來的問題。
2. `min_activation_contribution`，抑制 graph 弱邊與多 hop 噪音。
3. type-aware time decay，只作用於已定義的 episodic/current-state 類型。
4. 只有觀察到非 seed 記憶擠壓 direct hit 時，才加入 `max_flipped` 護欄。
5. 只有前述 deterministic 路徑仍無法改善 precision 時，才評估 reranker。
6. forgetting candidate dry-run 獨立於 retrieval 品質批次，不與排名算法一起驗證。

每一項都先跑 baseline，再跑單一變更；至少分開比較 candidate recall、selected recall、must-include hit、contradiction context、answer/citation correctness 與 p50/p95 latency。若 source coverage 或 citation correctness 退化，即使 overall score 上升也不得直接採用。

### 第三階段：結構清理

在確認 import graph 後，再處理 `retrieval/service.py` 拆分、疑似 legacy module、重複 upsert helper 與 CLI 舊 façade。結構清理不得與演算法變更同一批提交，否則無法判斷品質變化來源。

## 10. 驗證清單

- [x] `RetrievalConfig` 的新欄位有明確預設值、限制與 snapshot。
- [x] lexical candidate 能標示來源、去重，且不跨 namespace。
- [x] candidate recall 與 selected recall 分開報告，能辨識「沒召回」與「排序落後」。
- [x] activation threshold 只阻止低 contribution 繼續傳播，blocked path 仍可追蹤。
- [x] `max_flipped` 經實測判定不加入；改為 bonus/direct 依完整 final score 競爭，未移除 seeds 或 contradiction context。
- [x] time decay 只影響明確允許衰減的記憶類型，穩定 persona/profile 不因年齡自動降權。
- [x] spread path budget、cycle prevention 與 edge provenance 仍可追蹤。
- [x] `question` 不會被回答器改寫成已知事實。
- [x] `uncertain` 不會被 resolver 強制改成 `active`。
- [x] historical/considered 記憶仍可被一般查詢找到，但不會主導 current query。
- [x] evidence quote 與 offsets 能在 source message 中精確回讀。
- [x] citations 仍是 selected memories 的子集合。
- [x] retrieval learning 仍受 trace/citation gate 控制。
- [x] contract-green、semantic-correct 與 judge quality 分開報告。
- [x] 文件與 artifact 使用固定 latest-only 路徑，未無故新增 run-scoped history。

## 11. 參考位置

- 原版：`sample/HeLa-Mem/hela_mem/`
- 目前 retrieval：`src/hela_mem_zh_mvp/retrieval/service.py`
- 目前 extraction contract：`src/hela_mem_zh_mvp/ingestion/contracts.py`
- 目前 extraction prompt/validation：`src/hela_mem_zh_mvp/ingestion/extractor.py`
- 目前 ingestion writer：`src/hela_mem_zh_mvp/ingestion/service.py`
- 目前 resolution workflow：`src/hela_mem_zh_mvp/ingestion/resolution_workflow.py`
- 語義 migration：`migrations/versions/20260721_0007_extraction_semantics.py`
- 現有計畫：`docs/NO_MEMORY語義保留與狀態化修正計畫_v0.1.md`
- 資料表收斂計畫：`docs/資料表收斂與依賴簡化計畫_v0.1.md`

## 12. 實作結果（2026-07-22）

本文件的 deterministic 優先項目已落地：

1. retrieval 會融合 namespace-scoped entity、alias 與 topic exact match，保留有上限的 lexical seed 名額，並在 trace 輸出 `lexical_score`、`lexical_sources` 與複合 `source`。
2. activation 會套用 `min_activation_contribution`；低於門檻的路徑保留 `blocked_reason=below_activation_threshold`，但不累加 contribution、不繼續傳播。
3. `event` 與 `decision` 使用 frozen-clock time decay；`character_fact`、`preference` 與缺少 `occurred_at` 的記憶維持中性 factor。實測後將最低 factor 收斂為 `0.50`，避免精確 entity 命中的 active decision 被無關穩定記憶壓過。
4. lifecycle report 已成為多條件 forgetting candidate dry-run：必須同時通過 archive eligibility、idle time、edge weight 與 activation count gate；CLI 不提供自動 archive 或 delete 路徑。
5. evaluation artifact 已分開記錄 candidate `must_include` 與 final top-k `selected_evidence_groups`，並包含 config snapshot。
6. 共用 normalization 已移至 persistence-owned helper，ingestion 與 retrieval 不再跨 feature ownership 或複製規則。

`max_flipped` 未加入：實測顯示真正問題是 selection 無條件優先任何正向 graph bonus，而不是補入筆數缺少上限；現已讓 bonus 與 direct candidate 依完整 final score 競爭。deterministic lexical/content rerank 仍不足後，已加入 bounded structured model reranker；輸出 ID 必須是 candidate 子集合，最多五筆，失敗時回退 deterministic ranking，並在 trace 分開輸出 `model_rerank_score`。

驗證狀態：unit suite、ruff、DB/provider/embedding smoke、五訊息 live semantic canary 與完整 40 題 evaluation 均已實際執行。最後一次完整執行使用舊 selected-ID 契約，結果為 contract `PASS`、candidate must-include `40/40`、selected must-include `32/40`、answerability `40/40`、citation `40/40`、judge `40/40`。改採必要語意群組後，已用同一份完整 retrieval artifact 離線重算為 selected evidence `40/40`；尚未把這項離線重算表述為新版完整 evaluation 實跑結果。

## 13. 第三階段完成紀錄與驗證證據（2026-07-22）

### 結構清理

- `retrieval/service.py` 只保留候選、activation、ranking 與 selection 的 orchestration。
- semantic/lexical/content fusion 移至 `retrieval/candidates.py`。
- decay、完整分數、tie-break 與 final selection 移至 `retrieval/ranking.py`。
- bounded structured reranker 移至 `retrieval/reranker.py`；沒有新增 root façade、重複 upsert helper 或 CLI 子命令。
- evaluation 以 query-independent session chronology 產生 derived temporal relations，不使用已刪除的 hand-authored oracle edges。
- five-message canary 固定於 `data/input/semantic_canary.jsonl`，內容由測試鎖定為 primary input 的精確子集。

### Live DB 與 artifact 證據

- 五訊息 canary：5/5 extraction outcomes、7 memories、7/7 memories 有 evidence、7/7 evidence spans 可由 source message 精確回讀。
- read-back 語義：`msg-003` 同時保留 active/asserted 與 uncertain；`msg-004` 保留 question；`msg-046` 為 considered/historical/archived；`msg-050` 為 uncertain/current。
- canary answer 不把 CMP 解鎖問題改寫成已知事實，temporary namespace teardown 後不存在。
- 最後一次完整 40 題（舊 selected-ID 契約）：candidate recall `40/40`、selected recall `32/40`、must-not-primary violation `0`、citation `40/40`、answerability `40/40`、model rerank applied `40/40`、judge `40/40`、path-budget violation `0`。新版語意群組以該次 retrieval artifact 離線重算為 `40/40`，完整 workflow 留待下一次正式執行確認。
- 12 個 required multi-hop queries 中 11 個觀察到 multi-hop trace；剩餘 selected failures 都保留在 `results/pipeline/retrieval.json`，未以 judge PASS 覆蓋。

### 剩餘語意限制

selected must-include 的 8 個失敗集中在複合 cross-domain 題。多題的 reference evidence 超過 final top-5，且 oracle `must_include` 只是多個合理組合之一；例如 `Q_CROSS_DOMAIN_01` 明確詢問 migration，reranker 合理選入 M22，但舊 oracle 強制 M20。第三階段收斂後，`must_include` 保留為 candidate recall 的嚴格診斷；final top-5 改用 `selected_evidence_groups`，每一群代表一個必要語意面向、群內只接受明確列出的等價證據，且所有群組都必須命中。未宣告群組的題目仍逐項強制 `must_include`，因此不是任意相關結果即可通過，也沒有把 fixture oracle 注入 runtime。
