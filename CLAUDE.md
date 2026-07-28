# a-stock-tracker 开发入口

## 目标

本项目是 A 股选股与买入决策支持系统。当前唯一优先级是证明 Framework A 与 L3 v2 是否能改善风险调整后收益。

## 当前生产边界

- 只写 Framework A prediction；
- TuShare 提供基本面、估值、当前价格、QFQ 行情和 outcome 行情；
- L3 v2 只称为极端下跌风险门禁；
- 定性输入只读已有本地缓存或固定 fallback，daily 不调用外部模型；
- SQLite 是真相来源，Telegram 是唯一展示面；
- 不存在 Framework B、L3 v1 新写入、Sheets、qualitative acceptance 或 weekly PM 活动链。

历史数据库中的 Framework B、L3 v1 和 outcome shadow 数据保持不动，但不得据此恢复入口。

## 架构

- `a_stock_tracker/data/`：数据库、provider、采集、readiness、物化；
- `a_stock_tracker/signals/`：L3 v2；
- `a_stock_tracker/reporting/`：Telegram 与策略报告；
- `a_stock_tracker/qualitative/`：本地 qualitative 读取；历史研究代码将在第二批删除；
- `a_stock_tracker/cli.py`：应用编排；
- `pipeline.py`：薄兼容启动器。

## 约束

- 不修改历史核心评分字段；
- 不在阶段一结论前修改权重或阈值；
- 不把风险门禁称为已验证买点；
- 不新增手工备用入口；
- 不增加治理、reviewer、seal、authorization 或 migration；
- 不在测试中真实访问网络；
- 新功能必须直接验证 alpha、买入时点或回撤。

## 验证

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
