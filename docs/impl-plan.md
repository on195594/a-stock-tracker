# a-stock-tracker 实施计划

Generated: 2026-04-15
Last updated: 2026-05-12（Phase 3.6 完成）
Design ref: `docs/design.md`
Status: **历史实施计划，已归档（当前执行基线见 `docs/evolution-roadmap.md`）**

> 归档说明：本文档记录 Phase 1 至 Phase 3.6 的早期建设步骤，文中的接口、
> 数据源、命令和测试数量均为历史信息，不再作为当前实现或计划来源。Framework A 已于
> 2026-09-21 以 `CLOSED_UNPROVEN` 结案；当前状态以 `README.md` 和
> `docs/project-status.md` 为准，不得按本文恢复 Framework A 或旧目录。

Phase 1（Step 0-7）：✅ 完成
Phase 3（Step 8-14）：✅ 完成
Phase 3.5（Step 15-16）：✅ 完成
Phase 3.6（Step 17-18）：✅ 完成

---

## 概述

将 `~/.claude/skills/a-stock-research/` 改造为独立的自动化管道项目 `a-stock-tracker/`，
实现每日自动评分 → 记录预测 → 追踪 30/60/90 天收益 → 输出准确率报告的完整闭环。
Phase 3 新增：Gemini 定性评分 + Telegram 推送 + 双路 fallback 数据源。

目标路径：`~/a-stock-tracker/`

---

## Step 0：人工验证 AKShare 接口字段

**性质：** 门控任务（不写代码，只做验证）。后续步骤依赖此步输出。

**推荐模型：** 无需模型（纯终端操作）

### 入口提示词

```
请帮我在 Python REPL 中验证以下三个 AKShare 接口的字段名，
激活 venv 后直接运行，输出字段列表和前两行数据：

1. 财报摘要接口（验证 gross_margin / report_period 字段是否可用）：
   import akshare as ak
   df = ak.stock_financial_abstract_ths(symbol="603288", indicator="按报告期")
   print("columns:", df.columns.tolist())
   print(df.head(2))

2. 沪深300日线接口（验证 benchmark 数据字段名）：
   df300 = ak.index_zh_a_hist(symbol="000300", period="daily",
                               start_date="20240101", end_date="20240110")
   print("columns:", df300.columns.tolist())
   print(df300.head(2))

3. SQLite 版本（需 ≥ 3.31.0 支持 Generated Column）：
   python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

### 结束验证

```
记录以下信息（填入后续步骤的入口提示词）：

□ gross_margin 字段名（或"AKShare 无此字段，Phase 1 改为 phase1_fixed: 5"）
□ report_period 对应的字段名（预期 "报告期"，以实测为准）
□ 沪深300日线收盘价字段名（预期 "收盘"）
□ SQLite 版本号（如 3.39.2）→ ≥ 3.31.0 则继续，否则记录升级计划
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Bash` | 运行 Python 验证脚本 |

### gstack skills

| Skill | 时机 |
|-------|------|
| 无 | 此步骤无需 gstack skills |

**注意：** 此步骤纯手动验证，不产生代码文件。把验证结果记在纸上或 notes.md 里，后续步骤入口提示词中直接填入字段名。

---

## Step 1：项目脚手架 + lib/ 初始化

**推荐模型：** Haiku 4.5（机械性任务，不需要推理）

### 入口提示词

```
请帮我在 ~/ 下创建新项目 a-stock-tracker/，
完整创建以下文件结构：

a-stock-tracker/
├── config.py
├── requirements.txt
├── .gitignore
├── README.md
└── lib/
    └── __init__.py（空文件）

config.py 内容：
- WATCHLIST: list[dict]，包含 10 只 A 股示例（招商银行600036、五粮液000858...）
- DB_PATH = os.path.expanduser("~/a-stock-tracker/tracker.db")
- LOG_DIR = os.path.expanduser("~/a-stock-tracker/logs/")

requirements.txt 只需要：
- akshare
- pytest

.gitignore 包含：.env, *.db, logs/, __pycache__/, .venv/

然后将以下两个文件复制到 lib/：
cp ~/.claude/skills/a-stock-research/fetcher.py lib/fetcher.py
cp ~/.claude/skills/a-stock-research/cache.py   lib/cache.py

最后修改 lib/cache.py：
将文件顶部的 DB_PATH 变量改为从 config.py 读取，
或直接硬编码为 ~/a-stock-tracker/tracker.db。
```

### 结束验证

```
□ ls -la a-stock-tracker/ 显示所有文件存在
□ python3 -c "import config; print(config.WATCHLIST)" 无报错
□ python3 -c "from lib import cache" 无报错
□ lib/cache.py 的 DB_PATH 不再指向 ~/.claude/skills/a-stock-research/cache.db
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Bash` | 创建目录、复制文件 |
| `Write` | 创建 config.py、requirements.txt、.gitignore |
| `Edit` | 修改 lib/cache.py 的 DB_PATH |
| `Read` | 读取 lib/cache.py 确认改动范围 |
| `Glob` | 验证文件结构 |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/checkpoint` | 步骤完成后 | 保存进度：`/checkpoint "Step 1完成：项目脚手架就绪，lib/复制完成"` |

