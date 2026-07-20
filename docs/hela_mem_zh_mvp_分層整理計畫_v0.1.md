# `hela_mem_zh_mvp` 分層整理計畫 v0.1

## 1. 決策摘要

本次建議採用「功能切片（feature-first）＋集中式基礎設施」整理
`src/hela_mem_zh_mvp`，而不是只把現有檔案搬進 `services/`、`utils/`，也不採用會產生大量介面與空殼檔案的完整 DDD／Clean Architecture。

目標是讓開發者從需求直接找到程式：

- 寫入、抽取、候選與分類更新在 `ingestion/`。
- Hebbian 檢索、回答與 learning 在 `retrieval/`。
- fixture、judge 與三種評估流程在 `evaluation/`。
- Google／embedding 等外部服務在 `providers/`。
- SQLAlchemy model 與資料存取 primitive 在 `persistence/`。
- `cli.py` 只解析參數、建立依賴並呼叫 use case。

這是一項結構重構，不改資料 schema、演算法、CLI 命令、artifact 格式或既有資料。任何會改變 transaction 時點、provider 呼叫時點、retrieval 排名或 resolution 語意的改善，必須另立變更，不與搬移混在同一階段。

---

## 2. 現況盤點

### 2.1 規模與熱點

目前 package 有 25 個 Python 檔案，全部位於同一層。較大的檔案如下：

| 檔案 | 行數 | 現有責任 |
|---|---:|---|
| `memory_store.py` | 567 | namespace、source message、entity、candidate、memory、resolution、edge、reset/purge |
| `models.py` | 362 | 1 個 declarative Base 與 12 個 SQLAlchemy table model |
| `single_evaluation.py` | 332 | namespace lifecycle、查詢、回答、judge、artifact、QA |
| `schemas.py` | 280 | ingestion、resolution、answer、judge、fixture 等 22 個 enum/model |
| `retriever.py` | 257 | query embedding、semantic seed、Hebbian spread、ranking、selection、run persistence |
| `pipeline.py` | 243 | ingest、ask、完整 evaluation、artifact |
| `cli.py` | 210 | parser、dependency wiring、所有命令分派、resolution report query |

最明顯的風險是 `memory_store.write_ingestion()`：359 行、cyclomatic complexity 20、cognitive complexity 42，直接呼叫或操作至少 24 個 symbol。它同時處理：

1. source message idempotency。
2. ingestion run 建立與統計。
3. entity／alias upsert。
4. embedding 與 scoped candidate search。
5. candidate staging。
6. deterministic resolution。
7. memory／provenance／entity link 寫入。
8. extractor relations 與 resolution apply。
9. semantic／temporal edge 建立。

這是應優先解開的責任結，而不是先處理小檔案。

### 2.2 真實入口與流程

目前公開執行入口是：

```text
pyproject: hela-mem = hela_mem_zh_mvp.cli:main
main.py: 無參數時轉成 cli main(["run"])
```

主要呼叫路徑：

```text
CLI
├─ ingest -> pipeline.ingest -> extractor -> memory_store.write_ingestion
├─ ask -> pipeline.ask -> Retriever.retrieve -> answerer -> optional co_retrieval
├─ evaluate -> pipeline.evaluate -> fixture seed -> retrieve -> answer -> judge
├─ live-resolver-evaluate -> live_evaluation -> pipeline.ingest
└─ run/single-e2e-evaluate -> single_evaluation -> ingest + retrieve + answer + judge
```

目前還有一個命名重疊：`pipeline.evaluate()` 與 `evaluator.evaluate()` 是不同評估流程，但名稱無法表達差異；`evaluator.py` 也沒有被現行 CLI 直接使用。整理時要先確認其保留目的，再改成語意明確的名稱，不可直接刪除。

### 2.3 已知契約與基線

本計畫以以下現況作為相容基線：

