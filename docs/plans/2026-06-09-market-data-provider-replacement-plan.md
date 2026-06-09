# Market Data Provider 替代方案研究与迁移计划

创建时间：2026-06-09
状态：implementation checklist + phase-0 guardrail
范围：行情数据入口（`price_at_score`、`daily_bars`、`outcome_price`、`index_prices`）

## 0. 改写后的请求

原请求：

> 请直接删除AKShare/东方财富接，并帮我研究替代方案来实现。

改写为更可执行的提示：

> 停止继续依赖 AKShare/东方财富作为行情数据入口，先把现有 AKShare/东财行情 provider 从生产路径中禁用或移除；然后调研可替代的数据源，形成可落地的迁移方案，说明推荐方案、改造步骤、配置、测试和风险。基本面抓取若仍依赖 AKShare，先作为后续迁移项单独处理，避免本次改动导致 weekly/init 完全不可运行。

改动说明：把“删除接口”限定为行情数据入口，避免误删基本面缓存相关逻辑；把“研究替代方案”落成可实施的数据源、接口映射和分阶段改造计划。

## 1. 当前决策

已执行的 Phase 0 guardrail：

- `lib/market_data.py` 不再导入 `akshare`。
- 默认 `MarketDataProvider` 改为 disabled provider，返回 `SOURCE_DISABLED`。
- `MarketDataCacheService`、`cmd_daily`、`cmd_outcome_update` 不再默认构造 AKShare/东方财富行情 provider。
- `market_data.ak` 仅保留为 test compatibility shim，生产路径不得调用。

未执行：

- 暂不删除 `requirements.txt` 中的 `akshare`，因为 `lib/fetcher.py` / `lib/akshare_provider.py` 的基本面、估值和财报缓存仍依赖 AKShare。
- 暂不改写历史 `predictions`、`daily_bars`、`index_prices` 数值。

## 2. 当前 disabled mode 运维规则

在替代 provider 上线前，默认行情 provider 返回 `SOURCE_DISABLED`。该状态不是生产异常，而是有意阻断继续使用 AKShare/东方财富行情。

执行规则：

1. 暂停或跳过 `daily` cron，直到 Tushare provider 通过 Phase 0.5 probe。
2. 若误运行 `daily`，允许写入 `market_data_audit`，但不得写入新的 `predictions`。
3. disabled mode 下 `outcome-update` 只允许更新已有本地 `index_prices` 和已有缓存可覆盖的 outcome；不得把 `SOURCE_DISABLED` 误解释为停牌或无行情。
4. 报告中若出现 `SOURCE_DISABLED`，解释为“provider 未启用”，不是数据源临时失败。
5. disabled mode 不允许 Telegram 推送新信号。

实施要求：

- 后续 provider PR 必须包含一条测试：默认 disabled provider 下 `cmd_daily()` 不写 `predictions`，并记录 `SOURCE_DISABLED` 审计。
- cron 恢复前必须手动运行：

```bash
python3 pipeline.py accuracy-report
sqlite3 tracker.db "SELECT error_code, COUNT(*) FROM market_data_audit WHERE error_code='SOURCE_DISABLED' GROUP BY error_code;"
```

## 3. 数据需求

| 消费方 | 必需字段 | 刷新频率 | 可接受延迟 | 备注 |
|---|---|---:|---:|---|
| `price_at_score` | date, close | daily | T+0/T+1 | 用于当日评分和 PB 日度分位 |
| L3 `daily_bars` | date, open, high, low, close, volume | daily | T+1 可接受 | MA60/MA120/量能需要至少 120 个交易日 |
| `outcome_price` | date, close | daily | 可向前找 10 自然日 | 30/60/90d outcome |
| `index_prices` | date, close | daily | T+1 可接受 | 沪深 300 benchmark |

统一输出仍使用现有 `MarketDataResult` 和 `daily_bars` schema。

新增硬性口径字段：