---

## Step 2：lib/cache.py — 新增 predictions + index_prices 表

**推荐模型：** Sonnet 4.6（需要理解 Generated Column 语法、UNIQUE 约束，有一定推理要求）

### 入口提示词

```
请修改 ~/a-stock-tracker/lib/cache.py，
在现有的 get_db() 函数中新增两张表的建表 DDL：

**表1：predictions**
（注意：alpha_*d 是 REAL GENERATED ALWAYS AS (outcome_Xd - benchmark_Xd) VIRTUAL）
（需要 SQLite ≥ 3.31.0，在模块顶部加 assert sqlite3.sqlite_version_info >= (3, 31, 0)）

完整 DDL：
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL,
    name            TEXT,
    framework       TEXT    NOT NULL,
    score_date      TEXT    NOT NULL,
    price_at_score  REAL,
    quant_score     REAL,
    total_score     REAL,
    weights_hash    TEXT,
    report_period   TEXT,
    outcome_30d     REAL,
    outcome_60d     REAL,
    outcome_90d     REAL,
    benchmark_30d   REAL,
    benchmark_60d   REAL,
    benchmark_90d   REAL,
    alpha_30d       REAL GENERATED ALWAYS AS (outcome_30d - benchmark_30d) VIRTUAL,
    alpha_60d       REAL GENERATED ALWAYS AS (outcome_60d - benchmark_60d) VIRTUAL,
    alpha_90d       REAL GENERATED ALWAYS AS (outcome_90d - benchmark_90d) VIRTUAL,
    estimate_flag   INTEGER DEFAULT 0,
    threshold_adjusted INTEGER DEFAULT 0,
    created_at      TEXT,
    UNIQUE(code, framework, score_date)
);

**表2：index_prices**
CREATE TABLE IF NOT EXISTS index_prices (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    close   REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);

把两个 CREATE TABLE 语句加到现有 get_db() 的建表块中，
不要删除原有的表（保持 stock_fundamentals 等现有表）。
```

### 结束验证

```
□ python3 -c "from lib.cache import get_db; db=get_db(); print([r[0] for r in db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"
  → 输出应包含 predictions 和 index_prices
□ python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
  → 版本 ≥ 3.31.0（Generated Column 支持）
□ 插入一条测试数据后查询 alpha_30d 自动计算正确
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Read` | 读取现有 lib/cache.py 确认 get_db() 位置和现有建表语句 |
| `Edit` | 在 get_db() 中追加两张表的 DDL |
| `Bash` | 运行验证命令确认表结构 |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/review` | 修改完成后 | `git diff lib/cache.py` 然后 `/review`，重点检查 VIRTUAL 列语法和 UNIQUE 约束 |
| `/checkpoint` | 验证通过后 | `/checkpoint "Step 2完成：predictions + index_prices 表已建"` |

---

## Step 3：lib/fetcher.py — 新增 report_period 字段存储

**推荐模型：** Haiku 4.5（改动极小，找准插入点即可）

### 入口提示词

```
请修改 ~/a-stock-tracker/lib/fetcher.py。

在 cmd_fetch() 函数的 Step 2（处理 fin_df 的位置，约原文件第 277 行附近）添加：

# 记录最新财报所属期
if fin_df is not None and not isinstance(fin_df, (str, tuple)):
    report_period_raw = fin_df.sort_values("报告期", ascending=False).iloc[0]["报告期"]
    results['report_period'] = str(report_period_raw)[:10]  # YYYY-MM-DD 格式

注意：
- 字段名 "报告期" 以 Step 0 验证结果为准（如实测字段名为 "报告期"，直接用）
- 如果 Step 0 验证发现字段名不同，请替换为正确字段名
- 只新增这 4 行，不修改其他任何逻辑
```

### 结束验证

```
□ grep -n "report_period" lib/fetcher.py → 找到新增的 4 行
□ python3 -c "from lib import fetcher" 无报错（语法正确）
□ 确认 cmd_batch() 未被修改（fetcher.py 其余部分保持原样）
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Read` | 读取 lib/fetcher.py 确认 cmd_fetch() 的 fin_df 处理位置（约第 275-290 行） |
| `Edit` | 在 fin_df 处理后插入 4 行代码 |
| `Grep` | `grep -n "fin_df" lib/fetcher.py` 定位插入点 |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/checkpoint` | 完成后 | `/checkpoint "Step 3完成：report_period 字段存储已添加"` |

---

## Step 4：scorer.py + tests/test_scorer.py

**推荐模型：** Sonnet 4.6（需要实现线性插值算法 + 处理 invert 逻辑 + 11 个测试用例）

### 入口提示词

