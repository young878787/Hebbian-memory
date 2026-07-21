# Hebbian 中文角色記憶 MVP 實作與測試計畫

> 版本：v0.4  
> 日期：2026-07-19  
> 定位：小型概念驗證，不建立完整長期記憶平台

---

## 壹、計畫目標

本階段只驗證四件事：

1. 中文 Embedding 能否準確找到直接相關的角色設定或對話事件。
2. Hebbian spreading activation 能否從一筆種子記憶，聯想到文字不完全相似、但經驗上相關的其他記憶。
3. 系統能否正確處理「曾經這樣說，後來改變或沒有執行」的矛盾與更新記憶。
4. PostgreSQL 能否清楚保存記憶、關聯、狀態與每次召回路徑，方便分析結果。

本階段不追求完整聊天角色，也不先實作大型 Dream Engine、遺忘機制或自動對話摘要。

核心研究問題：

> 在相同中文 Embedding 與相同 Top-K 預算下，加入一層 Hebbian 聯想後，是否能更完整地召回與當前問題相關的事件脈絡，而不是只找到文字最相似的一筆記憶？

---

## 貳、要驗證的核心情境

### 情境：顯卡購買意圖曾經出現，但最後沒有購買

記憶事件：

```text
M1：使用者曾提到想買 RTX 3090。
M2：後來因價格與風險，考慮先借用或租借顯卡測試。
M3：最後沒有購買 RTX 3090。
M4：近期改為研究 CMP 170HX 解鎖大顯存的可能性。
```

建立的關聯：

```text
M1 ─ temporal ─ M2
M2 ─ temporal ─ M3
M3 ─ temporal ─ M4
M1 ─ semantic ─ M4
M3 ─ supersedes ─ M1
```

測試問題：

```text
我之前不是說過想買顯卡嗎？
```

理想召回不是只找到：

```text
M1：使用者曾提到想買 RTX 3090。
```

而是同時找出：

```text
M1：曾經有購買意圖。
M3：最後沒有購買。
M4：目前研究方向已轉向 CMP 170HX。
```

理想回答概念：

```text
你之前確實提過想買 RTX 3090，但後來沒有真的購買，
之後把方向轉到先研究或取得大顯存、低成本的 CMP 170HX。
```

這個情境能同時驗證：

- 歷史事件召回。
- 跨事件聯想。
- 時間順序。
- 意圖與實際結果不同。
- 舊記憶被新狀態更新，但沒有完全刪除。

---

## 參、系統定位

本 MVP 是：

```text
PostgreSQL
+ pgvector
+ 中文 Embedding
+ Hebbian adjacency edges
+ 矛盾／更新狀態
+ 可追蹤召回紀錄
```

不是：

```text
完整 GraphRAG
完整 HeLa-Mem 移植
大型知識圖譜
多 Agent 記憶平台
```

建議命名：

```text
HeLa-Mem-inspired Chinese Associative Memory MVP
```

---

## 肆、資料庫選擇

本階段改用 PostgreSQL，比 JSON 或純記憶體結構更適合，原因包括：

- 可直接使用 pgvector 儲存中文 Embedding。
- 能保留每筆記憶的歷史狀態。
- 容易查詢 superseded、contradiction 與 temporal 關係。
- 可以記錄每次 retrieval 的候選、分數與路徑。
- 後續加入多使用者、session、索引與 API 時不需要重做資料層。

建議使用：

```text
PostgreSQL 16+
pgvector
Python 3.11+
SQLAlchemy 2
psycopg 3
Alembic
pgvector（Python package）
Pydantic Settings
python-dotenv
OpenAI Python SDK（呼叫本機 vLLM）
Google Gen AI SDK（`google-genai`）
pytest
Qwen3-Embedding-4B（由本機 vLLM 提供）
```

### 服務與環境設定

本 MVP 固定採用以下服務邊界：

| 能力 | 服務 | 連線／模型合約 | 用途 |
|---|---|---|---|
| 資料庫 | PostgreSQL | `192.168.137.2:5432` 上的專用 database `hebbian_memory_mvp`，必須啟用 `pgvector` | 只儲存本次 MVP 的記憶、關聯、召回與評估紀錄 |
| AI provider | Google AI Studio API | `GOOGLE_API_KEY` + `GOOGLE_MODEL` | provider smoke test，以及需要時的單輪回答生成 |
| Embedding | 本機 vLLM | `http://localhost:8001/v1` + `EMBEDDING_MODEL` | 查詢與記憶文字向量化 |

重要設定統一由專案根目錄的 `.env` 管理，所有 CLI、Alembic migration、資料匯入與 smoke test 都必須使用同一份 `.env` 載入流程。`.env` 只保留在本機，不提交版本控制；另提供可提交的 `.env.example`，只放變數名稱、空值或非敏感預設值。不得把 API key、資料庫密碼寫入文件、程式碼或 log。預設設定如下：

```dotenv
GOOGLE_API_KEY=<local-secret>
GOOGLE_MODEL=gemini-3.1-flash-lite
# 備選模型；只有切換 provider 測試時才使用
# GOOGLE_MODEL=gemma-4-26b-a4b-it
REASONING_MODEL=0
GOOGLE_THINKING_LEVEL=minimal

EMBEDDING_BASE_URL=http://localhost:8001/v1
EMBEDDING_MODEL=Qwen3-Embedding-4B
EMBEDDING_API_KEY=local

POSTGRES_HOST=192.168.137.2
POSTGRES_PORT=5432
POSTGRES_DB=hebbian_memory_mvp
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<local-secret>
```

