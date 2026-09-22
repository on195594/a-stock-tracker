# 测试计划

## 默认活动范围

默认测试只覆盖结案后仍运行的通用数据链和安全边界：

- TuShare 行情、估值、财务、分红、readiness 与原子物化；
- SSE 交易日历、QFQ 个股日线和沪深300全收益采集；
- provider fail-closed、来源审计、缓存完整性与故障恢复；
- 项目结构与数据源 registry。

Framework A 评分/prediction、L3、qualitative 选择、策略报告和 Telegram 观察名单已经退出活动产品，因此不再由默认测试维护。对应生产实现、manifest、历史数据库行和审计材料仅为结案证据保留；如需恢复研究，必须另立项目和测试合同。

## 网络边界

测试不得真实调用 TuShare、通知服务或模型 API。活动 provider 必须在边界处使用 fixture/mock，并使用临时数据库和目录隔离运行态数据。

## 必跑命令

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