```
请在 ~/a-stock-tracker/ 下创建 scorer.py 和 tests/test_scorer.py。

**scorer.py 要求：**

1. 自定义异常（可放在 scorer.py 顶部或单独 exceptions.py）：
   class UnsupportedFrameworkError(Exception): pass
   class InsufficientDataError(Exception): pass

2. 核心函数：
   def score_stock(code: str, framework: str, data: dict[str, float | None]) -> dict

3. 评分逻辑从 weights.json 读取（weights.json 需同步创建），fields:
   - roe_3y_avg（非固定，breakpoints 线性插值）
   - net_profit_growth（非固定，breakpoints 线性插值）
   - debt_ratio（非固定，breakpoints 线性插值，invert=true）
   - gross_margin（非固定，breakpoints 线性插值，若 AKShare 无此字段则 phase1_fixed: 5）
   - pe_percentile_10y（非固定，breakpoints 线性插值，invert=true）
   - moat_fixed（固定 5 分）
   - market_pos_fixed（固定 2 分）
   - sentiment_fixed（固定 3 分）

4. breakpoints 插值规则：
   - 低于最小值 → 取最小 score
   - 高于最大值 → 取最大 score
   - 两点之间 → 线性插值
   - invert 字段：breakpoints 已按"高值→低分"排列，解释器不做特殊处理，与普通字段逻辑相同

5. data_quality 计算：
   - 分母 = 非固定字段数（Phase 1 = 5）
   - data_quality = 可用非固定字段数 / 5
   - data_quality < 0.5 → 抛出 InsufficientDataError

6. 返回值 dict：quant_score、total_score、component_scores、missing_fields、data_quality

**weights.json 结构（完整）：**
{
  "version": 2,
  "updated_at": "2026-04-15",
  "frameworks": {
    "A": {
      "fundamental": {
        "roe_3y_avg": {
          "max_score": 15,
          "breakpoints": [[0, 0], [8, 5], [12, 9], [15, 15], [25, 15]],
          "interpolate": true
        },
        "net_profit_growth": {
          "max_score": 10,
          "breakpoints": [[-20, 0], [0, 2], [8, 6], [15, 10], [30, 10]],
          "interpolate": true
        },
        "debt_ratio": {
          "max_score": 10,
          "breakpoints": [[30, 10], [50, 7], [65, 3], [80, 0]],
          "interpolate": true,
          "invert": true
        },
        "gross_margin": {
          "max_score": 10,
          "breakpoints": [[0, 0], [15, 4], [25, 7], [35, 10], [60, 10]],
          "interpolate": true
        },
        "moat_fixed":       {"max_score": 10, "phase1_fixed": 5},
        "market_pos_fixed": {"max_score": 5,  "phase1_fixed": 2}
      },
      "valuation": {
        "pe_percentile_10y": {
          "max_score": 15,
          "breakpoints": [[5, 15], [20, 12], [40, 8], [60, 4], [80, 0]],
          "interpolate": true,
          "invert": true
        },
        "sentiment_fixed": {"max_score": 5, "phase1_fixed": 3}
      }
    }
  },
  "thresholds": {
    "buy_strong":   65,
    "buy_moderate": 55,
    "buy_light":    45
  }
}

**tests/test_scorer.py 要求（11 个用例）：**
- test_happy_path_all_fields：5 个字段全有，验证总分
- test_unsupported_framework：framework="B" → UnsupportedFrameworkError
- test_insufficient_data：5 个非固定字段中 3 个为 None → InsufficientDataError
- test_boundary_data_quality：3/5 字段有值（data_quality=0.6）→ 正常通过
- test_breakpoint_interpolation：ROE=10%（在[8,5]和[12,9]之间）→ 验证线性插值结果
- test_breakpoint_below_min：ROE=-5% → score=0（不负数）
- test_breakpoint_above_max：ROE=30% → score=15（不超限）
- test_invert_field_low_value：pe_percentile_10y=5 → score=15
- test_invert_field_high_value：pe_percentile_10y=80 → score=0
- test_none_field_scores_zero：gross_margin=None → score=0，字段在 missing_fields 中
- test_phase1_fixed_defaults：moat/market_pos/sentiment 固定值不受 data 影响

所有测试用 Mock，不读真实 weights.json（直接在测试中构造最小 weights dict 传入）。
```

### 结束验证