- `uv run pytest`：23 passed。
- `uv run ruff check src tests main.py`：passed。
- console script 仍為 `hela-mem`。
- `main.py` 無參數仍執行 `run`。
- `embedding_only`／`hebbian` 排名、`activation_path`、edge direction 與 evaluation/learning 邊界不變。
- `.env`、`config.yaml` 與現行 PostgreSQL/pgvector schema 不變。
- `results/pipeline`、`results/live_resolver` 與 `results/summary.json` 的現行產物路徑和欄位不變。

README 與舊 v0.4 文件仍出現現行 parser 不支援的 `seed`、`query --mode`、`smoke --component` 等範例。這是文件漂移，應在最後一階段依現行 CLI 同步；不可為了符合舊文件而在本次重構中偷偷改 public behavior。

---

## 3. 方案比較

### 方案 A：只新增目錄並原檔搬移

例如把 `memory_store.py` 搬到 `stores/`、`retriever.py` 搬到 `services/`。優點是快、風險低；缺點是 359 行 use case 與 persistence 仍混在一起，只改善視覺，不改善維護邊界。

### 方案 B：完整水平分層

以 `domain/`、`application/`、`infrastructure/`、`interfaces/` 組成嚴格 Clean Architecture。依賴方向最清楚，但對目前規模會產生大量 protocol、DTO、repository wrapper，查一個功能需跨多個目錄，反而增加認知成本。

### 方案 C：功能切片＋集中基礎設施（採用）

把會一起修改的功能放在同一 package，SQLAlchemy 與 provider adapter 集中管理。只有跨功能且穩定的東西才放共用層。此方案最接近目前 code graph 的實際群聚，也能用漸進 façade 搬移。

---

## 4. 目標目錄

```text
src/hela_mem_zh_mvp/
├─ __init__.py
├─ cli.py                         # parser、composition、exit code；不放 SQL query
├─ config.py                      # config.yaml schema
├─ settings.py                    # .env / runtime settings
├─ db.py                          # engine、target guard、session factory
│
├─ providers/
│  ├─ __init__.py
│  ├─ base.py                     # StructuredProvider protocol、provider errors/probes
│  ├─ google.py                   # GoogleProvider adapter
│  └─ embedding.py                # EmbeddingClient、dimension/normalization
│
├─ persistence/
│  ├─ __init__.py
│  ├─ models.py                   # SQLAlchemy mappings；第一輪先維持單一 metadata 邊界
│  ├─ namespaces.py               # get/reset/purge namespace
│  ├─ sources.py                  # source message、ingestion run primitives
│  ├─ entities.py                 # entity/alias upsert、memory-entity link
│  ├─ memories.py                 # candidate/memory persistence primitives
│  ├─ resolutions.py              # lock、cycle、decision/status atomic apply
│  ├─ edges.py                    # edge upsert、direction/symmetry、reinforcement
│  └─ retrieval_runs.py           # RetrievalRun/RetrievalItem persistence
│
├─ ingestion/
│  ├─ __init__.py
│  ├─ contracts.py                # SourceMessage、ExtractionResult 與相關 enum/model
│  ├─ input.py                    # conversations.jsonl loading、content hash
│  ├─ extractor.py                # provider output -> validated extraction
│  ├─ normalization.py            # alias/topic/state/canonical normalization
│  ├─ candidates.py               # scoped pgvector candidate search
│  ├─ resolver.py                 # decision rules、snapshot、post-validation
│  └─ service.py                  # ingest use case；唯一流程編排入口
│
├─ retrieval/
│  ├─ __init__.py
│  ├─ contracts.py                # mode/scope/run mode、ranked/result DTO
│  ├─ service.py                  # semantic seed、spread、score、select
│  ├─ answerer.py                 # selected citations -> answer contract
│  └─ learning.py                 # co_retrieval learning gate
│
└─ evaluation/
   ├─ __init__.py
   ├─ contracts.py                # fixture、judge、report contracts
   ├─ fixtures.py                 # fixture load/seed
   ├─ judge.py                    # AI judge adapter use case
   ├─ artifacts.py                # 統一 JSON writer、path constants、redaction boundary
   ├─ standard.py                 # 現 pipeline.evaluate
   ├─ retrieval_ab.py             # 現 evaluator.evaluate；確認用途後命名
   ├─ live_resolver.py            # 現 live_evaluation
   └─ single_e2e.py               # 現 single_evaluation
```