設定檔規則：

1. `.env` 是本機執行時的唯一 secrets source of truth；統一由 `pydantic-settings` 載入，shell environment 優先於 `.env`，不得在不同入口各自定義名稱或預設值。
2. `.env.example` 必須同步列出上述所有變數，但 `GOOGLE_API_KEY` 與 `POSTGRES_PASSWORD` 保持空值。
3. `.gitignore` 必須忽略 `.env`、`.env.*.local` 等本機 secrets 檔，但保留 `.env.example`。
4. 啟動前先檢查 `.env` 是否存在及必要變數是否非空；錯誤訊息只能指出變數名稱，不得輸出變數值。
5. `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 是資料庫連線的唯一來源。程式使用 SQLAlchemy `URL.create()` 組合 DSN，不在 `.env` 重複保存 `DATABASE_URL`，避免特殊字元與雙份設定漂移。

這些設定的責任邊界如下：

1. `GOOGLE_MODEL` 只負責生成／provider 測試；不得用 Google 模型代替 embedding、seed ranking 或 Hebbian edge 計算。
2. `REASONING_MODEL=0` 代表關閉額外 reasoning model stage；runtime 不應把 `0` 當成可呼叫的模型名稱。Google 模型本身的 thinking 另外固定由 `GOOGLE_THINKING_LEVEL=minimal` 控制，兩者不得混為同一設定。
3. `EMBEDDING_BASE_URL` 預設使用 vLLM 的 OpenAI-compatible `/v1` 路徑。正式 migration 前必須從 `/v1/models` 與 `/v1/embeddings` 實際回應確認 served model id 及向量維度。
4. PostgreSQL 位址是遠端主機 `192.168.137.2`，不是本機 `localhost`；所有 migration 與測試都必須由同一個 Settings object 組合連線，且 database name 必須是 `hebbian_memory_mvp`。

非敏感演算法參數固定放在 `config.yaml`。第一版 canonical config 如下：

```yaml
retrieval:
  candidate_limit: 20
  seed_top_k: 3
  final_top_k: 5
  semantic_top_k: 3
  similarity_threshold: 0.72
  activation_alpha: 0.20
  spread_depth: 1
  max_neighbors_per_seed: 5
  tie_score_tolerance: 1.0e-6

learning:
  co_retrieval_increment: 0.02
  max_edge_weight: 1.0

status_adjustments:
  historical: {active: 0.00, superseded: 0.00, uncertain: -0.15, archived: -0.25}
  current: {active: 0.15, superseded: -0.35, uncertain: -0.20, archived: -0.40}
  general: {active: 0.05, superseded: -0.15, uncertain: -0.15, archived: -0.30}
