# TuShare 生产主源数据治理与切换规范

创建时间：2026-07-20  
状态：accepted for shadow implementation  
基线分支：`agent/qualitative-v2-shadow` at `3bee5b4`  
共享合同：`a-stock-lib/docs/specs/2026-07-20-tushare-primary-data-contract.md`

## 1. 目标

在不破坏现有评分、预测历史和生产 cron 的前提下，将 TuShare 提升为以下数据域的生产主源：

- 股票基础信息和行业；
- 盘后日线、指数日线和前复权日线；
- `daily_basic` 估值、市值和股息率；
- 财务指标与利润表、资产负债表、现金流量表；
- 分红方案与实施事件。

本规范采用 Spec → Provider TDD → 隔离影子库 → 有界 shadow → 分数据域 cutover → 独立回滚。生产数据库消费与 cron 切换必须在 shadow 完成后另行确认。

## 2. 非目标和保留数据源

本轮不强行迁移：

| 数据域 | 保留来源/原因 |
| --- | --- |
| 盘中实时行情 | 新浪主源，东财/腾讯交叉验证；TuShare 实时行情需独立付费 |
| 银行 NIM/NPL/拨备覆盖率 | 官方财报或受控研究证据 |
| 公告正文、新闻和定性证据 | 官方公告/WebSearch；TuShare 对应独立权限 |
| 部分宏观和十年国债收益率 | 维持现有来源，另行治理 |

不修改评分权重、不重算历史预测、不新建跨项目共享可写数据库。

## 3. 已验证基线

### 3.1 运行版本

```text
a-stock-tracker venv: a-stock-lib 0.2.0, tushare 1.4.29
a-stock-research:     a-stock-lib 0.3.0, tushare 1.4.29
a-stock-lib source:   0.3.0
```

消费者不做无业务收益的 0.3.0 中间升级；待 0.4.0 构建后分别在隔离环境直接验证。

### 3.2 当前 35 股缓存覆盖

```text
pe_ttm:              0/35
pe_percentile_10y:   0/35
pb:                 15/35
pb_percentile_10y:  15/35
dps:                 0/35
roe_3y_avg:          35/35
debt_ratio:          35/35
```

### 3.3 TuShare 能力实测

- 东方电缆十年 `daily_basic`：2427 行，close/pe/pe_ttm/pb 全部非空；
- 全市场单日 `daily_basic`：5524 行，close 100%，pe_ttm 约 72%，pb 约 99%；
- 东方电缆：`fina_indicator` 37、`income` 41、`balancesheet` 43、`cashflow` 41、`dividend` 13 行；
- PE 对亏损公司为空是合法业务状态。

购买前 2026-07-17 的“五次/日”错误不代表 2026-07-18 购买 2000 积分后的权限。实现仍须主动节流，不依赖高配额无限调用。

## 4. Carrier 和依赖方向

```text
TuShare
  ↓
a-stock-lib 0.4.0 Provider + contract + pure calculators
  ↓
a-stock-tracker ingestion/application cache
```

tracker 不复制 SDK 封装、错误分类或分位算法。`a-stock-lib` 不导入 tracker，不写 `tracker.db`。

## 5. 隔离影子存储

Phase 2/3 只允许写显式 `--db-path` 指向的影子 SQLite。不得通过全局 `TRACKER_DB_PATH` 环境变量静默改变整个应用数据库。

采集 CLI 必须：

- `--db-path` 可选，默认值仅在生产切换后讨论；
- shadow 命令必须显式提供隔离路径；
- 启动日志打印解析后的绝对 DB 路径；
- 拒绝空路径、目录路径和不可写父目录；
- 测试全部使用 `tmp_path`；
- 不调用 `get_db()` 隐式创建生产 schema。

### 5.1 `ingestion_runs`

```text
run_id                 INTEGER PRIMARY KEY
endpoint               TEXT NOT NULL
request_fingerprint    TEXT NOT NULL
requested_at           TEXT NOT NULL
completed_at           TEXT
status                 TEXT NOT NULL
error_code             TEXT
error_message          TEXT              # 已脱敏
row_count              INTEGER NOT NULL
source_as_of            TEXT
payload_sha256          TEXT
raw_artifact_path       TEXT
```

### 5.2 `observation_events`

数据内容去重与“何时观察到”分开保存，避免把 `observed_at` 放进内容主键后破坏重跑幂等性。

