# a-stock-tracker

A 股选股与买入决策支持系统。系统帮助用户判断“买什么、什么时候买”，目标是提高获得较高风险调整后收益的概率，不承诺收益。

## 当前阶段

项目处于三阶段路线的阶段一：证明现有 Framework A 与 L3 v2 是否具有可复验投资价值。

当前自动生产链：

```text
TuShare 基本面、估值与 QFQ 行情
  → Framework A 评分
  → L3 v2 极端下跌风险门禁
  → SQLite 决策快照
  → Telegram 主推/候补
  → 30/60/90 日 outcome 与策略报告
```

2026-07-28 第三轮减法第一批已经移除 Framework B、L3 v1 新写入与回填、Google Sheets、在线 Gemini、qualitative acceptance 和 weekly PM loop。历史数据库中的 B、L3 v1 字段和记录保持只读，不做破坏性迁移。

定性输入不再联网：daily 读取已有本地 `qualitative_scores` 最新值；不存在或无效时使用固定 fallback。已有合法 qualitative v2 数据仍可被自动读取。

## 常用命令

```bash
python3 pipeline.py init
python3 pipeline.py weekly
python3 pipeline.py daily
python3 pipeline.py outcome-update
python3 pipeline.py accuracy-report

python3 scripts/fetch_qfq_daily_bars_tushare.py
python3 scripts/check_market_data_readiness.py --scope cron
python3 -m scripts.run_tushare_primary_production_cycle daily
python3 -m scripts.run_tushare_primary_production_cycle weekly
```

`accuracy-report` 写入被 Git 忽略的 `artifacts/reports/accuracy-report.txt`。SQLite 是数据真相来源；Telegram 是唯一生产展示面。

## 自动任务

`cron-setup.sh` 管理以下任务：

- 周六 10:00：财务与分红刷新；
- 工作日 16:00：TuShare QFQ 日线；
- 工作日 17:15：TuShare 估值物化；
- 工作日 17:30：Framework A daily 与 Telegram；
- 工作日 18:00：outcome-update。

脚本会清理已退休的 Framework B、weekly PM 和 qualitative acceptance 旧 cron 规则。

## 目录

- `a_stock_tracker/data/`：SQLite、行情、TuShare ingestion/readiness/materialization；
- `a_stock_tracker/signals/`：L3 v2 确定性风险信号；
- `a_stock_tracker/reporting/`：Telegram 与策略报告；
- `a_stock_tracker/qualitative/`：当前 qualitative 本地读取及待第二批删除的历史研究代码；
- `scripts/`：自动运维、采集和当前研究入口；
- `tests/`：默认活动产品测试；
- `docs/`：架构、路线图、状态和历史记录。

## 配置

复制 `.env.example` 为 `.env`，按需设置：

```dotenv
TUSHARE_TOKEN=你的_TuShare_Token
TELEGRAM_BOT_TOKEN=你的_Telegram_Bot_Token
TELEGRAM_CHAT_ID=你的_Telegram_Chat_ID
QUALITATIVE_V2_MODE=on
```

daily 不读取 Gemini 或 Google 凭证。

## 验证

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```

当前方向和退出条件以 `docs/evolution-roadmap.md` 为准；运行事实与 blocker 以 `docs/project-status.md` 为准；当前动作只看 `TODOS.md`。
