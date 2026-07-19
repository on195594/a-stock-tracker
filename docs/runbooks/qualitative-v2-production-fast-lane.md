# 定性评分 v2 当天生产 Fast Lane

## 目标与边界

生产路径不再等待 M4/M5 的 36 股研究审计。v2 采用独立预计算批处理，`daily` 只读取已通过本地合同复验的结果；任何缺数、过期、损坏、`insufficient_data` 或外部失败均逐股回退现有 v1。

- 开关：`QUALITATIVE_V2_MODE=off|canary|on`，默认 `off`。
- canary：招商银行、中国神华、比亚迪、中国移动、华东医药。
- 固定模型：`gemini-2.5-flash`，每股一个逻辑调用、最多 3 次 HTTP attempt。
- 存储：只写 `qualitative_scores_v2`；不更新旧 `qualitative_scores`，不回写历史 `predictions`。
- artifact：`artifacts/qualitative-v2-production/<run-id>/gemini.jsonl`，run ID create-only。
- `daily` 不采集证据、不新增模型调用；只消费预先入库的 scored 结果。

## 当天启用步骤

先准备与真实 watchlist 身份完全一致、且通过 `validate_context_dict()` 的 context 文件。canary 目录必须恰好包含 5 个 JSON；全量目录必须恰好包含 35 个 JSON。

```bash
python scripts/run_qualitative_v2_production.py preview \
  --contexts artifacts/qualitative-v2-contexts/canary-20260719 \
  --scope canary
```

`preview` 不读取 `.env`、不访问数据库、不创建 artifact。输出应显示 5 个逻辑调用、最多 15 个 HTTP attempts。

显式执行：

```bash
python scripts/run_qualitative_v2_production.py score \
  --contexts artifacts/qualitative-v2-contexts/canary-20260719 \
  --scope canary \
  --run-id qualitative-v2-canary-20260719-01 \
  --execute
```

批处理逐股继续。只有 `VALID_SCORED` 和 `VALID_INSUFFICIENT_DATA` 可进入独立 v2 表；生产读取仅采用前者。若命令返回 `DEGRADED`，已成功的股票仍可 canary，其余自动走 v1。

确认至少一只 canary 为 `VALID_SCORED` 后，在运行 pipeline 的环境中设置：

```text
QUALITATIVE_V2_MODE=canary
```

随后运行常规 `daily`。日志中的 `定性评分使用 source-grounded v2` 表示该股命中 v2；其余股票保持 v1。

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