```

正式 evaluation 不接受 CLI 臨時覆寫；若修改參數，必須將完整 config snapshot 寫入 `retrieval_runs.metadata`，確保結果可重現。

### MVP 專用資料庫邊界

本次建立獨立的 PostgreSQL database `hebbian_memory_mvp`，不與其他專案或既有業務資料表共用 database。資料表只放置本計畫需要的四個核心表：

```text
memories
memory_edges
retrieval_runs
retrieval_items
```

建立與套用 migration 的順序固定如下：

```text
1. 由 PostgreSQL 管理者在 192.168.137.2 建立 hebbian_memory_mvp。
2. 在 hebbian_memory_mvp 啟用 pgvector extension。
3. 建立只對 hebbian_memory_mvp 有權限的開發／應用帳號。
4. 由 Settings 組合 SQLAlchemy URL，連線驗證 `current_database() = 'hebbian_memory_mvp'`、`current_user = POSTGRES_USER`。
5. 由 Alembic 在此 database 建立四個核心表與索引。
6. 匯入本次約 40 筆測試記憶，不讀取或修改其他 database。
```

Migration 與啟動 smoke 必須在執行前檢查 `host`、`port` 與 `current_database()`；目標不是 `192.168.137.2:5432/hebbian_memory_mvp` 時直接停止，避免把測試表建立到錯誤資料庫。

### 啟動與 smoke gate

正式進入 Phase 1 前，先依序通過以下檢查：

```text
1. 192.168.137.2:5432 可連線，且 PostgreSQL 可載入 pgvector extension。
2. localhost:8001 可連線，/v1/models 包含 Qwen3-Embedding-4B 對應的 served model id。
3. /v1/embeddings 能對一段中文文字回傳非空向量，確認向量維度、數值與模型名稱。
4. GOOGLE_API_KEY 存在且 Google AI Studio 能以 GOOGLE_MODEL 完成最小請求。
```

其中第 1～3 項是 retrieval pipeline 的 hard gate；第 4 項只對 provider integration smoke test 必要，不阻塞 Embedding-only 與 Embedding + Hebbian 的離線評估。Google 呼叫失敗時要保留 HTTP status／provider error 類型，但遮蔽 key 與完整 prompt 內的敏感資料。

---

## 伍、資料模型

### 1. memories

儲存實際文字記憶。

```sql
CREATE TABLE memories (
    id UUID PRIMARY KEY,
    external_id VARCHAR(64) NOT NULL UNIQUE,
    content TEXT NOT NULL,
    memory_type VARCHAR(32) NOT NULL,
    topic VARCHAR(100),
    occurred_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    status VARCHAR(20) NOT NULL DEFAULT 'active',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,

    source_session_id VARCHAR(100),
    source_message_ids JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    embedding VECTOR(2560) NOT NULL,

    CONSTRAINT ck_memories_status
        CHECK (status IN ('active', 'superseded', 'uncertain', 'archived')),
    CONSTRAINT ck_memories_type
        CHECK (memory_type IN ('character_fact', 'event', 'preference', 'decision')),
    CONSTRAINT ck_memories_importance
        CHECK (importance BETWEEN 0.0 AND 1.0),
    CONSTRAINT ck_memories_confidence
        CHECK (confidence BETWEEN 0.0 AND 1.0)
);
```

本機 vLLM 已實測 `Qwen3-Embedding-4B` 回傳 `2560` 維，因此 migration 固定使用 `VECTOR(2560)`。Phase 0 仍須重跑 probe；若回傳維度不是 `2560`，直接停止，不得自動改 schema 或混寫不同維度。`id` 由應用程式以 `uuid.uuid4()` 產生；`external_id` 保留 `M1`、`M2` 這類人工可讀 fixture ID。

`status` 第一版只保留：

```text
active       目前仍有效
superseded   已被後續記憶更新
uncertain    尚未確認
archived     保留歷史，但一般不主動召回
```

### 2. memory_edges

Hebbian 與一般關聯都存在同一張表，但必須用 `edge_type` 分開。

```sql
CREATE TABLE memory_edges (
    source_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,

    edge_type VARCHAR(32) NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    activation_count INTEGER NOT NULL DEFAULT 0,
    last_activated_at TIMESTAMPTZ,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    PRIMARY KEY (source_id, target_id, edge_type),
    CONSTRAINT ck_memory_edges_type
        CHECK (edge_type IN (
            'temporal', 'semantic', 'co_retrieval',
            'supersedes', 'contradicts', 'supports'
        )),
    CONSTRAINT ck_memory_edges_weight
        CHECK (weight BETWEEN 0.0 AND 1.0),
    CONSTRAINT ck_memory_edges_activation_count
        CHECK (activation_count >= 0),
    CONSTRAINT ck_memory_edges_no_self_loop
        CHECK (source_id <> target_id)
);
```

第一版 edge types：

```text
temporal       同一事件序列
semantic       Embedding 語義相近
co_retrieval   反覆一起被召回
supersedes     新記憶取代舊狀態
contradicts    兩筆記憶互相矛盾
supports       一筆記憶支持另一筆結論
```

Edge 方向固定如下：

| edge type | 儲存方向 | spreading 行為 |
|---|---|---|
| `temporal` | 較早事件 → 較晚事件 | 沿時間向後展開 |
| `supersedes` | 新記憶 → 被取代的舊記憶 | current query 不由舊記憶反推新事實；狀態處理另查 inbound edge |
| `supports` | 證據記憶 → 被支持的結論 | 單向展開 |
| `semantic` | 對稱關係，交易內寫入 A→B 與 B→A | 可雙向展開 |
| `co_retrieval` | 對稱關係，交易內寫入 A→B 與 B→A | 可雙向展開 |
| `contradicts` | 對稱關係，交易內寫入 A→B 與 B→A | 只進 trace 與狀態判定，不提供正向加分 |

對稱 edge 的兩筆資料必須使用相同 weight、activation count 與 timestamp，並在同一 transaction 內建立或更新。spreading 一律讀 outbound edges；因此不得只寫對稱關係的單邊資料。

### 3. retrieval_runs

記錄每次測試。

```sql
CREATE TABLE retrieval_runs (
    id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    retrieval_mode VARCHAR(32) NOT NULL,
    run_mode VARCHAR(16) NOT NULL,
    query_scope VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_latency_ms REAL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT ck_retrieval_runs_mode
        CHECK (retrieval_mode IN ('embedding_only', 'hebbian')),
    CONSTRAINT ck_retrieval_runs_run_mode
        CHECK (run_mode IN ('evaluation', 'learning')),
    CONSTRAINT ck_retrieval_runs_scope
        CHECK (query_scope IN ('historical', 'current', 'general')),
    CONSTRAINT ck_retrieval_runs_latency
        CHECK (total_latency_ms IS NULL OR total_latency_ms >= 0)
);
```

### 4. retrieval_items

記錄每筆候選如何被召回。

```sql
CREATE TABLE retrieval_items (
    run_id UUID NOT NULL REFERENCES retrieval_runs(id) ON DELETE CASCADE,
    memory_id UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,

    candidate_rank INTEGER NOT NULL,
    final_rank INTEGER,
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    semantic_score REAL NOT NULL DEFAULT 0.0,
    hebbian_score REAL NOT NULL DEFAULT 0.0,
    status_adjustment REAL NOT NULL DEFAULT 0.0,
    final_score REAL NOT NULL,

    retrieval_source VARCHAR(32) NOT NULL,
    activation_path JSONB NOT NULL DEFAULT '[]'::jsonb,

    PRIMARY KEY (run_id, memory_id),
    UNIQUE (run_id, candidate_rank),
    UNIQUE (run_id, final_rank),
    CONSTRAINT ck_retrieval_items_source
        CHECK (retrieval_source IN ('seed', 'hebbian')),
    CONSTRAINT ck_retrieval_items_semantic_score
        CHECK (semantic_score BETWEEN 0.0 AND 1.0),
    CONSTRAINT ck_retrieval_items_hebbian_score
        CHECK (hebbian_score >= 0.0),
    CONSTRAINT ck_retrieval_items_ranks
        CHECK (
            candidate_rank >= 1
            AND (final_rank IS NULL OR final_rank >= 1)
            AND ((selected = TRUE AND final_rank IS NOT NULL)
                 OR (selected = FALSE AND final_rank IS NULL))
        )
);
```

`activation_path` 固定為 JSON array。seed 使用空 array；Hebbian candidate 至少包含一個 contribution：

```json
[
  {
    "source_external_id": "M1",
    "target_external_id": "M4",
    "edge_type": "semantic",
    "edge_weight": 0.24,
    "source_semantic_score": 0.81,
    "activation_alpha": 0.20,
    "contribution": 0.03888
  }
]
```

`contribution` 必須能由同一筆 path 的欄位重算；多個 seed 對同一 target 的 contribution 逐筆保留，不得只存最後加總。

### 5. Migration 與索引 contract

第一版資料量只有約 40 筆，為了結果可重現，向量檢索固定使用 pgvector exact cosine search：

```sql
SELECT *, 1 - (embedding <=> :query_embedding) AS semantic_score
FROM memories
ORDER BY embedding <=> :query_embedding
LIMIT :candidate_limit;
```

MVP 不建立 HNSW／IVFFlat approximate vector index。只建立以下 B-tree indexes：

```sql
CREATE INDEX ix_memories_status_occurred_at
    ON memories (status, occurred_at DESC);