| 字段 | 位置 | 要求 |
|---|---|---|
| `source` | `daily_bars.source` / audit | provider 名称和接口名，例如 `tushare.daily` |
| `adjusted` | `daily_bars.adjusted` | 固定枚举：`none` / `qfq` / `hfq`，第一版统一 `none` |
| `volume_unit` | 新增或 audit metadata | 固定枚举，第一版必须在 registry 记录；若无法持久化，禁止跨 provider fallback 计算 L3 |

L3 计算硬规则：

- 120 日窗口必须同 `source`、同 `adjusted`、同 `volume_unit`。
- 新 provider 上线前必须用同一 provider 覆盖当前 watchlist 最近 180 个交易日。
- 不允许把 AKShare 历史 bars 与 Tushare/BaoStock 新 bars 混在同一个 L3 窗口中计算。

## 4. 候选数据源

### 4.1 Tushare Pro（推荐主源）

可覆盖：

- A 股日线：`daily(ts_code, start_date, end_date)`，字段含 `trade_date/open/high/low/close/vol/amount`。
- 指数日线：`index_daily(ts_code='000300.SH', ...)`。
- 交易日历：`trade_cal(...)`。
- 代码格式：`600036.SH`、`000001.SZ`。

优点：

- 标准 API，非网页逆向。
- 股票、指数、交易日历在同一体系内，适合统一 freshness 和交易日逻辑。
- 有 token 和调用门槛，稳定性通常优于免费网页接口。

缺点：

- 需要注册 token。
- 部分接口有积分/权限门槛；需要实测当前账号是否满足日线、指数日线、交易日历调用。
- 需要处理调用频率和限额。

实施结论：作为主源。

### 4.2 BaoStock（推荐免费兜底/预热源）

可覆盖：

- A 股历史 K 线：`query_history_k_data_plus("sh.600000", "date,code,open,high,low,close,volume,...", start_date, end_date, frequency="d", adjustflag="3")`。
- 官方/包说明强调无需注册、免费中国股票市场数据。

优点：

- 免费、无需 token。
- 日线历史数据覆盖 L3 和 outcome 的基本需求。
- 可以作为 daily_bars 预热源或 Tushare 临时不可用时的 fallback。

缺点：

- 包当前仍标 Alpha；服务 SLA 不明确。
- 指数数据和复权/成交量单位需要本项目实测确认。
- 字段为字符串，需要严格 numeric conversion 和空值处理。

实施结论：作为 fallback 或历史预热工具；生产主源仍优先 Tushare。

生产边界：

- BaoStock-only 模式默认只允许 `market-data-backfill` 和 report-only。
- 无 `TUSHARE_TOKEN` 时，BaoStock-only 不允许 `cmd_daily` 写入新 `predictions`。
- 若未来要允许 BaoStock-only 生产写入，必须另行授权，并通过至少 5 个交易日与 Tushare 或人工抽样收盘价对账。

### 4.3 MiniQMT / QMT broker API（长期可选）

可覆盖：

- 本地券商客户端行情 API，例如 `xtdata` 的日线订阅/下载能力。

优点：

- 对个人真实使用环境可能更稳定。
- 可以与券商行情源一致。

缺点：

- 需要本地客户端、账号和运行环境。
- 不适合无头 cron 服务器，部署复杂度高。

实施结论：作为未来增强，不作为当前替代主线。

### 4.4 继续使用 Sina/Tencent/Eastmoney 网页接口（不推荐）

不再作为方案：

- 本轮问题的根因就是网页/逆向源不稳定、DNS/远端断连、schema 和可用性不可控。
- 即使换一个网页源，也只是把 AKShare/东方财富问题迁移到另一个未承诺 SLA 的接口。

## 5. 推荐架构

```text
TushareMarketDataProvider
  fetch_score_price()
  fetch_l3_bars()
  fetch_outcome_price()
  fetch_index_bars()

BaoStockMarketDataProvider
  fetch_l3_bars()
  fetch_outcome_price()
  optional fetch_score_price()

CompositeMarketDataProvider
  primary = Tushare
  fallback = BaoStock
  writes MarketDataResult source/fallback_reason
```