刻意不做以下過度拆分：

- `models.py` 第一輪不依 table 拆檔，避免 SQLAlchemy relationship、Alembic metadata 與 import cycle 同時變動。
- `config.py`、`settings.py`、`db.py` 都很小且是所有入口都會看到的平台邊界，保留頂層比再包一層更直接。
- 不建立只有一個 method 的 repository class；先用具型別的 module function，確有第二種 storage adapter 時才抽 protocol。

---

## 5. 依賴規則

允許的方向：

```text
cli
 ├─ ingestion
 ├─ retrieval
 ├─ evaluation
 ├─ providers
 └─ config/settings/db

evaluation ──> ingestion + retrieval + providers + persistence
ingestion  ──> provider base + persistence + config
retrieval  ──> provider base + persistence + config
providers  ──> settings + feature contracts
persistence ─> feature contracts/config（只在型別確實需要時）
```

禁止：

- `persistence` import `ingestion.service`、`retrieval.service`、`evaluation` 或具體 `GoogleProvider`。
- `providers` import SQLAlchemy model、session 或 feature service。
- `ingestion` 與 `retrieval` 互相 import；共同資料只能經明確 contract 或 persistence model。
- `cli.py` 直接執行 `select()` 或知道 table 欄位。
- `evaluation` 的 artifact writer 被正式 runtime write path 反向依賴。
- 新增泛用 `utils.py`、`helpers.py`、`common.py` 作為無邊界收容區。

使用標準庫 `ast` 寫 architecture test 掃描 import 即可，暫不新增 dependency。規則違反時 pytest 應直接列出來源模組與禁止目標。

---

## 6. 現有模組搬移對照

| 現有模組 | 目標 | 處理方式 |
|---|---|---|
| `provider.py` | `providers/base.py`、`providers/google.py` | protocol 與 adapter 分開 |
| `embedding.py` | `providers/embedding.py` | 原行為搬移 |
| `models.py` | `persistence/models.py` | 先整檔搬移並保留 metadata import 相容 |
| `memory_store.py` | `persistence/{namespaces,sources,entities,memories}.py`＋`ingestion/service.py` | 最主要拆分目標 |
| `edges.py` | `persistence/edges.py`、`retrieval/learning.py` | edge primitive 與 learning policy 分開 |
| `resolution_store.py` | `persistence/resolutions.py` | 原子套用與 lock 留在 DB 邊界 |
| `ingestion.py` | `ingestion/input.py` | 避免與 package 同名 |
| `extractor.py` | `ingestion/extractor.py` | 原行為搬移 |
| `normalization.py` | `ingestion/normalization.py` | 目前只服務寫入/resolution |
| `candidate_search.py` | `ingestion/candidates.py` | 候選屬 resolution write path |
| `resolver.py` | `ingestion/resolver.py` | deterministic/AI decision policy |
| `retriever.py` | `retrieval/contracts.py`＋`retrieval/service.py`＋`persistence/retrieval_runs.py` | DTO、排序與 trace persistence 分開 |
| `answerer.py` | `retrieval/answerer.py` | 原行為搬移 |
| `fixtures.py` | `evaluation/fixtures.py` | fixture 不是 runtime input |
| `judge.py` | `evaluation/judge.py` | 僅評估依賴 |
| `evaluator.py` | `evaluation/retrieval_ab.py` | 先確認是否仍為受支援流程 |
| `live_evaluation.py` | `evaluation/live_resolver.py` | 語意化命名 |
| `single_evaluation.py` | `evaluation/single_e2e.py` | 保留 CLI alias |
| `pipeline.py` | `ingestion/service.py`、`retrieval/service.py`、`evaluation/standard.py` | 移除「所有流程都放 pipeline」責任 |
| `schemas.py` | 各 feature 的 `contracts.py` | 依 consumer 搬移，不建立全域 DTO 倉庫 |
| `cli.py` | `cli.py` | 留作 thin composition root |
| `config.py`、`settings.py`、`db.py` | 原位 | 小而穩定，不為整齊而多包一層 |