```text
event_key              TEXT PRIMARY KEY   # SHA-256(run_id + record_key)
record_key             TEXT NOT NULL
run_id                 INTEGER NOT NULL
observed_at            TEXT NOT NULL
pit_status             TEXT NOT NULL      # backfilled_latest|prospective_observed
UNIQUE(run_id, record_key)
```

同一 canonical payload 在不同采集 run 中只保留一条数据记录，但每次真实观察都有独立 event。严格 PIT 的首次可见时间取该 `record_key` 的最早 `prospective_observed.observed_at`。

### 5.3 `valuation_observations`

```text
record_key             TEXT PRIMARY KEY   # endpoint + natural key + canonical payload SHA-256
code                   TEXT NOT NULL
trade_date             TEXT NOT NULL
close                  REAL
pe                     REAL
pe_ttm                 REAL
pb                     REAL
ps                     REAL
ps_ttm                 REAL
dv_ratio               REAL
dv_ttm                 REAL
total_mv               REAL
circ_mv                REAL
source                 TEXT NOT NULL
source_as_of            TEXT NOT NULL
payload_sha256          TEXT NOT NULL
```

索引至少覆盖 `(code, trade_date)` 和 `(trade_date)`。同一日期 payload 改变时产生新的 `record_key`，不覆盖旧记录；payload 相同的重跑复用数据记录并新增 observation event。

### 5.4 `financial_observations`

```text
record_key             TEXT PRIMARY KEY
code                   TEXT NOT NULL
endpoint               TEXT NOT NULL      # fina_indicator|income|balancesheet|cashflow
ann_date               TEXT
f_ann_date             TEXT
end_date               TEXT NOT NULL
report_type            TEXT
comp_type              TEXT
end_type               TEXT
update_flag            TEXT
source                 TEXT NOT NULL
payload_json           TEXT NOT NULL
payload_sha256          TEXT NOT NULL
```

`record_key` 由 endpoint、代码、报告键和 canonical payload 生成，不包含 `observed_at` 或 `run_id`；不得因 `end_date` 相同覆盖更正记录。观察时间和 PIT 状态由 `observation_events` 保存。

### 5.5 `dividend_observations`

```text
record_key             TEXT PRIMARY KEY
code                   TEXT NOT NULL
ann_date               TEXT
end_date               TEXT
record_date            TEXT
ex_date                TEXT
div_proc               TEXT
source                 TEXT NOT NULL
payload_json           TEXT NOT NULL
payload_sha256          TEXT NOT NULL
```

### 5.5 原始响应归档

原始响应按请求保存为：

```text
artifacts/tushare-ingestion/<run_id>/<endpoint>.jsonl.gz
```

要求：

- 使用标准库 gzip，无新依赖；
- 不包含 Token、请求头或代理凭据；
- 文件写入采用临时文件 + fsync + 原子 replace；
- DB 保存相对路径和 SHA-256；
- artifact 是运行产物，不提交 git。

## 6. Point-in-time 规则

### 6.1 财务记录选择

有效公告日：

```text
COALESCE(NULLIF(f_ann_date, ''), ann_date)
```

该表达式适用于 `income/balancesheet/cashflow`；TuShare 官方 `fina_indicator` 不提供 `f_ann_date`，其有效公告日只能使用 `ann_date`。空字符串必须在写入时规范为 `NULL`。给定 `score_date`，只能选择有效公告日 `<= score_date` 且存在 `prospective_observed.observed_at <= score cutoff` 的记录。选择流程：

1. 从 `observation_events` 限定 score cutoff 当时已经真实观察到的 `record_key`；
2. 再选择有效公告日最新的记录；
3. 同一公告日存在不同 payload 时，选择 cutoff 前最后观察到的版本；
4. 不删除、更写旧数据记录或 observation event。

### 6.2 历史回填限制

`backfilled_latest` 表示今天查询到的历史结果，可能已包含后续更正：

- 不得覆盖既有 predictions；
- 不得声称是历史评分当时可见数据；
- 不得用来重写 Phase 4 历史命中率；
- 可以用于当前研究、当前分位和标明“非严格 PIT”的探索分析。

只有正式采集日起不可变保存的 `prospective_observed` 才能支持未来严格 PIT 回放。

## 7. 字段迁移映射

切换前，scorer 继续读取 `stock_fundamentals.data`。影子表不进入评分。

