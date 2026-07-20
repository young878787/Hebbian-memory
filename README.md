# HeLa-Mem-inspired Chinese Associative Memory MVP

此專案依 `docs/Hebbian中文角色記憶MVP計畫_v0.4.md` 實作。它是可追蹤的中文角色記憶概念驗證，不是通用長期記憶平台。

## 啟動順序

```powershell
Copy-Item .env.example .env
# 填入 GOOGLE_API_KEY、POSTGRES_PASSWORD 與實際服務設定
uv sync --group dev
uv run python -m hela_mem_zh_mvp.cli smoke --component all
uv run alembic upgrade head
uv run python -m hela_mem_zh_mvp.cli seed --fixtures data/fixtures
uv run python -m hela_mem_zh_mvp.cli query --mode hebbian --scope historical "我之前是不是說過想買顯卡？"
uv run python -m hela_mem_zh_mvp.cli evaluate --run-mode evaluation --output results
uv run pytest
```

`smoke --component postgres|embedding` 是 retrieval 的 hard gate；Google 只影響 provider smoke。所有執行入口共用 `.env` 的 `Settings`，不接受 `DATABASE_URL`。

## 單次串接驗證

`single-e2e-evaluate` 不執行固定 fixture 的完整批次評估。每次執行都會先清除固定測試 namespace `single-e2e-v1` 的記憶、向量、edge 與相關 trace，再以一個輸入批次與一個問題依序驗證：AI extraction、resolver、retrieval、AI answer、AI judge。

```powershell
uv run hela-mem single-e2e-evaluate
# 也可替換測試輸入與問題
uv run hela-mem single-e2e-evaluate --input data/input/conversations.jsonl --query "使用者最後對 RTX 3090 的決定是什麼？"
```

結果寫入 `results/single_e2e/summary.json`。這個指令使用 `evaluation` mode，不會強化 co-retrieval edge；若任一階段契約失敗，會以非零結束碼退出。

## 安全界線

- `.env` 不會提交，也不會被 log 輸出。
- migration 只接受 `hebbian_memory_mvp` 為目標資料庫。
- `evaluate --run-mode evaluation` 只寫 trace，絕不調整 edge。
- `sample/HeLa-Mem` 為唯讀研究參考，runtime 不 import 它。