```
□ pytest tests/test_scorer.py -v → 11/11 PASSED
□ python3 -c "from scorer import score_stock; print(score_stock('600036','A',{'roe_3y_avg':16.5,'net_profit_growth':5.5,'debt_ratio':91.2,'gross_margin':56.8,'pe_percentile_10y':18}))"
  → total_score ≈ 52.2 分（见设计文档示例）
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Write` | 创建 scorer.py、weights.json、tests/test_scorer.py、tests/__init__.py |
| `Read` | 读取设计文档确认 breakpoints 数据和评分结构 |
| `Bash` | `pytest tests/test_scorer.py -v` |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/review` | 写完 scorer.py 后 | 重点检查：插值边界是否正确、invert 字段是否误加了额外逻辑 |
| `/qa` | 测试通过后 | `/qa` 对 scorer.py 做额外边界检查（提供代码路径） |
| `/checkpoint` | 全部通过后 | `/checkpoint "Step 4完成：scorer.py 11/11 测试通过"` |

---

## Step 5：pipeline.py — 4 个命令 + tests/test_pipeline.py

**推荐模型：** Sonnet 4.6（主体）；复杂 outcome-update 逻辑可升级 Opus 4.6

### 入口提示词

```
请在 ~/a-stock-tracker/ 创建 pipeline.py，
实现以下 4 个 CLI 子命令（用 argparse）：

**1. pipeline.py init**
- 对 config.WATCHLIST 中每只股票调用 lib/fetcher.cmd_fetch(code)
- 填充 tracker.db 的 stock_fundamentals 表（为 daily 做准备）
- 输出进度日志

**2. pipeline.py daily**
- 启动检查：
  weights_hash = hashlib.md5(json.dumps(weights["frameworks"], sort_keys=True).encode()).hexdigest()[:8]
  查询 predictions 表中 score_date=today 的记录，若存在且 weights_hash 不同 → 打印警告 + sys.exit(1)
- 调用 lib/fetcher.cmd_batch() 批量更新 stock_fundamentals
- 从 spot_em 快照提取 price_at_score（批量抓取后按 code 过滤 "最新价" 字段）
- 对每只 WATCHLIST 股票：
  data = cache.get_fundamentals(code)  # 返回 JSON data 字段
  report_period = data.get("report_period")
  result = scorer.score_stock(code, "A", data)
  INSERT OR IGNORE INTO predictions (...)
- 追加日志到 logs/daily_log.txt

**3. pipeline.py outcome-update**
- 确保 index_prices 表有足够历史：
  检查 MIN(score_date)，若 index_prices 比它早则跳过，否则补拉历史
  增量更新：从 MAX(date) 到 today
- 查询 score_date + 30 自然日 ≤ today 且 outcome_30d IS NULL 的记录
- 对每条记录：
  outcome = (outcome_price / price_at_score - 1) * 100
  benchmark = (index_close_at_outcome / index_close_at_score - 1) * 100
  若到期日无交易数据 → 找最近 5 交易日内可用价，estimate_flag=1；超出 5 日 → outcome 置 NULL
  benchmark 拉取失败 → outcome 正常写入，benchmark 留 NULL
- 同逻辑处理 60d / 90d（未到期的跳过，不覆盖）

**4. pipeline.py accuracy-report**
- 执行设计文档中的 SQL 查询（带 CASE signal_tier ORDER BY）
- 输出头部：
  - 若总结案记录 < 100：打印"⚠️ 样本不足（N 条），结论仅供参考，请勿据此做交易决策"
  - 打印排除条数："已排除 N 条 NULL outcome 记录"
  - 选择性偏差免责声明
- 输出到 stdout + 保存到 accuracy_report.txt

**容错规范：**
- AKShare 超时：指数退避重试 3 次（1s/2s/4s），失败记录到日志，继续处理下一只
- InsufficientDataError：记录到 skipped_stocks 日志，不中断整体
- 使用 logging 模块，禁止 print（accuracy-report 输出除外）

然后创建 tests/test_pipeline.py（15 个测试用例），
全部 Mock AKShare API，使用 tmp_path fixture 隔离数据库：
- test_daily_happy_path
- test_daily_idempotent
- test_daily_one_stock_fails
- test_daily_weights_hash_conflict
- test_daily_price_from_spot_em
- test_outcome_update_30d_normal
- test_outcome_update_estimate_flag
- test_outcome_update_null_beyond_5_days
- test_outcome_update_benchmark_failure
- test_outcome_update_60d_not_yet_due
- test_accuracy_report_empty
- test_accuracy_report_ordering
- test_accuracy_report_stat_warning
- test_index_prices_init
- test_index_prices_skip_existing
```

### 结束验证

```
□ pytest tests/test_pipeline.py -v → 15/15 PASSED
□ pytest tests/ -v → 26/26 全部通过
□ python3 pipeline.py --help → 显示 init/daily/outcome-update/accuracy-report 子命令
□ python3 -c "import pipeline" → 无导入报错
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Write` | 创建 pipeline.py 和 tests/test_pipeline.py |
| `Read` | 读取 lib/cache.py 和 lib/fetcher.py 确认 API（get_fundamentals、cmd_batch 等函数签名）|
| `Grep` | 在 lib/fetcher.py 中定位 spot_em 快照返回结构 |
| `Bash` | `pytest tests/ -v` 全量测试 |
| `Edit` | 修复测试失败的 bug |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/review` | pipeline.py 写完后 | 重点：weights_hash 冲突检查是否有竞争条件、outcome 单位是否一致（百分比） |
| `/qa` | 测试通过后 | `/qa` 额外检查：restart 幂等性、estimate_flag 边界、benchmark NULL 路径 |
| `/checkpoint` | 26/26 通过后 | `/checkpoint "Step 5完成：pipeline.py 4命令 + 26测试全绿"` |

