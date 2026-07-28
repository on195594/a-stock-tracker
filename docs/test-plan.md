# 测试计划

## 默认活动范围

默认测试覆盖：

- Framework A 评分和 prediction 写入；
- TuShare 行情、估值、财务、分红、readiness 和原子物化；
- QFQ 缓存与 L3 v2；
- 本地 qualitative 快照选择与 prediction 决策时快照；
- Telegram 主推/候补语义；
- outcome-update，以及 QFQ/全收益口径的 IC、spread、收益与回撤报告；
- 项目结构与数据源 registry。

两批减法后不再测试 Framework B、L3 v1、Sheets、在线 Gemini、qualitative acceptance、weekly PM、M4/M5、历史 qualitative writer/shadow 或 outcome shadow，因为对应活动代码已删除。

## 网络边界

测试不得真实调用 TuShare、AKShare、Telegram 或模型 API。provider 和 Telegram 必须在边界处替换为 fixture/mock。

## 必跑命令

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