CREATE INDEX ix_memory_edges_target
    ON memory_edges (target_id, edge_type);
CREATE INDEX ix_retrieval_items_run_selected
    ON retrieval_items (run_id, selected, final_rank);
```

Alembic `upgrade` 只能建立上述四張表與 indexes；`downgrade` 只能在 `hebbian_memory_mvp` 內依外鍵反向順序移除本次物件，不得操作其他 schema 或 database。

---

## 陸、矛盾記憶的處理原則

### 1. 不直接刪除舊記憶

例如：

```text
舊：使用者想買 RTX 3090。
新：使用者最後沒有購買 RTX 3090。
```

不能刪除舊記憶，因為使用者問：

```text
我之前是不是說過想買？
```

仍然需要找到舊事件。

正確處理：

```text
M1.status = superseded
M3.status = active
M3 ─ supersedes ─> M1
```

### 2. 區分「矛盾」與「狀態更新」

以下屬於狀態更新：

```text
以前想買 RTX 3090。
後來決定不買。
```

以下才是矛盾：

```text
同一時間一筆記憶說已經買了。
另一筆記憶說完全沒有買。
```

因此：

- `supersedes` 表示狀態隨時間改變。
- `contradicts` 表示資料來源或內容互相衝突。

### 3. Hebbian 權重不能決定事實真偽

Hebbian edge 只表示：

```text
這兩筆記憶經常一起被啟動或使用。
```

它不代表：

```text
這筆記憶比較正確。
```

目前有效狀態應由：

- 時間。
- status。
- supersedes relation。
- confidence。
- source quality。

共同判斷。

### 4. 召回歷史問題與目前狀態問題要分開

歷史問題：

```text
我以前是不是說過想買顯卡？
```

允許召回 `superseded` 記憶。

目前狀態問題：

```text
我現在還打算買 RTX 3090 嗎？
```

應優先召回 `active` 記憶，並將被取代的舊記憶只作背景說明。

---

## 柒、記憶寫入方式

第一版先手動整理測試記憶，不先加入 LLM 自動擷取。

每筆記憶保持 atomic：

不建議：

```text
使用者之前想買 3090，後來覺得太貴所以沒買，
現在改研究 CMP 170HX，還曾問過能不能解鎖 80GB。
```

建議拆成：

```text
M1：使用者曾想買 RTX 3090。
M2：使用者認為 RTX 3090 的價格與風險較高。
M3：使用者最後沒有購買 RTX 3090。
M4：使用者開始研究 CMP 170HX。
M5：使用者關心 CMP 170HX 是否能解鎖更大顯存。
```

這樣才能測出圖是否真的找到事件關係。

---

## 捌、建立關聯的方法

### 1. Temporal Edge

同一個事件串中的相鄰記憶：

```text
initial_weight = 0.25
```

### 2. Semantic Edge

新記憶寫入後，用 Embedding 找出最接近的舊記憶。

第一版：

```text
semantic_top_k = 3
similarity_threshold = 0.72
initial_weight = similarity × 0.30
```

### 3. 人工標記的邏輯關係

測試資料建立時人工標記：

```text
supersedes
contradicts
supports
```

這些不是自動學習權重，而是明確的資料關係。fixture 未指定 weight 時，`supersedes`、`contradicts`、`supports` 預設為 `1.0`；正式 fixture 建議仍顯式寫出 weight。

### 4. Co-retrieval Hebbian Edge

同一次查詢中共同被採用的記憶：

```text
weight = min(1.0, weight + 0.02)
activation_count += 1
```

第一版為避免測試結果被查詢順序污染，分成兩種模式：

```text
run_mode=evaluation：不更新權重
run_mode=learning：允許 co-retrieval reinforcement
```

正式比較 Embedding-only 與 Hebbian 時，必須使用 `run_mode=evaluation`。此模式仍會寫入 `retrieval_runs` 與 `retrieval_items`，但禁止新增或更新 `memory_edges`。`run_mode=learning` 必須由 CLI 顯式指定，且只能在 retrieval 結果與 trace 成功保存後，以獨立 transaction 更新 co-retrieval edges。

---

## 玖、召回流程

### Step 1：中文 Embedding 找種子

```text
query
  ↓
