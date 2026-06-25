# Phase 4 a-stock-lib Transition Review

日期：2026-06-25
审查方：agy
范围：tracker 切换到 `a-stock-lib==0.1.1` 的未提交 diff

## 结论

agy 对抗性审查未发现 Blocker / High / Medium 问题，可以继续合并 Phase 4 切换。

## 核查点

- `lib/market_data.py` 仍以 `os.environ["TUSHARE_TOKEN"]` 作为启用门禁；环境变量缺失时直接返回 `RemovedMarketDataProvider`，不会实例化共享包 provider，因此不会被 `a-stock-lib` 内部 `.env` 兜底绕过。
- 删除 `lib/tushare_provider.py` 和 `lib/baostock_provider.py` 后，运行路径已改为 `a_stock_lib.providers.*`，未发现旧导入导致的 `ImportError` 风险。
- `requirements.txt` 使用 `--find-links ../a-stock-lib/dist` 和 `a-stock-lib==0.1.1`，符合 Phase 4 计划，不使用绝对路径或 editable install。
- `tests/test_pipeline.py` 的 OHLC fixture 调整只作用于测试专用 `_AkLikeTestProvider`，用于适配共享包更严格的 OHLC schema，不影响生产 provider 行为。

## 验证

- `pytest tests/ -q`：171 passed
- `ruff check .`：passed
- `mypy`：passed
- `python3 scripts/check_market_data_readiness.py`：READY