---

## 7. 關鍵拆分設計

### 7.1 `write_ingestion()`

最終 `ingestion.service.ingest_extraction()` 只保留流程編排；每個步驟有可獨立測試的輸入輸出：

```text
validate/store source messages
  -> start ingestion run
  -> upsert extracted entities
  -> for each extracted memory:
       normalize identity/topic/state
       obtain embedding
       find scoped candidates
       stage candidate
       resolve decision
       persist memory/provenance/entity links
       persist decision
  -> apply explicit relations
  -> derive semantic/temporal edges
  -> complete ingestion run and counts
```

拆分準則：

- persistence function 只做查詢／寫入／`flush`，不自行 `commit`。
- decision function 不寫 DB；輸入 snapshot，輸出 typed decision。
- orchestration 保留現有順序、failure propagation 與 caller-owned transaction。
- `created/merged/superseded/contradicted/ignored/deferred` 統計欄位逐項 golden-test。
- 第一輪仍保留目前 embedding 呼叫相對 transaction 的位置。若要移到 transaction 外，因為會改 concurrency/failure semantics，另開後續設計。

### 7.2 retrieval

目前 `Retriever` 同時做純計分與 DB trace persistence。拆成：

- `retrieval.service`：semantic candidates、one-hop spread、score、selection。
- `retrieval.contracts`：`RankedMemory`、`RetrievalResult`、mode/scope。
- `persistence.retrieval_runs`：保存 run/item trace。
- `retrieval.learning`：只有明確 `ask --learn` 且 citation contract 通過時強化 co-retrieval。

排序、tie-break、seed 保留、contradiction context、`final_top_k=5` 與 `activation_path` 必須用現有 fixture 做逐欄比較，不能只檢查 selected id。

### 7.3 evaluation

三種 workflow 分檔，但共用：

- artifact path 與 JSON serialization。
- fixture loader。
- answer/judge error normalization。
- deterministic check helpers。
- summary status 計算。

不得把三種 workflow 硬合成一個帶十多個旗標的函式。共用穩定 primitive，保留各自 namespace lifecycle 與結果 schema。

### 7.4 CLI

`cli.py` 的目標是每個 branch 只做：

1. 驗證參數。
2. 建立 settings/config/provider/session。
3. 呼叫一個 command handler。
4. 輸出 JSON 並映射 exit code。

目前 resolution report 直接 `select(MemoryCandidate/MemoryResolutionDecision)` 的部分移到 application/persistence query function。parser 的 command、option、default、exit code 與 stdout/stderr shape 以 characterisation test 鎖定。

### 7.5 相容 façade

搬移期間保留薄 façade，讓舊 import 暫時有效：

```python
# hela_mem_zh_mvp/retriever.py（過渡期）
from .retrieval.contracts import RankedMemory, RetrievalResult
from .retrieval.service import Retriever

__all__ = ["RankedMemory", "RetrievalResult", "Retriever"]
```

相同策略套用 `models.py`、`schemas.py`、`provider.py`、`embedding.py`、`pipeline.py`。façade 不包含邏輯、不雙向 import、不發 runtime warning；repo 內部 import 全部遷移並通過後，再以一次獨立 cleanup 移除。若 package 已有 repo 外 consumer，façade 至少保留一個版本。

---

## 8. 分階段實作

### Phase 0：凍結行為基線

產出：

- 保存 `pytest`、Ruff 與 `git diff` 基線。
- 新增 CLI parser/exit-code characterisation tests。
- 對 ingestion counts、resolution decisions、retrieval trace、evaluation summary 建立 golden assertions。
- 新增 architecture import test，但先只 report，待目標 package 建立後才 enforce。
- 列出受支援 public imports 與 CLI/artifact 契約。

Gate：沒有足以偵測 ranking、edge、decision、artifact 漂移的測試前，不開始搬 `memory_store.py`。