---

## Step 6：端到端烟雾测试

**推荐模型：** Sonnet 4.6

### 入口提示词

```
请帮我在沙箱环境中完整跑一遍 a-stock-tracker 的端到端流程，
使用 2 只真实股票（600036 招商银行 + 000858 五粮液）：

1. 安装依赖：
   cd ~/a-stock-tracker
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt

2. 验证 SQLite 版本：
   python3 -c "import sqlite3; print(sqlite3.sqlite_version)"

3. 首次初始化（真实 AKShare 调用）：
   python3 pipeline.py init

4. 检查 tracker.db 中 stock_fundamentals 表：
   python3 -c "from lib.cache import get_db; db=get_db(); print(db.execute('SELECT code FROM stock_fundamentals').fetchall())"

5. 模拟 daily 运行（真实 AKShare 调用）：
   python3 pipeline.py daily

6. 检查 predictions 表：
   python3 -c "from lib.cache import get_db; db=get_db(); import json; rows=db.execute('SELECT code,score_date,total_score,weights_hash FROM predictions').fetchall(); print(rows)"

7. 验证幂等：再次运行 pipeline.py daily，确认 predictions 行数不变

8. 运行 accuracy-report（此时 outcome 全部为 NULL，应显示样本不足警告）：
   python3 pipeline.py accuracy-report

报告任何错误，包括完整 traceback。
```

### 结束验证

```
□ init 完成，2 只股票的 stock_fundamentals 有数据
□ daily 完成，predictions 表有 2 条记录（code + score_date + total_score 非 NULL）
□ 第二次 daily 不新增行（INSERT OR IGNORE 幂等）
□ accuracy-report 输出样本不足警告（无崩溃）
□ 无 UnicodeDecodeError / SQLite 语法错误 / AttributeError
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Bash` | 完整流程执行，含 pip install、pipeline 命令、DB 查询 |
| `Read` | 查看 logs/daily_log.txt 确认日志输出 |
| `Edit` | 修复烟雾测试发现的 bug |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/investigate` | 遇到 traceback 时 | 把错误信息完整粘贴，`/investigate "pipeline daily 报 AttributeError: ..."` |
| `/checkpoint` | 烟雾测试通过后 | `/checkpoint "Step 6完成：端到端烟雾测试通过，predictions 表有数据"` |

---

## Step 7：cron 定时配置

**推荐模型：** Haiku 4.5（机械性配置任务）

### 入口提示词

```
请帮我完成 a-stock-tracker 的定时任务配置，分两步：

**Step 7a：创建 cron-setup.sh**
在 ~/a-stock-tracker/ 创建 cron-setup.sh：
- 检查 .venv 是否存在，不存在则打印提示并退出
- 用 crontab -l 读取现有配置
- 追加以下两条规则（如已存在则跳过）：
  # a-stock-tracker daily (工作日 16:30)
  30 16 * * 1-5 cd ~/a-stock-tracker && .venv/bin/python pipeline.py daily >> ~/a-stock-tracker/logs/daily.log 2>&1
  # a-stock-tracker outcome-update (工作日 17:00)
  00 17 * * 1-5 cd ~/a-stock-tracker && .venv/bin/python pipeline.py outcome-update >> ~/a-stock-tracker/logs/outcome.log 2>&1
- 打印安装成功确认信息

**Step 7b：更新 README.md**
写入首次运行步骤：
1. cp ~/.claude/skills/a-stock-research/fetcher.py lib/fetcher.py（已完成则跳过）
2. cp ~/.claude/skills/a-stock-research/cache.py lib/cache.py（已完成则跳过）
3. python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
4. python3 pipeline.py init   # 为 watchlist 每只股票执行首次 fetch
5. bash cron-setup.sh         # 配置定时任务
6. python3 pipeline.py accuracy-report  # 每周手动查看（30d 结案后有数据）

写入 watchlist 更新说明：修改 config.py 中的 WATCHLIST 列表，
重新运行 pipeline.py init 为新股票预填数据。