默认策略：

1. 若 `TUSHARE_TOKEN` 存在，使用 `CompositeMarketDataProvider(Tushare, BaoStock optional)`。
2. 若无 token，但安装了 `baostock` 且显式设置 `MARKET_DATA_ALLOW_BAOSTOCK_ONLY=1`，允许 BaoStock-only report/degraded 模式。
3. 否则继续使用 disabled provider，daily 不写新评分，报告明确 `SOURCE_DISABLED`。

内部 canonical symbol 规则：

| 对象 | 项目内部格式 | Tushare | BaoStock |
|---|---|---|---|
| A 股 | `600036` | `600036.SH` | `sh.600036` |
| A 股 | `000001` | `000001.SZ` | `sz.000001` |
| 沪深300 | `000300` | `000300.SH` | `sh.000300` 或实测确认 |

调用层只能传项目内部格式，例如 `fetch_index_bars("000300")`。provider 层负责转换，禁止在 `pipeline.py` 中出现 `sh000300`、`000300.SH`、`sh.000300` 等供应商格式。

## 6. 接口映射

### Tushare

| 项目字段 | Tushare 字段 | 转换 |
|---|---|---|
| date | trade_date | `YYYYMMDD` -> `YYYY-MM-DD` |
| open | open | float |
| high | high | float |
| low | low | float |
| close | close | float |
| volume | vol | 注意单位，需记录 registry |
| index close | index_daily.close | float |

代码转换：

```text
6xxxxx -> 6xxxxx.SH
0xxxxx / 3xxxxx -> xxxxxx.SZ
000300 benchmark -> 000300.SH
```

### BaoStock

| 项目字段 | BaoStock 字段 | 转换 |
|---|---|---|
| date | date | already `YYYY-MM-DD` |
| open/high/low/close | same | string -> float |
| volume | volume | string -> float |
| code | sh.600036 / sz.000001 | code prefix mapping |

## 7. 实施计划

### Phase 0.5：Provider capability probe（硬前置）

目标：先验证账号权限、字段、延迟和限频，再写生产 provider。

输入：

- `.env` 中配置 `TUSHARE_TOKEN`。
- 选取样本：`600036`、`000001`、`002594`、`000300`。

验证项：

1. `daily` 能拉取 1 年 A 股日线，字段含 `trade_date/open/high/low/close/vol`。
2. `index_daily` 能拉取沪深 300。
3. `trade_cal` 能拉取最近 1 年交易日历。
4. 单次请求延迟、错误码、限频表现有记录。
5. 抽样 close 与现有 `daily_bars` 或人工行情页面差异 ≤ 0.5%。

输出：

- 新增 `docs/reviews/YYYY-MM-DD-tushare-capability-probe.md`。
- 若任一必需接口权限不足，停止 Phase 1，不写 provider。

验收命令草案：

```bash
python3 scripts/probe_tushare_market_data.py
```

### Phase 1：Tushare provider

文件：

- 新增 `lib/tushare_provider.py`
- 修改 `lib/market_data.py`
- 修改 `requirements.txt`：新增 `tushare`
- 修改 `.env` 文档：新增 `TUSHARE_TOKEN`

测试：

- token 缺失 -> `AUTH_MISSING`，不得 fallback 到网页源
- `daily` 正常返回 -> 标准化 date/close/volume
- `index_daily` 正常返回 -> 写入 `index_prices`
- 空响应、缺列、限流、权限不足 -> 结构化 error_code
- canonical code conversion 覆盖 `6xxxxx`、`0xxxxx`、`3xxxxx`、`000300`
- provider 输出 `source=tushare.daily` / `source=tushare.index_daily`

验收：

```bash
pytest tests/test_market_data.py tests/test_pipeline.py -q
python3 pipeline.py accuracy-report
```

Stop 条件：