Rollback：只新增測試，無 runtime 變更。

### Phase 1：建立 package 骨架並搬低耦合 adapter

順序：

1. 建立 `providers/`，拆 `StructuredProvider` 與 `GoogleProvider`。
2. 搬 embedding client。
3. 建立 `ingestion/`、`retrieval/`、`evaluation/`、`persistence/` 空邊界與 `__init__.py`。
4. 更新 repo 內 import；頂層 façade re-export。
5. 啟用第一批 architecture rules。

Gate：23 個既有測試、Ruff、`python -m hela_mem_zh_mvp.cli --help`、console script help 全過；provider smoke 不在這階段強制連外。

Rollback：單純 revert 該 phase；舊 façade 路徑仍在。

### Phase 2：切分 contracts 與 persistence 邊界

順序：

1. 將 `schemas.py` 的型別按 consumer 搬到各 feature `contracts.py`。
2. 將 SQLAlchemy mappings 搬到 `persistence/models.py`，保持同一 `Base.metadata`。
3. 更新 Alembic import，確認 metadata table/constraint/index 集合完全相同。
4. 搬 namespace、edge、resolution primitives。
5. 保留頂層 `schemas.py`、`models.py` façade。

Gate：

- Alembic autogenerate comparison 不應產生 schema diff。
- model table name、column、FK、index、enum/string value 不變。
- migrations import/upgrade path 可載入。
- architecture test 禁止 persistence 反向依賴 feature service/provider adapter。

Rollback：不產生新 migration；若 autogenerate 有差異立即停止，不帶差異進下一階段。

### Phase 3：拆解 ingestion/resolution

順序：

1. 先抽 source、entity、candidate、memory primitive，不改原呼叫順序。
2. 將純 normalization、candidate key、snapshot 與 decision 搬到 ingestion feature。
3. 將 `write_ingestion()` 改成 thin compatibility wrapper，委派 `ingestion.service`。
4. 抽 relation application 與 derived edge creation 為具名步驟。
5. 為每步補 unit test，整體以 PostgreSQL test namespace 做 transaction/integration test。

Gate：

- 同一 fixture 輸入的 row counts、memory status、decision action、target ids、edge direction/weight/metadata 與回傳 counts 相同。
- duplicate message id/hash 行為相同。
- deferred endpoint 不建立 resolved edge。
- 任一步驟 fault injection 後 transaction 完整 rollback。
- `write_ingestion()` 本體降至約 40 行以內，僅做委派或 orchestration。

Rollback：每次只抽一個 primitive；compatibility wrapper 可立即切回前一實作。

### Phase 4：拆解 retrieval/ask

順序：

1. 搬 DTO/enums。
2. 將 scoring/selection 與 run persistence 分開。
3. 搬 answerer 與 learning gate。
4. `pipeline.ask()` 改成 façade。

Gate：針對 32 題 fixture 比對 selected ids、candidate/final rank、semantic/hebbian/status/final score、source、activation path；evaluation mode 的 edge checksum 不變，learning 只有通過 citation gate 才更新。

Rollback：保留 `Retriever` façade與原 method signature。

### Phase 5：整理 evaluation 與 CLI

順序：

1. 建立共用 artifact writer 與 summary helper。
2. 分別搬 standard、live resolver、single E2E，不合併 namespace lifecycle。
3. 盤點 `evaluator.py` 的現行 consumer；有 consumer 則命名 `retrieval_ab`，無 consumer仍先保留 façade並標示待移除，不直接刪。
4. 建立 command handler，移除 CLI 內 SQL query。
5. 保留所有命令、alias、defaults、exit codes。

Gate：相同測試輸入下 artifact 檔名、schema keys、status 判定與 exit code 相同；stdout 保持 machine-readable JSON，stderr 錯誤分類不變。

Rollback：CLI 仍可切回 façade import；各 workflow 一次只搬一個。

### Phase 6：清理 façade、文件與索引

產出：