写入注意事项：Phase 1 watchlist ≤ 20 只（批量获取约 15 min，17:00 前能完成）。
```

### 结束验证

```
□ bash cron-setup.sh 运行成功，无报错
□ crontab -l 显示两条 a-stock-tracker 规则
□ README.md 包含完整首次运行步骤
□ logs/ 目录存在（cron 日志落地路径）
```

### Claude Code 工具清单

| 工具 | 用途 |
|------|------|
| `Write` | 创建 cron-setup.sh |
| `Edit` | 更新 README.md |
| `Bash` | `bash cron-setup.sh`、`crontab -l` 验证 |

### gstack skills

| Skill | 时机 | 具体用法 |
|-------|------|---------|
| `/checkpoint` | 配置完成后 | `/checkpoint "Step 7完成：cron 已配置，项目 Phase 1 全部就绪"` |

---

## 全局 gstack 工作流建议

### 每步开始前
- `/checkpoint` 加载上次保存点，防止 context 断裂丢失进度

### 遇到 bug 时
- `/investigate "错误描述 + traceback"` — 自动从错误追踪根因，比手动 debug 快 3x

### 代码写完后
- `/review` — 对照设计文档检查实现是否符合规格（尤其是 outcome 单位、weights_hash 计算方式）

### 测试通过后
- `/qa` — 额外边界检查，发现测试用例没覆盖到的路径

### 完成全部步骤后
- `/health` — 整体代码质量检查，找出潜在的 technical debt

---

## 参考：模型选择说明

| 步骤 | 推荐模型 | 原因 |
|------|---------|------|
| Step 0 | 无（手动） | 纯验证，无代码 |
| Step 1 | Haiku 4.5 | 机械性脚手架，不需要推理 |
| Step 2 | Sonnet 4.6 | DDL 语法 + Generated Column，需要一定理解 |
| Step 3 | Haiku 4.5 | 改动 4 行，定位准确即可 |
| Step 4 | Sonnet 4.6 | 线性插值算法 + 11 个测试，需要推理 |
| Step 5 | Sonnet 4.6 | 主体逻辑；outcome-update 复杂部分可升级 Opus 4.6 |
| Step 6 | Sonnet 4.6 | 需要理解错误上下文并修复 |
| Step 7 | Haiku 4.5 | Shell 脚本 + README，机械性任务 |

---

## 快速参考：关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| lib/ 引用方式 | 复制（非 symlink） | 独立演进，原 skill 保持不变 |
| 首次初始化 | `pipeline.py init` | cmd_batch 依赖现有缓存，新项目需先 init |
| report_period 来源 | lib/fetcher.py 扩展存储 | 评分时就记录，避免后续追溯复杂 |
| benchmark 缓存 | index_prices 表 | 避免 outcome-update 时 O(N) 重复 API 调用 |
| weights 变更检测 | weights_hash（仅 frameworks 子树）| 避免 updated_at 注释字段触发虚假实验分组 |
| 同日权重冲突 | 启动检查 + sys.exit(1) | 不静默覆盖旧数据，强制手动确认 |
| 评分结构 | breakpoints + 线性插值 | 消除离散阶梯的断崖效应（ROE 14.9% vs 15.0%）|
| 命中率定义 | 跑赢沪深300（alpha > 0） | 绝对命中率在牛市中无意义 |

---

_设计文档：`docs/design.md`_
_测试计划：`docs/test-plan.md`_

---

## Phase 3 实施记录（2026-04-26/27）

以下步骤为 Phase 3 实际实施内容，已全部完成。

---

## Step 8：P0 Bug 修复

**已完成：2026-04-26**

### P0-A：删除 price_at_score=NULL 存量记录

score_date=2026-04-21 的 5 条 predictions 记录因 spot_em 故障导致 price_at_score=NULL，
无法参与后续 outcome 计算。已手动删除，保留 4 条有效记录。

```sql
DELETE FROM predictions WHERE score_date='2026-04-21' AND price_at_score IS NULL;
```

### P0-B：benchmark 数据源修复（腾讯 fallback）

东方财富 `ak.index_zh_a_hist` 自 2026-04-21 起持续故障，index_prices 表无新数据。
新增腾讯 fallback：

```python
# pipeline.py _ensure_index_prices()
try:
    df = _retry(ak.index_zh_a_hist, ...)  # 东方财富主路径
except:
    df = _retry(ak.stock_zh_index_daily_tx, symbol="sh000300")  # 腾讯 fallback
    assert "date" in df.columns and "close" in df.columns
    df = df[df["date"].astype(str) >= start_date]  # datetime.date → str 比较
```

---

## Step 9：qualitative_scores 表 DDL

**已完成：2026-04-26**

在 `lib/cache.py` 的 `get_db()` 中新增：

```sql
CREATE TABLE IF NOT EXISTS qualitative_scores (
    code        TEXT NOT NULL PRIMARY KEY,
    moat        INTEGER NOT NULL,
    market_pos  INTEGER NOT NULL,
    sentiment   INTEGER NOT NULL,
    scored_date TEXT NOT NULL
);
```

---

## Step 10：gemini_scorer.py

**已完成：2026-04-26**

新文件，136 行，包含：
- `FALLBACK = {"moat": 5, "market_pos": 2, "sentiment": 3}`
- `VALID_RANGES = {"moat": (1, 10), "market_pos": (1, 5), "sentiment": (1, 5)}`
- `CACHE_TTL_DAYS = 30`，`GEMINI_TIMEOUT_S = 10`
- `GEMINI_MODEL = "gemini-2.5-flash"`（注：原设计为 gemini-2.0-flash，已于 2026-04-27 迁移）
- `thinkingBudget: 0`（禁用思维链，避免 maxOutputTokens 被思考 token 消耗导致 JSON 截断）
- `get_qualitative_score(code, name)` → 公开接口
- `_check_cache / _write_cache / _call_gemini / _validate` → 内部实现

**关键约束：** all-or-nothing fallback，`_validate` 任何字段不合法 → 全部返回 FALLBACK，不混用部分 Gemini 值。

---

## Step 11：scorer.py Phase 3 注入

**已完成：2026-04-26**

2 行改动（`_score_field` 函数）：

```python
# 改前：固定返回 phase1_fixed，data 中的值被忽略
return float(field_cfg["phase1_fixed"])

