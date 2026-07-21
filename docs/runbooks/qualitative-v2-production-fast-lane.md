# 定性评分 v2 当天生产 Fast Lane

## 目标与边界

生产路径不再等待 M4/M5 的 36 股研究审计。v2 采用独立预计算批处理，`daily` 只读取已通过本地合同复验的结果；合法 partial result 按维度采用 v2/v1，任何过期、损坏、全维度 `insufficient_data` 或外部失败均逐股回退现有 v1。

- 开关：`QUALITATIVE_V2_MODE=off|canary|on`，默认 `off`。
- canary：原 5 股集合，加经独立授权纳入的东方电缆、长江电力和三花智控。
- 固定模型：`gemini-2.5-flash`，每股一个逻辑调用、最多 3 次 HTTP attempt。
- 存储：只写 `qualitative_scores_v2`；不更新旧 `qualitative_scores`，不回写历史 `predictions`。
- artifact：`artifacts/qualitative-v2-production/<run-id>/gemini.jsonl`，run ID create-only。
- `daily` 不采集证据、不新增模型调用；只消费预先入库且每次读取重新验证的结果。
- `hybrid_v2`：只采用 `status=scored` 的 v2 维度；`insufficient_data` 维度使用 v1，日志记录逐维来源。

## 当天启用步骤

> 2026-07-19 状态：下述 `qualitative-v2-prod-canary-20260719-01` 已消耗 45/45 attempts，并记录在仓库跟踪的 `qualitative_v2_production_authorizations.json` 退休账本中。该命令仅作为历史执行示例；再次执行会在创建 artifact、打开数据库或发起网络请求前失败。未来采集必须先取得新授权并把新 ID、scope 和上限登记为唯一 active grant。

> 东方电缆状态：首次来源授权 `qualitative-v2-orient-cable-pilot-20260719-01` 在 Gemini 前停止；随后一次性授权 `qualitative-v2-orient-cable-hybrid-20260719-01` 使用封存输入、零新增来源请求完成 1 个 Gemini 逻辑调用/1 attempt，返回 `READY_HYBRID` 并写入 1 行 v2。603606 在 canary 模式采用 moat=7、market_pos=4，sentiment 使用 v1。两项授权均已退休，不能复用。完整记录见 `docs/reviews/2026-07-19-orient-cable-production-pilot.md`。

> 指定 5 股状态：一次性授权 `qualitative-v2-selected-five-20260719-01` 已完成招商银行、长江电力、中国神华、华东医药、三花智控的官方年报采集、结构化评分及生产写入。实际来源用量为 24 次 HTTP attempts、8 次 PDF downloads；Gemini 为 5 个逻辑调用/5 attempts。5 股均为 `READY_HYBRID`，授权已退休。完整记录见 `docs/reviews/2026-07-19-selected-five-production-batch.md`。

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

随后预检与真实 watchlist 身份完全一致的 context。原 canary scope 目录必须恰好包含 5 个 JSON；东方电缆 `orient-cable` scope 必须恰好包含 1 个 JSON；已完成的一次性 `selected-five` scope 必须恰好包含指定的 5 个 JSON；全量目录必须恰好包含 35 个 JSON。

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

生产 canary 读路径可以在 v2 表为空时先启用，以验证真实调度、逐股回退和一键回滚；此时评分变化为零。历史 canary 配置为：

```text
QUALITATIVE_V2_MODE=canary
```

2026-07-19 经用户批准直接上线 v2 后，生产已切换为：

```text
QUALITATIVE_V2_MODE=on
```

`on` 表示 35 股全部进入 v2 选择器，不表示可以绕过证据合同。当前 6 股读取合法 hybrid v2，其余 29 股在合法 v2 行就绪前逐股回退 v1。

随后等待正常交易日的常规 `daily` 调度。日志中的 `定性评分使用 source-grounded v2` 表示该股命中 full v2；`定性评分使用 hybrid_v2，维度来源=...` 表示逐维采用；其余股票保持 v1。非交易日不要为验证此开关而手动创建 prediction。

## 自动化生产验收

全局开关启用后先运行只读验收：

```bash
python scripts/check_qualitative_v2_production.py
```