- `rg` 確認 repo 內無舊 import。
- 移除確定沒有外部 consumer 的 façade；其餘列出 deprecation window。
- 更新 README 與舊計畫中的實際 CLI 範例。
- 更新 package tree、資料流圖與新模組責任。
- 重新建立 codebase-memory index，確認真實群聚與目標邊界一致。

Gate：完整 offline suite、Ruff、architecture tests、package build、CLI smoke；live DB/provider 測試另列結果，不以單純 `/health` 或 import success 宣稱完成。

---

## 9. 驗證矩陣

| 層級 | 必驗項目 |
|---|---|
| Import | wheel 安裝後新舊受支援 import 都可載入，沒有 circular import |
| Unit | normalization、resolution matrix、edge direction、ranking、citation gate |
| Contract | CLI parser/default/exit code、DTO JSON、artifact schema、config/env |
| Persistence | table metadata no diff、namespace isolation、idempotency、rollback |
| Integration | ingest -> resolve -> retrieve -> answer trace；evaluation 不改 edge |
| Regression | 32 題 fixture 的完整 retrieval trace 比較，不只比較總分 |
| Static | Ruff、architecture import rules、無新 `utils/common` 收容區 |
| Packaging | `uv build`，console script `hela-mem --help` 與 `python -m` 入口 |
| Live（條件式） | DB target guard、embedding dimension 2560、provider structured output |

建議每一 phase 的最小命令：

```powershell
uv run pytest
uv run ruff check src tests main.py
uv build
uv run python -m hela_mem_zh_mvp.cli --help
uv run python main.py --help
```

需要 live 驗證時，先確認 `.env` 指向專用 `hebbian_memory_mvp` database，再使用 test namespace；不得對未知或 production namespace 做 reset/purge。

---

## 10. 風險與控制

| 風險 | 控制 |
|---|---|
| 大量 import move 造成 circular import | 先定依賴規則；底層先搬；每 phase 保留單向 façade |
| SQLAlchemy model 搬移造成 Alembic 誤判 | 保留單一 Base/metadata；autogenerate 必須 no diff |
| transaction 行為被「順手改善」 | 將 commit/flush/provider call 時點列為 parity contract；改善另立變更 |
| evaluation 共用化改壞 namespace cleanup | 三 workflow 分開搬，共用 artifact primitive，不共用 lifecycle |
| 舊 import 或 CLI consumer 中斷 | characterisation tests＋過渡 façade＋獨立 cleanup phase |
| 檔案變多但仍難找 | 每個檔案必須對應 feature 責任；避免一函式一檔與泛用 utils |
| 只通過 unit test 卻資料語意漂移 | 比對 DB row/state/edge、retrieval trace 與 artifact schema |
| README 與程式再次漂移 | CLI help/README command smoke 納入測試或 release checklist |

---



## 12. 完成定義

只有以下條件全數成立，才算「分層整理完成」：

1. 頂層只保留 entry/config/runtime platform；功能檔案均落在明確 feature package。
2. `write_ingestion()` 不再同時承擔 DB primitive、決策、edge 與流程細節。
3. CLI 不含 SQL query，provider adapter 不依賴 DB，persistence 不反向依賴 workflow。
4. architecture tests 能阻止違反依賴方向的新增 import。
5. 現有 CLI、public import（在承諾的相容期內）、DB schema、資料語意與 artifact contract 無破壞。
6. 32 題 fixture 的完整 retrieval trace 與目前基線一致，或每個差異都有獨立、經核准的功能變更說明。
7. unit、contract、integration、Ruff、build 與入口 smoke 均有實際執行紀錄。
8. README、模組樹與資料流文件對應實際程式，不再引用不存在的 CLI 參數。
9. codebase-memory 重新索引後，群聚主要沿 `ingestion`、`retrieval`、`evaluation`、`providers`、`persistence` 邊界形成。

此計畫的核心判準不是「資料夾變整齊」，而是修改一個寫入、檢索或評估需求時，能在單一功能區域完成大部分工作，且依賴方向與回歸證據能阻止責任再次混回同一檔案。