# 改后：data 中有值时优先使用（Gemini 注入），None 时回退 phase1_fixed
return float(value) if value is not None else float(field_cfg["phase1_fixed"])
```

对应 `test_scorer.py` 中 `test_phase1_fixed_defaults` 更名为 `test_phase3_fixed_field_override`，
测试覆盖 Gemini 高值覆盖场景和 None 回退场景。

---

## Step 12：pipeline.py Phase 3 集成

**已完成：2026-04-26**

4 处改动：

1. **`_load_dotenv()`**：标准库 .env 加载（模块级调用），cron 环境无需手动 export
2. **Gemini 注入**：
   ```python
   qual = get_qualitative_score(code, name)
   data = dict(fundamentals.get("data", fundamentals))  # 必须 copy，避免污染缓存
   data["moat_fixed"] = qual["moat"]
   data["market_pos_fixed"] = qual["market_pos"]
   data["sentiment_fixed"] = qual["sentiment"]
   ```
3. **spot_em fallback**：东方财富批量失败 → 腾讯日线逐股（`stock_zh_a_hist_tx`，5日窗口）
4. **Telegram 推送**：`push_daily_signals()` 在 cmd_daily 末尾调用，try/except 包裹不阻断流程

---

## Step 13：telegram_push.py

**已完成：2026-04-26**

新文件，61 行：
- 无 token 时静默返回（不 raise）
- 查询 predictions WHERE total_score ≥ threshold AND score_date = today
- 格式：Markdown 消息，含股票代码/名称/评分/信号级别
- 推送失败只记 WARNING，不影响评分写入

---

## Step 14：weights.json 阈值下调 + .env 模板

**已完成：2026-04-26**

```json
// 改前
"buy_strong": 65, "buy_moderate": 55, "buy_light": 45

// 改后（Phase 3 Gemini 接入后总分上限提升到约80）
"buy_strong": 55, "buy_moderate": 45, "buy_light": 35
```

`.env` 模板（不提交 git，在 `.gitignore` 中）：
```
GEMINI_API_KEY=your_gemini_api_key_here
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

---

---

## Phase 3.5 实施记录（2026-04-28）

---

## Step 16：东方财富死路径移除 + sheets_sync 重构

**已完成：2026-04-28**

### Step 16-A：移除东方财富死路径（pipeline.py + lib/fetcher.py）

`ak.stock_zh_a_spot_em` 和 `ak.index_zh_a_hist` 自 2026-04 中旬起持续
RemoteDisconnected，每日 daily cron 耗尽重试后输出 ERROR 噪音。

改动：
- `pipeline.py`：`cmd_daily` 直接走腾讯日线逐股（`stock_zh_a_hist_tx`），
  移除 spot_em 尝试块；`_ensure_index_prices` 直接用 `stock_zh_index_daily_tx`
- `lib/fetcher.py`：`_fetch_spot_em_safe` 新增今日失败记忆哨兵，同进程只尝试一次
- `tests/test_pipeline.py`：全量更新 mock 目标，补 test 21（今日到期走 hist 路径）
- 测试：21/21 PASSED

### Step 16-B：sheets_sync.py 重构

删除 `push_accuracy()` Python SQL 聚合，改由 Google Sheets 公式引擎计算。

改动：
- 删除 `push_accuracy()`（35 行 Python SQL）
- 新增 `_init_accuracy_formula_tab()`：首次写入 COUNTIFS/AVERAGEIFS 公式字符串；
  `value_input_option='USER_ENTERED'` 使 Sheets 解析 = 开头为公式；非空则跳过（幂等）
- `push_predictions()`：`_cell()` 替代 `_fmt()`，数值保留 float/int 原生类型，
  RAW 模式写入后 Sheets 存为数字，COUNTIFS 数字比较才能正确工作
- `push_holdings_template()`：`enumerate` 动态行号 `f"=C{row_num}*D{row_num}"`，
  修复原代码注释 `=C2*D2` 硬编码所有行的 bug；加 `value_input_option='USER_ENTERED'`
- `sync_all()`：替换调用
- 新增 `tests/test_sheets_sync.py`（3 用例，全 mock gspread，0.26s）
- 测试：40/40 PASSED（含原有 37 个）