Qwen3-Embedding-4B
  ↓
pgvector cosine search
  ↓
seed_top_k = 3
```

Embedding client 固定使用 OpenAI Python SDK 呼叫本機 vLLM：

```text
base_url = EMBEDDING_BASE_URL
api_key = EMBEDDING_API_KEY
model = EMBEDDING_MODEL
timeout = 30 秒
max_attempts = 2
```

每個向量必須是 2560 維、有限數值，並在寫入前做 L2 normalization；空文字、空向量、NaN、Inf 或維度錯誤均視為 hard failure，不得寫入資料庫。

### Step 2：一層 Hebbian spreading

只從 seed 向外展開一層：

```text
hebbian_score(target)
  += semantic_score(seed)
   × edge_weight(seed, target)
   × activation_alpha
```

第一版：

```text
activation_alpha = 0.20
spread_depth = 1
max_neighbors_per_seed = 5
```

每個 seed 的 neighbors 依 `weight DESC`、`target external_id ASC` 排序後取前 5；不得依資料庫未指定順序截斷。

### Step 3：狀態與矛盾處理

取得候選後：

1. 查詢是否有 `supersedes` 關係。
2. 查詢是否存在 `contradicts` 關係。
3. 判斷問題是在問歷史還是目前狀態。
4. 對目前狀態題優先保留 active 記憶。
5. 對歷史題保留 superseded 記憶及其後續結果。

`query_scope` 不由 LLM 猜測。測試資料必須明確提供 `historical`、`current` 或 `general`；互動式 CLI 則要求 `--scope`，未指定時只可使用 `general`。

狀態調整固定如下：

| query scope | active | superseded | uncertain | archived |
|---|---:|---:|---:|---:|
| `historical` | `0.00` | `0.00` | `-0.15` | `-0.25` |
| `current` | `+0.15` | `-0.35` | `-0.20` | `-0.40` |
| `general` | `+0.05` | `-0.15` | `-0.15` | `-0.30` |

### Step 4：排名與選取公式

所有分數計算固定為：

```text
semantic_score = clip(1 - cosine_distance, 0.0, 1.0)

hebbian_score(target)
  = sum(
      semantic_score(seed)
      × edge_weight(seed, target)
      × activation_alpha
    )

embedding_only_final_score
  = semantic_score + status_adjustment

hebbian_final_score
  = semantic_score + hebbian_score + status_adjustment
```

`contradicts` edge 不加入 `hebbian_score`，只寫入 `activation_path` 並交由狀態規則判斷。分數相同時依序使用 `occurred_at DESC NULLS LAST`、`created_at DESC`、`external_id ASC` 打破平手，保證重跑結果一致。

比較時維持相同 `final_top_k = 5` 預算：

```text
Embedding-only：依 embedding_only_final_score 取前 5 筆。
Embedding + Hebbian：固定保留前 3 筆 seed，
                     再加入最多 2 筆不在 seed 中、
                     hebbian_score > 0 且 final_score 最高的候選。
```

### Step 5：輸出少量重點記憶

```text
seed memories：3 筆
Hebbian bonus：最多 2 筆
final_top_k：最多 5 筆
```

每筆必須輸出：

- content。
- status。
- semantic score。
- Hebbian score。
- relation path。
- 是否被其他記憶 supersede。

### Step 6：Google provider contract

Google provider 固定使用 `google-genai` 的同步 client：

```text
model = GOOGLE_MODEL
temperature = 0
max_output_tokens = 512
thinking_level = GOOGLE_THINKING_LEVEL
timeout = 30 秒
max_attempts = 2
```

`GOOGLE_MODEL` 預設 `gemini-3.1-flash-lite`；`gemma-4-26b-a4b-it` 僅作顯式切換的備選。Google provider 只取得最終選取的記憶、status 與 trace，不得看到測試 oracle。只對 timeout、5xx 與可重試 429 執行第二次嘗試；結構化輸出若因 JSON 或 Pydantic schema 驗證失敗，也以明確 JSON 修復指示執行一次重試。每次退避最多等待 10 秒。400／401／403、無效 model 與 quota exhaustion 不盲目重試。最終失敗時，smoke test 必須以非零 exit code 結束並保留原始錯誤類型；不得吞成空字串、空 JSON 或 retrieval failure。

---

## 拾、小型測試資料

### 建議規模

```text
角色固定設定：10 筆
顯卡事件記憶：8 筆
其他互動事件：12 筆
干擾記憶：10 筆