| 现有字段 | TuShare 原始/派生 | 新 canonical 语义 | cutover 行为 |
| --- | --- | --- | --- |
| `roe_3y_avg` | `fina_indicator.roe_waa` 年度样本 | 最近三个已披露年度均值 | 显式物化并记录报告期/来源 |
| `net_profit_growth` | `fina_indicator.netprofit_yoy` 年度样本 | 最近三个已披露年度均值 | 不与单季同比混用 |
| `debt_ratio` | `fina_indicator.debt_to_assets` | 最新有效报告期 | 银行等行业适用性由质量层判断 |
| `gross_margin` | `fina_indicator.grossprofit_margin` | 最新有效报告期 | 历史稳定性另存派生 metadata |
| `bps` | `fina_indicator.bps` | 最新有效报告期每股净资产 | 不用于合成行情价 |
| `dividend_yield` | `daily_basic.dv_ttm` | 当日 TTM 股息率 | 与 dividend 事件事实分开 |
| `dps` | `dividend` 实施记录 | 明确方案状态后的每股现金分红 | 实现前核对官方字段与税前/税后口径 |
| `pb` | `daily_basic.pb` | 当日 PB | 不与旧百度快照混源 |
| `float_to_total_ratio` | `circ_mv/total_mv*100` | 同日市值口径 | 分母无效时为空 |
| `report_period` | `end_date` | scorer 实际使用的报告期 | 同时记录有效公告日 |
| `pb_hist_monthly` | daily_basic 月末 PB | 最近十年同口径月末序列 | 旧百度序列 deprecated |
| `pb_percentile_10y` | 共享分位计算器 | 仅 `FULL_10Y` 才可物化旧键 | 不足十年不得冒充十年分位 |
| `pe_ttm` | `daily_basic.pe_ttm` | 当日 TTM PE | 亏损为空是合法状态 |
| `pe_percentile_10y` | 共享分位计算器 | FULL_10Y 分位 | 当前 Framework A 暂不计权 |

新增分位 metadata：

```text
valuation_window_start
valuation_window_end
valuation_valid_months
valuation_coverage_status
```

长期 canonical 名为 `pb_percentile`/`pe_ttm_percentile`；旧 `*_10y` 键只作为兼容 materialization，不允许两套来源同时被 scorer 读取。

## 8. 数据质量与合法空值

字段状态至少包括：

```text
OK
MISSING
STALE
NOT_APPLICABLE_LOSS
INSUFFICIENT_HISTORY
SCHEMA_CHANGED
SOURCE_FAILED
```

规则：

- `pe_ttm is null` 且公司亏损：`NOT_APPLICABLE_LOSS`；
- PB/close 等必需字段缺失：不可伪造为 0；
- 前一交易日快照可以降级读取，但必须保留真实 `source_as_of`；
- 超过一个开市交易日仍未更新，估值评分 fail-closed；
- 不允许用旧 AKShare/百度值填入 TuShare observation；fallback 只能在 materialization 层显式发生并记录原因。

## 9. 采集策略

### 日常增量

- 17:15 后按 `trade_date` 调用一次全市场 `daily_basic`；
- 过滤并写入 watchlist，完整响应保存审计 artifact；
- 当天响应日期不匹配时标记 stale，不覆盖 `source_as_of`；
- 不在本规范阶段修改 cron。

### 历史回填

- 估值按 35 个股票代码回填最近十年；
- 财务/分红按 endpoint + 股票代码回填；
- 使用不高于 180 次/分钟的主动节流；
- 每个 request 在 `ingestion_runs` 留 checkpoint；
- 重跑按 deterministic `record_key` 幂等；
- 权限/参数/schema 错误不重试，网络瞬态错误最多一次重试；
- 重试合同测试仅在检测到 `a-stock-lib>=0.4.0` 时执行；tracker 仍锁定 0.2.0 的 Phase 0B 默认环境必须显式 skip，0.4.0 wheel shadow 环境必须实际执行并通过。

## 10. Readiness 与 shadow 门禁

新增 TuShare 主源专用 readiness，不复用行情 `READY_CRON` 作为估值/财务绿灯。建议独立入口：

```text
scripts/check_tushare_primary_readiness.py
```

scope：

```text
valuation
financial
dividend
all
```

### 日度类门禁

- 35/35 有当日 close 或明确停牌状态；
- PB 除来源合法空值外完整；
- PE 空值全部有明确业务原因；
- TuShare daily 与 daily_basic 同日 close 在配置容差内；
- 连续两个交易日通过；
- 东方电缆已知样本可复核；
- 一个亏损 PE 空值和一个停牌/未更新反向案例通过。

### 财务类门禁

- 四个 endpoint schema 通过；
- 最新报告期和公告日可审计；
- fixture 覆盖更正记录、未来公告日排除和重复重跑；
- 至少一个真实历史重复/更正记录做回放；
- 银行专属指标缺失不得伪装成通用财务抓取失败；
- 不要求等待一个完整季度，不建立永久观察器。

