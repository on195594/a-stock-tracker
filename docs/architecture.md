# 项目目录与模块边界

业务代码统一位于 `a_stock_tracker/`，根目录只保留项目级入口、配置、文档、脚本、测试和运行产物目录。

```text
a-stock-tracker/
├── a_stock_tracker/          # 可导入的业务包
│   ├── cli.py                # 已退休 Framework A 编排（历史保留）
│   ├── config.py             # watchlist 与历史运行配置
│   ├── paths.py              # 项目路径的单一来源
│   ├── scoring.py            # Framework A 历史评分实现
│   ├── data/                 # 活动的 SQLite、行情与 TuShare 数据链
│   ├── signals/              # L3 v2 历史风险信号实现
│   ├── reporting/            # 历史 Telegram 与策略报告实现
│   └── qualitative/          # 历史本地定性分只读选择
├── config/                   # 受版本控制的运行配置
│   ├── weights.json
│   ├── experiment_manifest.json # 已结案 S2 实验的历史身份与收样边界
│   └── trading_calendar.json # 有来源的本地日历种子与 fail-closed 回退
├── scripts/                  # 通用数据运维、采集和诊断入口
├── tests/                    # 活动数据链与安全边界测试
├── docs/                     # 当前文档、运行手册与历史审查
├── data/                     # 忽略的运行态日历、数据库与缓存
├── artifacts/                # 运行产物（忽略，不提交）
└── pipeline.py               # 历史 CLI 的薄兼容启动器（不受 cron 调用）
```

## 个人同业选股工具边界

`docs/plans/2026-09-22-personal-stock-selection-roadmap.md` 与 `docs/specs/2026-09-22-peer-screen-spec.md` 是本项目正式文档。工具实现在仓库旁的独立公开 Git 仓库 [`a-stock-screen`](https://github.com/on195594/a-stock-screen)，通过显式只读连接消费旧库；它不属于本仓库业务包，不新增本仓库模块、数据库 schema、入口或定时任务。

## 依赖方向

- 活动链只由 `scripts/` 编排 `data/` 的采集、readiness 和原子物化；`data/` 不依赖历史评分、通知或展示层。
- `cli.py`、`scoring.py`、`signals/`、`reporting/` 与 `qualitative/` 仅为结案审计保留，不属于活动生产链，也不再维持退休功能测试。
- 历史 `reporting/` 不能成为数据真相来源；历史 `qualitative/` 不联网、不调用模型、不写定性缓存。
- 其他模块不得反向导入 `cli.py`。

新增业务模块时先选择上述领域，不再向仓库根目录添加 Python 实现文件。新增运行时生成文件应进入已忽略的 `data/`、`logs/` 或 `artifacts/`。S2 `experiment_manifest.json` 作为 `CLOSED_UNPROVEN` 实验的历史配置保留，不再驱动定时评分或报告；历史准确率报告仍位于 `artifacts/reports/accuracy-report.txt`。工作日 16:00 的 QFQ 数据任务继续从 TuShare SSE `trade_cal` 原子刷新 `data/trading_calendar.json` 及按 hash 保存的规范化提供方返回行，并核对已有官方休市证据；刷新失败恢复上一份文件，不得预填未来日期。凭据、私钥和 token 只能放在被忽略的 `credentials/` 或环境变量中，绝不能纳入版本控制。

## 自动约束

- 根目录 `AGENTS.md` 是所有后续编码任务必须遵守的仓库级约束。
- `tests/test_project_structure.py` 检查顶层白名单、业务包领域、配置位置、旧目录/旧导入、`sys.path` 注入和 CLI 反向依赖。
- 新增顶层文件、顶层目录或一级业务领域不是普通实现细节；必须先获得用户明确批准，并在同一变更中更新本文件、`AGENTS.md` 和结构测试。
- 任何源码或目录调整都至少运行 `pytest tests/test_project_structure.py -q`；提交前运行默认
  活动产品测试、`ruff check .`、`ruff format --check .`、mypy 和 `git diff --check`。
