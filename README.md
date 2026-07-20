# HeLa-Mem-inspired Chinese Associative Memory MVP

此專案依 `docs/Hebbian中文角色記憶MVP計畫_v0.4.md` 實作。它是可追蹤的中文角色記憶概念驗證，不是通用長期記憶平台。

## 啟動順序

```powershell
Copy-Item .env.example .env
# 填入 GOOGLE_API_KEY、POSTGRES_PASSWORD 與實際服務設定
uv sync --group dev
uv run python -m hela_mem_zh_mvp.cli smoke
uv run alembic upgrade head
$env:HEBBIAN_NAMESPACE = "my-research-namespace"
uv run python -m hela_mem_zh_mvp.cli ingest
uv run python -m hela_mem_zh_mvp.cli ask "我之前是不是說過想買顯卡？"
uv run python -m hela_mem_zh_mvp.cli evaluate
uv run pytest
```

`smoke` 會驗證 PostgreSQL、embedding 與 provider；所有執行入口共用 `.env` 的 `Settings`，不接受 `DATABASE_URL`。`ask --learn` 只有通過 citation contract 時才強化 co-retrieval edge。

## 完整執行入口

根目錄 `main.py` 是通用入口。無參數時會執行完整流程：AI extraction、resolver、60 題 retrieval、AI answer、AI judge。fixture 目前包含 60 筆記憶（其中 20 筆為 `lifestyle`）與 60 題測試；題目包含 32 題 baseline 與 28 題 `architecture_v1`，並記錄複雜度、目標架構能力與必要 hop 數。每次執行使用獨立暫用 namespace，結束時會刪除其所有資料與 namespace row；舊的 `single-e2e-v1` namespace 也會一併移除，不影響其他 namespace。

```powershell
uv run python main.py
# 可透過參數執行既有 CLI，或限制題數／改跑單題
uv run python main.py run --query-limit 10
uv run python main.py run --input data/input/conversations.jsonl --query "使用者最後對 RTX 3090 的決定是什麼？"
```

彙總結果寫入 `results/summary.json`；逐題資料寫入 `results/pipeline/` 的 `retrieval.json`、`answers.json`、`judge_input.json` 與 `summary.json`。`status` 表示整條執行與契約是否跑通，`quality_status` 獨立表示 AI judge 的答案品質；兩者不混為同一個 gate。這個流程使用 `evaluation` mode，不會強化 co-retrieval edge；若任一執行階段、契約或暫用 namespace 清理失敗，會以非零結束碼退出。

## 安全界線

- `.env` 不會提交，也不會被 log 輸出。
- migration 只接受 `hebbian_memory_mvp` 為目標資料庫。
- `evaluate` 只寫 retrieval trace，預設不載入 `memory_edges.jsonl`，也不調整 edge；該檔案僅供 opt-in 研究／投影測試使用。
- `sample/HeLa-Mem` 為唯讀研究參考，runtime 不 import 它。