### 评分对账

换源允许产生可解释差异，不要求数值完全不变。每个差异必须能归因于：

- 来源变化；
- 报告期变化；
- 指标口径变化；
- 合法空值；
- 分位窗口变化。

任何无来源、无报告期或无法解释的分数变化都阻止该数据域 cutover。

## 11. 测试与 smoke 隔离

默认 pytest 必须清除并禁止继承：

```text
TUSHARE_TOKEN
TG_TOKEN
TG_CHAT_ID
HTTPS_PROXY
HTTP_PROXY
ALL_PROXY
```

规则：

- Provider 单测只用 fake client，不访问网络；
- ingestion 测试必须显式传入 `tmp_path` 下的 `--db-path`；
- 测试不得导入或调用会隐式创建生产 `tracker.db` 的入口；
- Telegram、Google Sheets 和公告调用全部使用 fake；
- 真实 TuShare smoke test 与 pytest 分离，需显式加载 Token，只请求最小数据且不打印凭据；
- smoke 输出必须包含 endpoint、行数、source_as_of 和脱敏状态，不包含请求头、Token 或 `.env` 内容。

## 12. Cutover 顺序

1. 股票基础信息/行业；
2. 估值和市值；
3. 财务指标/三大报表/分红；
4. 复权行情；
5. tracker 评分 materialization；
6. research；
7. a-stock-monitor / a-stock-qa；
8. east-cable-monitor 正式去除 tracker 源码依赖。

旧源先降为显式 fallback；fallback 必须记录字段、原因、来源日期和用户可见影响。没有 shadow PASS 和单独生产确认，不得切换 scorer 或 cron。

## 13. Consumer 治理

- east-cable-monitor 的 `PB×BPS` 行情合成先作为独立安全修复删除；正式 Provider 解耦在后续阶段完成；
- a-stock-monitor 不应长期直接执行 research `cache.db` 的裸 SQL；research 应提供稳定 CLI，再迁移 skill；
- a-stock-qa 后续校验来源、报告期、合法空值和分位覆盖状态；
- consumer 迁移不允许修改持仓记录、阈值或评分权重。

## 14. 生产失败与回滚

| 情况 | 行为 |
| --- | --- |
| 当日 daily_basic 未更新 | 标记 stale，不伪装日期 |
| 上一交易日数据仍可用 | 显式 degraded，保留 source_as_of |
| 超过一个交易日未更新 | 估值评分 fail-closed |
| 亏损 PE 为空 | NOT_APPLICABLE_LOSS |
| 公告日晚于 score date | 禁止使用 |
| 历史回填 | backfilled_latest，不覆盖历史预测 |
| 限流/权限错误 | 不盲目重试 |
| 网络瞬态错误 | 最多一次有界重试 |
| 来源差异无法解释 | 阻止该域 cutover |

生产 cutover 前必须：

- 备份 `tracker.db` 并验证可打开；
- 备份 crontab 原文；
- 保存当前版本和 git SHA；
- 每个数据域有独立 feature flag；
- 回滚只关闭该域新读取路径，不删除影子表；
- 回滚无需改代码或重建历史数据。

## 15. 分阶段实施

### Phase 0A：现存安全修复

删除 east-cable 的 PB×BPS 价格 fallback；无可信价格时 fail-closed。该修复独立分支、独立测试、独立部署决定。

### Phase 0B：合同冻结

本规范与共享 lib 合同经审查后冻结，作为 Phase 1 的实现依据。

### Phase 1：a-stock-lib 0.4.0

TDD 实现 Provider、分位计算器、测试隔离、节流和版本发布，不写 tracker DB。

### Phase 2：隔离影子库

实现 schema、ingestion CLI、artifact、checkpoint 和幂等回填，只写显式 shadow DB。

### Phase 3：有界 shadow

完成日度两交易日 + 财务更正回放 + 评分差异解释，不建立持续评测项目。

### Phase 4：消费方 shadow

tracker → research → monitor/QA → east-cable，旧主源仍可回退。

### Phase 5：生产 cutover

另行确认后，备份、按域切换、修改 cron、验证并保留独立回滚。

## 16. Phase 0B 验收

本阶段不应修改：

- `tracker.db`；
- `cron-setup.sh` 或用户 crontab；
- `data-source-registry.yaml` 当前生产主源声明；
- `config/weights.json`；
- Provider 代码；
- requirements 或已安装包。

验证：

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests/test_data_source_registry.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
