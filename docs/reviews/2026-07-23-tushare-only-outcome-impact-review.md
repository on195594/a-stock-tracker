# TuShare-only 切换：历史 outcome / benchmark 影响只读评估

日期：2026-07-23
状态：final, read-only
数据库：`tracker.db`
历史审计来源：`backups/tushare-only-cutover-20260723-215627/tracker.before.db`

## 结论

1. 活动行情和后续 outcome 更新已经是 TuShare-only；本评估没有改写任何 prediction、outcome、benchmark 或 alpha。
2. 历史审计中有 25 条涉及 BaoStock：14 次成功的 outcome fallback、10 次失败的 outcome fallback、1 次成功的 benchmark fallback。
3. 14 次成功 outcome fallback 可以证明至少 14 次历史 outcome 写入使用了 BaoStock，但现有 audit 表没有保存 `prediction_id`、`window`、`target_date` 或实际成交日，无法无歧义定位到具体 prediction 行。
4. 以当前本地 `tushare.daily` 未复权日线按现行“目标日前 10 个自然日内最近交易日”规则只读重算，1,767 个可比历史 outcome 中有 450 个不一致。该范围显著大于 14 次 BaoStock 成功 fallback，说明还混有历史价格来源、精度、快照时间或复权口径差异，不能把 450 个差异全部归因于 BaoStock。
5. 沪深 300 本地 `index_prices` 与实时 TuShare `index_daily` 的 64 个共同日期中，30 个存在差异，但最大绝对差仅 0.0046 指数点，表现为两位小数与四位小数的精度差；当前证据不支持把 benchmark rounding 当作重大投资结论偏差。
6. 不应直接覆盖既有 outcome。安全路径是先生成逐 prediction 的 TuShare shadow outcome，补齐 provenance，再审查差异分布和策略报告影响，最后另行批准是否切换标签版本。

## BaoStock 历史审计

| purpose | status | rows | 含义 |
|---|---:|---:|---|
| `outcome_price` | `degraded` | 14 | TuShare 因限频失败，BaoStock 返回价格，随后可写 outcome |
| `outcome_price` | `failed` | 10 | TuShare 与 BaoStock 均失败，不会写 outcome |
| `benchmark_price` | `degraded` | 1 | TuShare index 请求失败，BaoStock 返回指数历史 |

成功 outcome fallback 涉及的代码包括：`002050`、`002648`、`601899`、`600362`、`601668`、`002594`、`601088`、`601933`。由于一个代码可对应多个 score date 和 30/60/90 日窗口，不能只凭代码和 run date反推唯一 prediction。

## TuShare 本地 shadow 重算

算法：

- 对每个非空历史 `outcome_{30d,60d,90d}`，从 `score_date + window` 向前最多 10 个自然日；
- 只读取 `daily_bars.adjusted='none' AND source='tushare.daily'`；
- 使用最近交易日 close 和既有 `price_at_score` 计算收益率；
- 比较精度为绝对差 `1e-8` 个百分点；
- 全程 SQLite `mode=ro`。

| 窗口 | 已存标签 | 可比 | 精确一致 | 不一致 | 本地 TuShare 缺失 | 最大绝对差（百分点） |
|---|---:|---:|---:|---:|---:|---:|
| 30d | 1,271 | 1,268 | 855 | 413 | 3 | 28.671329 |
| 60d | 501 | 498 | 461 | 37 | 3 | 16.039981 |
| 90d | 4 | 1 | 1 | 0 | 3 | 0 |
| 合计 | 1,776 | 1,767 | 1,317 | 450 | 9 | — |

这些差异不是 BaoStock 专属影响量。特别是大量早期 30d 标签不一致，说明历史 `price_at_score` / outcome 数据来源和精度合同本身需要版本化治理。

## Benchmark 只读比对

通过一次 TuShare `index_daily` 只读请求比对 `000300`：

- TuShare API：5,955 行；
- 本地 `index_prices`：64 行；
- 共同日期：64；
- close 不完全一致：30；
- 最大绝对 close 差：0.0046 指数点；
- 差异形态：本地多为两位小数，TuShare 为四位小数。

该差异会对 benchmark return 产生极小的尾数影响，但仍应在未来 provenance 中明确 source 与精度。

## 投资与报告风险

- **P1：历史标签可审计性不足。** 当前 audit 不能把来源精确绑定到 prediction/window/target trade date。
- **P1：历史策略评估口径混合。** 450/1,767 可比 outcome 与当前 TuShare 重算不一致，直接使用旧标签做因子有效性判断可能混入数据源和价格口径噪声。
- **P2：benchmark 精度差。** 现有差异很小，但 source/precision 没有随标签持久化。
- **非结论：** 本评估不能证明 450 个差异都是错误，也不能证明用 TuShare shadow 覆盖后策略表现一定更好。

## 后续安全合同

若要治理历史 outcome，必须另开 spec，并至少包含：

1. 新建 shadow 表，不覆盖 `predictions`；
2. 主键绑定 `prediction_id + window`；
3. 保存 `source`、`target_date`、`actual_trade_date`、`price_at_score_source`、`outcome_price`、`adjusted`、`fetched_at` 和算法版本；
4. 使用 TuShare-only 分批回算，受限频保护；
5. 输出旧/新 outcome、benchmark、alpha、hit-rate 和分层报告差异；
6. 独立审查通过并由用户明确批准后，才讨论版本切换；禁止静默覆盖历史字段。