该命令只解析 `.env` 中的 `QUALITATIVE_V2_MODE`，不会把 Gemini、Telegram 或其他键写入 `os.environ`、stdout 或报告；SQLite 使用 `mode=ro` 与 `query_only` 打开。当前 tracked baseline 绑定：

- 模式必须为 `on`，watchlist/eligibility 必须为 35/35；
- 000963、002050、600036、600900、601088、603606 的逐维 v2 分数必须与获批生产结果一致；
- 其余 29 股必须归类为 v1 fallback，不能把 fallback 计入 v2 coverage；
- 截至 2026-07-17 的 1,859 条 predictions 不可变评分字段必须保持同一 SHA-256；outcome/benchmark 字段不在 seal 中，允许后续自然结案；
- 内存强制 `mode=off` 时，35 股必须全部调用 v1 getter，且不得访问 v2 数据库。

输出为单行 JSON。`decision=PASS` 返回 exit 0；任何模式、watchlist、分数、validator、历史 seal 或回滚路径漂移均输出 `decision=ROLLBACK` 并返回 exit 2。`ROLLBACK` 是自动化决策和告警信号，检查器本身不会修改 `.env`、数据库或 artifact。

下一交易日 `daily` 完成后执行：

```bash
python scripts/check_qualitative_v2_production.py \
  --require-score-date 2026-07-20
```

managed cron 会在工作日 17:45（TuShare primary 17:15、`daily` 17:30 之后，`outcome-update` 18:00 之前）自动执行等价的动态日期检查：

```cron
45 17 * * 1-5 /home/lin/a-stock-tracker/cron-alert-wrap.sh "cd /home/lin/a-stock-tracker && .venv/bin/python scripts/check_qualitative_v2_production.py --require-today" qualitative-v2-production-acceptance --alert-exit-2 >> /home/lin/a-stock-tracker/logs/qualitative-v2-production-acceptance.log 2>&1
```

验收仍原样返回 exit 2；该任务单独启用 `--alert-exit-2`，因此现有 Telegram 包装器会发送带 `ROLLBACK` 标识的告警。其他任务默认仍抑制 exit 2，weekly PM loop 的“摘要已发送、不重复告警”语义不变。cron 不会自行修改 `.env` 或数据库。收到告警后必须先读 JSON `errors` 定位失败门禁；只有 mode、v2 分数、adoption 或真实不可变字段漂移才进入下述 `off` 回滚，不能仅凭告警标题盲目关闭 v2。

2026-07-20 首次自然验收已返回 `PASS`：35 股完整，6 股 hybrid、29 股 fallback，`rollback_verified=true`。2026-07-21 TuShare 三域强切后，验收任务迁移到 17:45。行情 capability report 仍停留在 2026-07-15 并已 stale；报告过期不等于 provider 已确认失效，也不得被引用为当前 PASS。当前 `cron-setup.sh` 会独立安装 TuShare primary cycle，并保留切换前已存在的评分链；若要从零恢复行情依赖任务，仍须先刷新并通过 market-data readiness。

本阶段关于测试入口、跨模型幂等、exit 2 告警语义、readiness 安装副作用和 cron 重叠的长期规则，已归档到 [`lessons-learned.md`](../lessons-learned.md) G-3～G-7。

附加门禁要求该日期恰好存在 watchlist 35 股 Framework A prediction，并在 `logs/daily.log` 中找到当前所有 v2 adoption 的 `hybrid_v2`/`source-grounded v2` 证据。若确认是 v2 生产合同失败，再将 `.env` 的 `QUALITATIVE_V2_MODE` 设置为 `off`；后续新 pipeline 进程会完整使用 v1。不要删除 v2 表或修改历史 predictions。

`config/qualitative/production_acceptance_baseline.json` 是受审查的生产配置，不是运行时自动学习文件。v2 seal 只绑定评分身份与 L3 元数据等真正不可变字段；`outcome_30d/60d/90d`、benchmark、alpha 和可由 `outcome-update` 合法更新的 `estimate_flag` 不属于 immutable identity。扩大 v2 股票、刷新分数或改变 seal 合同时，必须升级 schema 并在同一变更中更新 baseline、测试和项目状态；检查器不会自行接受新状态。

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