---

## Step 15（后续）：Gemini 模型维护

**已完成初步修复：2026-04-27**

gemini-2.0-flash 对新用户停用（404），迁移至 gemini-2.5-flash。
同时修复 gemini-2.5-flash 默认开启思维链导致 JSON 截断的问题：
- `thinkingBudget: 0`（禁用思维链）
- `maxOutputTokens: 64 → 256`
- `temperature: 0 → 1`（thinking 模型推荐值）

**维护建议：** 若 Gemini 再次 404，运行以下命令查看可用模型：
```bash
python3 -c "
import os, json, urllib.request
api_key = [l.split('=',1)[1].strip() for l in open('.env') if 'GEMINI_API_KEY' in l][0]
url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}'
with urllib.request.urlopen(url) as r:
    print('\n'.join(m['name'] for m in json.loads(r.read())['models']))
"
```
更新 `gemini_scorer.py` 中的 `GEMINI_MODEL` 常量即可。

---

## Phase 3.6 实施记录（2026-05-12）

---

## Step 17：PB 百分位因子替换 + qualitative_scores 表重构

**已完成：2026-05-12**

### Step 17-A：pe_percentile_10y → pb_percentile_10y

`pe_percentile_10y` 对亏损股（PE 为负值）无法计算，导致 38 只股票全部 NULL（评分上限
实际只有 3/5 字段可用）。改用 PB 百分位（来源：`ak.stock_zh_valuation_baidu`）解决此问题。

改动：
- `weights.json`：Framework A 中 `pe_percentile_10y` → `pb_percentile_10y`（max_score=15）；
  Framework B 中同步替换（max_score=5）；`updated_at` 更新
- `weights_hash` 同步变更（`32de6bb5` → `adb88515`）
- **2026-05-12 前的历史记录（hash=32de6bb5）与之后记录不可跨期比较**
- `lib/fetcher.py`：删除 `compute_pe_percentile`（依赖 pe_ttm，对亏损股无效）；
  新增 `_fetch_pb_percentile`，调用 `ak.stock_zh_valuation_baidu` 月度 PB 序列，
  计算10年历史分位（`PB_TIMEOUT=30s`）
- `scorer.py`：`SUPPORTED_FRAMEWORKS` 保留 `{"A"}`，移除 `"B"`（Framework B 暂停，见 Step 18）

### Step 17-B：qualitative_scores 表 PK 重构

原表 PK 仅为 `(code)`，导致同一股票只能存一行（无法保留历史），`_check_cache` 无法
"取最新行"语义。新 PK = `(code, scored_date)`。

改动：
- `lib/cache.py`：`qualitative_scores` 建表 DDL 修改 PRIMARY KEY 为 `(code, scored_date)`；
  新增自动迁移逻辑：`get_db()` 检测旧表 schema，若 PK 仍为单列则 DROP 重建
  （历史单行数据舍弃，影响极小：qualitative_scores 实测为 0 行）
- `gemini_scorer.py`：`_write_cache` 改为 `INSERT OR IGNORE`（幂等，同日重复写入跳过）；
  `_check_cache` 改为 `ORDER BY scored_date DESC LIMIT 1`（取最新行）
- 新增测试用例 7、8（多行返回最新；同日 INSERT OR IGNORE 保留第一次）

---

## Step 18：accuracy-report 增强 + Framework B 暂停

**已完成：2026-05-12**

### Step 18-A：accuracy-report 增强

两个新节段，帮助长期追踪模型健康状态：

**Gemini 评分漂移检测节（CT2）：**
- 统计过去 30 天内有 Gemini 分（非 fallback）的股票，与30天前的分比较
- 漂移 > 10 分（`|moat_new - moat_old|` 或其他维度）时高亮显示
- 若无足够历史数据则显示"数据不足，暂无漂移分析"

**Framework B 重启进度节（CT3）：**
- 显示当前 Framework A 30d 结案记录数 vs 目标（100条）
- 显示任一信号层级是否达到 `hit_rate_vs_300 > 55%`
- 未达标时提示预计还需多少周（基于当前日均结案速率估算）

### Step 18-B：Framework B 暂停

Framework B 已有 73 条历史记录（2026-04-29 至 2026-05-11），
**暂停原因：** 等待 Framework A 达到 Phase 4 启动门槛后一并优化/重启。

- `scorer.py`：`SUPPORTED_FRAMEWORKS = {"A"}`（移除 "B"）
- `pipeline.py`：`cmd_daily` 仅对 Framework A 运行
- 73 条历史记录保留，不删除
- **重启方式：** 在 `scorer.py` 的 `SUPPORTED_FRAMEWORKS` 集合中加回 `"B"` 即可

### 测试覆盖

- 新增 11 个用例（test_gemini_scorer × 4，test_pipeline × 5，test_scorer × 2）
- 全部 56 个用例通过（0 failed）