總計：約 40 筆
```

這個規模足夠看到概念，不需要一開始就建立 200 筆資料。

### 資料檔 contract

`character_memories.jsonl` 每行一筆 atomic memory：

```json
{
  "external_id": "M1",
  "content": "使用者曾想買 RTX 3090。",
  "memory_type": "event",
  "topic": "gpu",
  "occurred_at": "2026-01-10T12:00:00+08:00",
  "status": "superseded",
  "importance": 0.8,
  "confidence": 1.0,
  "source_session_id": "fixture-gpu-01",
  "source_message_ids": ["msg-001"],
  "metadata": {}
}
```

`memory_edges.jsonl` 每行一筆邏輯 edge：

```json
{
  "source_external_id": "M3",
  "target_external_id": "M1",
  "edge_type": "supersedes",
  "weight": 1.0,
  "metadata": {}
}
```

對稱 edge 在 fixture 只寫一次，由 loader 依 edge 方向 contract 於 transaction 內 materialize 雙向資料；directional edge 不得自動反向建立。

`test_queries.jsonl` 每行一題，oracle 只能由 evaluator 讀取，不得傳入 retriever 或 Google provider：

```json
{
  "query_id": "Q_GPU_HISTORY_01",
  "query": "我之前是不是說過想買顯卡？",
  "scope": "historical",
  "must_include": ["M1", "M3"],
  "nice_to_have": ["M4"],
  "must_not_primary": [],
  "expect_answerable": true,
  "category": "association"
}
```

所有 JSONL 在連線資料庫或呼叫模型前先以 Pydantic schema 驗證；未知欄位、重複 external ID、缺少 edge endpoint、非法 enum、非 ISO-8601 時間或超出 `0.0～1.0` 的分數均直接失敗。

### 測試問題

第一版只做 10 題。

#### A. 直接召回：2 題

```text
她喜歡什麼？
她不喜歡哪種環境？
```

確認中文 Embedding 基本正常。

#### B. Hebbian 聯想：3 題

```text
我之前是不是說過想買顯卡？
為什麼現在提到 CMP 170HX，可能會聯想到 RTX 3090？
我最近又提到大顯存，角色應該想到哪些過去內容？
```

#### C. 更新與矛盾：3 題

```text
我現在還打算買 RTX 3090 嗎？
我最後有沒有買那張顯卡？
目前優先研究的顯卡方向是什麼？
```

#### D. 無答案與干擾：2 題

```text
我有沒有買過 A100？
我曾經說過最喜歡 AMD 顯卡嗎？
```

系統不應因為話題相近而硬找一個答案。

---

## 拾壹、簡化評估方式

本階段不先做大型 benchmark，只使用人工可判讀的概念驗證。

### 1. Key Memory Hit

指定每題必須召回的核心記憶。

例如：

```text
問題：我之前是不是說過想買顯卡？

必須：
M1 曾想買 RTX 3090
M3 最後沒有購買

