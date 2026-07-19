# 定性评分 v2 当天生产 Fast Lane

## 目标与边界

生产路径不再等待 M4/M5 的 36 股研究审计。v2 采用独立预计算批处理，`daily` 只读取已通过本地合同复验的结果；合法 partial result 按维度采用 v2/v1，任何过期、损坏、全维度 `insufficient_data` 或外部失败均逐股回退现有 v1。

- 开关：`QUALITATIVE_V2_MODE=off|canary|on`，默认 `off`。
- canary：招商银行、中国神华、比亚迪、中国移动、华东医药；另含经独立授权加入资格的东方电缆单股 pilot。
- 固定模型：`gemini-2.5-flash`，每股一个逻辑调用、最多 3 次 HTTP attempt。
- 存储：只写 `qualitative_scores_v2`；不更新旧 `qualitative_scores`，不回写历史 `predictions`。
- artifact：`artifacts/qualitative-v2-production/<run-id>/gemini.jsonl`，run ID create-only。
- `daily` 不采集证据、不新增模型调用；只消费预先入库且每次读取重新验证的结果。
- `hybrid_v2`：只采用 `status=scored` 的 v2 维度；`insufficient_data` 维度使用 v1，日志记录逐维来源。

## 当天启用步骤

> 2026-07-19 状态：下述 `qualitative-v2-prod-canary-20260719-01` 已消耗 45/45 attempts，并记录在仓库跟踪的 `qualitative_v2_production_authorizations.json` 退休账本中。该命令仅作为历史执行示例；再次执行会在创建 artifact、打开数据库或发起网络请求前失败。未来采集必须先取得新授权并把新 ID、scope 和上限登记为唯一 active grant。

> 东方电缆状态：`qualitative-v2-orient-cable-pilot-20260719-01` 已消耗 11/12 HTTP attempts 和 2/3 PDF downloads 后退休。官方 PDF 传输与提取通过，但在 2026-06-19 至 2026-07-19 窗口内没有得到持续性 sentiment，且提交前复核发现初版壁垒/行业地位截取规则过宽；因此 Gemini 为 0 调用、v2 表为 0 写入，603606 在 canary 模式继续逐股回退 v1。完整记录见 `docs/reviews/2026-07-19-orient-cable-production-pilot.md`。

先使用已批准的 CNINFO 边界构建真实 canary context。该命令只读 `stock_fundamentals`，不读取模型凭证、不写数据库：

```bash
python scripts/collect_qualitative_v2_production_contexts.py \
  --authorization-id qualitative-v2-prod-canary-20260719-01 \
  --scope canary \
  --as-of-date 2026-07-19 \
  --run-id qualitative-v2-contexts-canary-20260719-01 \
  --execute-cninfo
```

采集器对每家公司执行 `核心技术`、`市场占有率`、`合同期限` 三组全文检索，保存原始响应 SHA、locator、manifest 和通过 `validate_context_dict()` 的 context。技术成功但没有匹配片段时，context 只保留实际存在的基本面 supporting evidence；不得补造直接证据，也不得因此调用 Gemini。

随后预检与真实 watchlist 身份完全一致的 context。原 canary scope 目录必须恰好包含 5 个 JSON；东方电缆 `orient-cable` scope 必须恰好包含 1 个 JSON；全量目录必须恰好包含 35 个 JSON。

```bash
python scripts/run_qualitative_v2_production.py preview \
  --contexts artifacts/qualitative-v2-contexts/canary-20260719 \
  --scope canary
```

`preview` 不读取 `.env`、不访问数据库、不创建 artifact。输出应显示 5 个逻辑调用、最多 15 个 HTTP attempts，并分别给出 `score_ready`（全三维）与 `hybrid_ready`（至少一维可评分）。

显式执行：

```bash
python scripts/run_qualitative_v2_production.py score \
  --contexts artifacts/qualitative-v2-contexts/canary-20260719 \
  --scope canary \
  --run-id qualitative-v2-canary-20260719-01 \
  --execute
```

批处理逐股继续。只有 `VALID_SCORED` 和 `VALID_INSUFFICIENT_DATA` 可进入独立 v2 表：

- 全三维 scored：返回 `READY`，生产使用 full v2。
- 至少一维 scored、其余合法 insufficient：返回 `READY_HYBRID`，生产逐维合成。
- 全维 insufficient、invalid 或调用失败：返回 `DEGRADED`，该股使用 v1。

`qualitative_scores_v2` 中 partial 行保留已评分整数，缺失维度必须为 `NULL`；读取时 context/result/hash 和冗余列任一漂移都会整股回退 v1。

生产 canary 读路径可以在 v2 表为空时先启用，以验证真实调度、逐股回退和一键回滚；此时评分变化为零。在运行 pipeline 的环境中设置：

```text
QUALITATIVE_V2_MODE=canary
```

随后等待正常交易日的常规 `daily` 调度。日志中的 `定性评分使用 source-grounded v2` 表示该股命中 full v2；`定性评分使用 hybrid_v2，维度来源=...` 表示逐维采用；其余股票保持 v1。非交易日不要为验证此开关而手动创建 prediction。

## 扩展与回滚

同日扩展 35 股使用新的 context 目录和新的 run ID：

```bash
python scripts/run_qualitative_v2_production.py preview \
  --contexts artifacts/qualitative-v2-contexts/all-20260719 \
  --scope all

python scripts/run_qualitative_v2_production.py score \
  --contexts artifacts/qualitative-v2-contexts/all-20260719 \
  --scope all \
  --run-id qualitative-v2-all-20260719-01 \
  --execute
```

全量上限为 35 个逻辑调用、105 个 HTTP attempts。只有明确设置 `QUALITATIVE_V2_MODE=on` 才会让非 canary 股票读取 v2。

回滚只需设置 `QUALITATIVE_V2_MODE=off` 并重新运行 pipeline；无需删除任何 v2 数据，也不改变历史 prediction。开关拼写错误同样 fail closed 到 v1。

## 发布阻塞项

仅保留两类同步阻塞项：

1. context 或模型输出未通过现有 v2 合同，或身份/hash/固定模型发生漂移；
2. 无法保证逐股 v1 回退、独立表和 `off` 回滚。

Claude blind reference、36 股分层覆盖、第二/第三来源、完整 support audit 和 M4 coverage 继续作为发布后研究审计，不阻断 canary。本路径不授权投资有效性声明、自动交易或历史数据改写。