- `daily`、`index_daily` 或 `trade_cal` 任一权限不足。
- 无法稳定获得 `vol`。
- `fetch_score_price` 对 35 只 watchlist 覆盖率 < 95%。

### Phase 2：BaoStock fallback

文件：

- 新增 `lib/baostock_provider.py`
- 修改 `requirements.txt`：新增 `baostock`
- 新增 provider 组合测试

测试：

- login/logout 成功路径
- query 返回字符串字段 -> numeric conversion
- empty/error_code 非 0 -> 结构化 failure
- Tushare failed + BaoStock ok -> `status=degraded` 且 `fallback_source` 有值
- BaoStock-only 未授权时，`cmd_daily` 不写 `predictions`
- BaoStock 与 Tushare 同日 close 抽样偏差 > 0.5% 时，fallback 不可用于生产写入

### Phase 3：历史 daily_bars 预热

新增命令：

```bash
python3 pipeline.py market-data-backfill --start 2025-01-01 --end 2026-06-09
```

目标：

- 先补齐当前 watchlist 过去 180 个交易日。
- L3 覆盖率目标 ≥ 95%。
- 不重算历史 `total_score`，只重算 L3 metadata。
- 同一股票最近 120 交易日必须同 source + 同 adjusted + 同 volume_unit。
- 新 provider 覆盖旧 source 时，必须记录迁移批次和覆盖策略。

验收 SQL：

```sql
SELECT code, COUNT(*) AS bars, MIN(trade_date), MAX(trade_date), COUNT(DISTINCT source) AS sources
FROM daily_bars
WHERE trade_date >= date('now', '-260 days')
GROUP BY code
ORDER BY bars;
```

成功标准：

- 每只 watchlist 最近 180 交易日 bars 足够。
- `COUNT(DISTINCT source)=1` 或 L3 计算明确拒绝混源窗口。
- `pipeline.py accuracy-report` 中 L3 覆盖率 ≥ 95%。

### Phase 4：基本面 AKShare 迁移（单独计划）

后续再处理 `lib/fetcher.py`：

- 财报摘要
- 毛利率/ROE/负债率
- PB 历史分位
- 股息率

该阶段不与行情迁移混在同一个 PR。

## 8. Stop 条件

以下任一情况发生，应停止生产写入，只保留 report-only：

- Tushare token 不存在或权限不足。
- 连续 2 个交易日 `price_at_score` 覆盖率 < 95%。
- `daily_bars` L3 覆盖率 < 90%。
- 股票日线和指数日线来源日期不一致且无法解释。
- BaoStock fallback 与 Tushare 主源同日 close 偏差超过 0.5% 且样本超过 3 只。
- 任一 L3 窗口出现 mixed source / mixed adjustment / unknown volume_unit。
- `cmd_daily` 在 provider disabled 或 AUTH_MISSING 时写入了新 `predictions`。

## 9. PR 拆分建议

1. PR-1：Phase 0.5 probe script + probe report，不改生产 provider。
2. PR-2：Tushare provider + tests + disabled/auth behavior。
3. PR-3：market-data-backfill 命令 + daily_bars 同源覆盖。
4. PR-4：BaoStock fallback，只允许 backfill/report-only。
5. PR-5：cron 恢复和生产运行手册。

每个 PR 都必须通过：

```bash
pytest tests/ -q
ruff check .
mypy
git diff --check
```

## 10. 资料链接

- Tushare Pro 官方数据接口说明：`https://tushare.pro/document/1?doc_id=230`
- Tushare Pro 接口权限/积分说明：`https://tushare.pro/document/1?doc_id=108`
- BaoStock 主页：`https://baostock.com/`
- BaoStock PyPI 示例：`https://pypi.org/project/baostock/`
- MiniQMT XtData 行情模块参考：`https://www.miniqmt.com/api/miniQMT/miniQMT%E5%AE%98%E6%96%B9API%E6%96%87%E6%A1%A3XtQuant.XtData%E8%A1%8C%E6%83%85%E6%A8%A1%E5%9D%97.html`