加分：
M4 目前改研究 CMP 170HX
```

### 2. Association Completeness

每題人工評分：

```text
0：只找到表面相似或錯誤記憶
1：找到一筆正確記憶，但缺少重要脈絡
2：完整找出事件、結果與目前狀態
```

### 3. Contradiction Correctness

```text
0：把舊記憶當成目前事實
1：同時找到新舊記憶，但沒有判斷哪筆有效
2：正確指出曾經如此、後來更新為另一狀態
```

### 4. No-answer Safety

無答案題：

```text
0：捏造或錯誤推斷
1：召回相近內容但明確說資料不足
2：正確判斷沒有相關記憶
```

### 5. Traceability

每次結果必須能看到：

```text
query
→ seed memory
→ edge
→ associated memory
→ final selected memories
```

### 6. Baseline 比較

每題跑兩次：

```text
A：Embedding-only
B：Embedding + Hebbian
```

只比較：

- 是否多找回必要脈絡。
- 是否導入錯誤聯想。
- 是否正確處理 superseded 記憶。
- 額外延遲。

不做統計顯著性檢定，但使用下方固定 contract gate 與 quality gate 判定結果。

### 7. 自動 contract gate

以下屬於 hard gate，任一失敗即整批測試失敗並回傳非零 exit code：

1. `.env`／config／fixture schema validation 通過。
2. PostgreSQL 目標 database、user、`vector` extension 與 Alembic revision 正確。
3. vLLM model id 為 `Qwen3-Embedding-4B`，中文 probe 為 2560 維有限向量。
4. 10 題在 `embedding_only` 與 `hebbian` 各完成一次，共建立 20 筆 `retrieval_runs`。
5. 每個 selected item 都有 final rank、完整 scores 與合法 activation path。
6. `run_mode=evaluation` 前後的 `memory_edges` row count、weights 與 activation count 完全不變。
7. 同一 config 連跑兩次 evaluation，selected external IDs、rank 與 scores 在 `1e-6` tolerance 內一致。
8. Google smoke 成功時回傳非空文字；失敗時保留 provider error 類型，不能偽裝成 retrieval PASS。

### 8. MVP quality gate

品質 gate 與 contract gate 分開報告，第一版成功門檻固定為：

1. 直接召回 2 題：Embedding-only 的所有 `must_include` 均進 final top 5。
2. Hebbian 聯想 3 題：B 的 Association Completeness 不得低於 A，且至少 2 題高於 A。
3. 更新與矛盾 3 題：current scope 的 final rank 1 必須是 `active`；`superseded` 只能作歷史背景。
4. 無答案 2 題：人工 No-answer Safety 每題至少 1 分，且不得生成不存在的購買或偏好事實。
5. 10 題 Traceability 全部可由 `retrieval_items.activation_path` 重建。
6. A／B 都使用相同 `final_top_k = 5`；不得以增加 context 數量製造 Hebbian 優勢。

評估輸出必須同時列出 contract gate PASS/FAIL、每題 A/B 結果、人工品質欄位與尚未評分項目，不得只輸出單一總分。

---

## 拾貳、成功條件

本 MVP 成功，不代表完整長期記憶已完成，而是至少出現以下現象：

1. 直接問題能找到正確角色設定。
2. 「曾想買顯卡，但最後沒買」能同時召回歷史意圖與實際結果。
3. 提到 CMP 170HX 時，能透過 Hebbian 關聯帶出之前的大顯存與顯卡研究脈絡。
4. 問目前狀態時，不會把已 superseded 的 RTX 3090 購買意圖當成現在決定。
5. 無答案題不會因 GPU 話題接近而捏造 A100 或 AMD 偏好。
6. Hebbian 結果能顯示是哪一條 edge 將記憶帶入。
7. PostgreSQL 能完整保存每次測試結果，方便後續調整權重。

---

## 拾參、開發順序

### 固定專案結構

```text
D:\Hebbian-memory\
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── config.yaml
├── migrations/
│   ├── env.py
│   └── versions/
├── data/fixtures/
│   ├── character_memories.jsonl
│   ├── memory_edges.jsonl
│   ├── test_queries.jsonl
│   └── aliases.json
├── src/
│   └── hela_mem_zh_mvp/
│       ├── __init__.py
│       ├── settings.py
│       ├── db.py
│       ├── models.py
│       ├── schemas.py
│       ├── embedding.py
│       ├── provider.py
│       ├── fixtures.py
│       ├── edges.py
│       ├── retriever.py
│       ├── evaluator.py
│       └── cli.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── live/
├── results/
    ├── embedding_only.json
    ├── hebbian.json
    └── summary.json
└── sample/HeLa-Mem/        # 唯讀研究參考，不作 runtime dependency
```

`sample/HeLa-Mem` 不得直接 import。原始 `get_embedding()` 使用本機 SentenceTransformer，`retrieve()` 還包含 keyword LLM、time decay 與 retrieval 後自動 reinforcement，均與本 MVP contract 不同；只能參考概念或公式，不能整段複製成 production path。

`pyproject.toml` 固定使用 `src` layout 與 `uv` 管理，Python 範圍為 `>=3.11,<3.13`，至少包含：

```text
sqlalchemy
psycopg[binary]
alembic
pgvector
pydantic-settings
python-dotenv
pyyaml
openai
google-genai
pytest
```

`uv.lock` 必須提交，確保其他環境能重現相同依賴版本。

### 固定 CLI 與 exit code

```powershell
Copy-Item .env.example .env
uv sync
uv run python -m hela_mem_zh_mvp.cli smoke --component all
uv run alembic upgrade head
uv run python -m hela_mem_zh_mvp.cli seed --fixtures data/fixtures
uv run python -m hela_mem_zh_mvp.cli query --mode hebbian --scope historical "我之前是不是說過想買顯卡？"
uv run python -m hela_mem_zh_mvp.cli evaluate --run-mode evaluation --output results
uv run pytest -q
```

CLI exit code 固定為：

```text
0：成功／所有指定 gate 通過
1：服務、migration、provider、測試或 quality gate 失敗
2：設定、CLI 參數或 fixture schema 錯誤
```

`seed` 預設遇到既有 `external_id` 即失敗，不覆寫資料；只有顯式 `--replace-fixtures` 才能更新 fixture-owned records，且不得刪除非 fixture 資料。

### Phase 0：環境與服務 smoke

- 以同一份環境設定檢查 PostgreSQL、vLLM embedding 與 Google AI Studio provider。
- 確認 vLLM served model id 為 `Qwen3-Embedding-4B`、中文 probe 維度固定為 `2560`；不符即停止。
- Google 執行最小 provider smoke；確認 `REASONING_MODEL=0` 不會觸發額外 reasoning request，且 provider thinking level 為 `minimal`。
- 將連線結果、模型名稱、向量維度、延遲與錯誤類型寫入測試摘要；不得寫入 API key。
- 產出 `.env.example`、`.gitignore`、Settings validation 與 service smoke tests。

### Phase 1：PostgreSQL 基礎

- 建立 PostgreSQL、pgvector。
- 在 `192.168.137.2:5432` 建立獨立 database `hebbian_memory_mvp` 與最小權限的開發帳號。
- 管理者在 database 外完成 role／database provisioning；應用程式 `.env` 只保存 `hebbian_memory_app` 的連線帳密，不保存 PostgreSQL 管理者密碼。
- 驗證 `current_database()` 後才允許執行 migration；禁止把表建立到其他專案資料庫。
- 先執行 `CREATE EXTENSION IF NOT EXISTS vector`，再套用 Alembic migration。
- 建立 memories、memory_edges、retrieval_runs、retrieval_items。
- 以 `VECTOR(2560)` 建立欄位；MVP 使用 exact cosine search，不建立 HNSW／IVFFlat index。
- 加入 Alembic upgrade/downgrade 與 database target guard integration tests。
- 手動匯入約 40 筆中文測試記憶。

### Phase 2：Embedding Baseline

- 接入 `http://localhost:8001/v1` 的 vLLM `Qwen3-Embedding-4B`。
- 以 `/v1/models` 回傳的實際 served model id 發送 embedding 請求，不自行猜測 model id。
- 完成 2560 維驗證、L2 normalization 與 pgvector exact cosine search。
- 執行 10 題 Embedding-only 測試。
- 保存候選、final scores、rank、config snapshot 與結果。
- 完成 embedding client、fixture loader、store 與 baseline integration tests。

