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
  → 自动 30/60/90 日 QFQ 策略报告
  → Telegram 未验证观察名单
```

2026-07-28 三轮减法已完成：前两轮移除 Framework B、L3 v1 新写入与回填、Google Sheets、在线 Gemini、M4/M5、历史 qualitative/outcome shadow 和手工离线入口；阶段一收口进一步删除 legacy outcome/Phase4 和手工报告入口，并降级 Telegram 语义。Git 历史承担恢复职责，生产数据库历史行不做破坏性迁移。

定性输入不再联网或更新：daily 只读已有本地 v1/v2 分数，不存在或无效时使用固定 fallback。16:00 QFQ 自动任务同时更新沪深300全收益指数；17:30 daily 落库后自动刷新 30/60/90 日总收益、IC、Q5−Q1 spread 和非重叠批次回撤报告。

## 常用命令

```bash
python3 scripts/fetch_qfq_daily_bars_tushare.py
python3 scripts/check_market_data_readiness.py --scope cron
python3 -m scripts.run_tushare_primary_production_cycle daily
python3 -m scripts.run_tushare_primary_production_cycle weekly
python3 pipeline.py daily
```

自动报告写入被 Git 忽略的 `artifacts/reports/accuracy-report.txt`。SQLite 是数据真相来源；Telegram 只展示未验证观察名单，不构成买入建议。

## 自动任务

`cron-setup.sh` 管理以下任务：

- 周六 10:00：财务与分红刷新；
- 工作日 16:00：TuShare QFQ 日线；
- 工作日 17:15：TuShare 估值物化；
- 工作日 17:30：Framework A daily、自动策略报告与 Telegram 观察名单。

脚本会清理已退休的 Framework B、weekly PM、qualitative acceptance 和 legacy outcome-update 旧 cron 规则。

## 目录

- `a_stock_tracker/data/`：SQLite、行情、TuShare ingestion/readiness/materialization；
- `a_stock_tracker/signals/`：L3 v2 确定性风险信号；
- `a_stock_tracker/reporting/`：Telegram 与策略报告；
- `a_stock_tracker/qualitative/`：已有 qualitative 分数的只读选择；
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

daily 不读取 Gemini 或 Google 凭证；仓库不再提供 qualitative writer。

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