### Phase 3：Google AI provider integration smoke

- 以 Google AI Studio 的 `GOOGLE_MODEL=gemini-3.1-flash-lite` 執行固定小 prompt，確認 API key、模型名稱與錯誤處理。
- 需要展示回答時，將 Phase 2／4 選出的記憶與 relation path 傳給 provider，產生單輪純文字回答。
- 回答生成只作整合 smoke，不納入 Embedding-only 與 Embedding + Hebbian 的召回分數；避免 LLM 文字品質掩蓋 retrieval 問題。
- `gemma-4-26b-a4b-it` 只列為備選 provider model，切換時要另記錄 model name、延遲與結果，不與 Gemini 結果混算。
- 驗證 missing key、invalid model、401／403、429／quota、timeout 與空回應皆保留正確 failure 類型。

### Phase 4：Hebbian Spreading

- 依固定方向實作一層 spreading activation 與分數公式。
- 加入 temporal、semantic、co_retrieval edges；contradicts 不提供正向分數。
- 實作相同 `final_top_k = 5` 的 A/B 選取與完整 activation path。
- 再跑同一組 10 題，驗證 evaluation mode 不修改 edge。

### Phase 5：更新與矛盾

- 加入 active、superseded、uncertain。
- 實作 supersedes 與 contradicts。
- 依 fixture `query_scope` 與固定狀態分數調整排序，不使用 LLM 猜 scope。
- 驗證顯卡事件串。
- 完成 current／historical／general scope 的 unit 與 integration tests。

### Phase 6：小型結論

整理：

- 哪些題目 Hebbian 比 Embedding-only 更完整。
- 哪些 edge 帶入錯誤聯想。
- 矛盾判斷是否穩定。
- 查詢延遲增加多少。
- 是否值得進入下一階段。
- contract gate 與 quality gate 是否分別通過；未人工評分項目必須明列。

---

## 拾肆、暫不納入本階段

- 自動從完整對話抽取記憶。
- 每輪聊天即時寫入記憶。
- Dream consolidation。
- 自動遺忘。
- 大型測試集。
- 完整角色回覆流程與人格控制（本階段只保留單輪 provider smoke）。
- 多使用者。
- 圖形介面。
- 完整 HeLa-Mem semantic memory distillation。

這些待本次概念驗證成立後再擴充。

---

## 拾伍、下一階段可能擴充

若第一版證明 Hebbian 能補足跨事件脈絡，再增加：

1. LLM 自動將對話拆成 atomic memories。
2. 自動判定 supersedes、contradicts、supports。
3. 共同召回後逐步強化 edge。
4. 對弱 edge 執行 decay。
5. 將反覆共同召回的 cluster 整理成 consolidated memory。
6. 擴充成 50～100 題正式測試。
7. 加入角色回覆模型，測試召回是否真的改善回答。

---

## 最終收斂

本階段不測完整記憶智慧，而是精準展示一個核心概念：

> 當使用者提到「顯卡」時，系統不只找到文字最像的單筆資料，而能聯想到「曾想購買、後來沒買、目前改研究其他方案」的完整脈絡，並能分辨歷史意圖與目前狀態。

只要這個情境可以穩定、可追蹤地成立，就足以證明 Hebbian associative memory 值得繼續研究。

## 參考資料

1. HeLa-Mem: Hebbian Learning and Associative Memory for LLM Agents, ACL 2026  
   https://aclanthology.org/2026.acl-long.625/

2. HeLa-Mem 官方程式碼  
   https://github.com/ReinerBRO/HeLa-Mem

3. Qwen3 Embedding 官方專案  
   https://github.com/QwenLM/Qwen3-Embedding

4. Qwen3 Embedding 技術報告  
   https://arxiv.org/abs/2506.05176

5. Companion Emergence  
   https://github.com/hanamorix/companion-emergence

6. Gemini 3.1 Flash-Lite 官方模型文件  
   https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite

7. Gemma 4 on Gemini API  
   https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api

8. Google Gen AI Python SDK  
   https://googleapis.github.io/python-genai/

9. pgvector 官方專案  
   https://github.com/pgvector/pgvector
