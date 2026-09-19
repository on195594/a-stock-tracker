# 项目目录与模块边界

业务代码统一位于 `a_stock_tracker/`，根目录只保留项目级入口、配置、文档、脚本、测试和运行产物目录。

```text
a-stock-tracker/
├── a_stock_tracker/          # 可导入的业务包
│   ├── cli.py                # 主编排 CLI 的真实实现
│   ├── config.py             # watchlist 与运行配置
│   ├── paths.py              # 项目路径的单一来源
│   ├── scoring.py            # Framework A 确定性评分
│   ├── data/                 # SQLite、行情、TuShare readiness/materialization 与缓存
│   ├── signals/              # L3 v2 风险信号
│   ├── reporting/            # Telegram 与策略报告
│   └── qualitative/          # 已有本地定性分的只读选择
├── config/                   # 受版本控制的运行配置
│   ├── weights.json
│   ├── experiment_manifest.json # S2 已登记实验身份与收样边界
│   └── trading_calendar.json # 有来源、按 as-of 更新的本地只读交易日历证据
├── scripts/                  # 独立运维、采集和研究入口
├── tests/                    # 自动化测试与 fixture
├── docs/                     # 设计、计划、规范、runbook 与历史审查
├── reviews/                  # 受控里程碑证据（保留原路径）
├── artifacts/                # 运行产物（忽略，不提交）
└── pipeline.py               # 兼容旧命令的薄启动器
```

## 依赖方向

- `data/` 与 `signals/` 提供底层能力，不依赖通知或展示层；TuShare production cycle 只编排 data 层采集、readiness 和原子物化。
- `reporting/` 可以读取数据与评分结果，但不能成为数据真相来源。
- `qualitative/` 只读取已有 SQLite 定性分，不采集证据、不调用模型、不写定性缓存。
- `cli.py` 负责组装上述模块；其他模块不得反向导入 CLI。

新增业务模块时先选择上述领域，不再向仓库根目录添加 Python 实现文件。新增运行时生成文件应进入已忽略的 `data/`、`logs/` 或 `artifacts/`，其中准确率报告固定写入 `artifacts/reports/accuracy-report.txt`；受审查的静态配置（包括 S2 `experiment_manifest.json` 与 `trading_calendar.json`）进入 `config/`，由 `paths.py` 提供项目绝对路径。当前 manifest 登记固定 35 股、精确 scoring hash 与 2026-09-18 至 2026-11-17 收样范围；默认报告加载有来源的本地只读日历证据。日历不得预填未来日期，`evaluation_as_of` 超过其证据 `as_of` 前必须从批准来源刷新，否则报告 fail-closed。凭据、私钥和 token 只能放在被忽略的 `credentials/` 或环境变量中，绝不能纳入版本控制。

## 自动约束

- 根目录 `AGENTS.md` 是所有后续编码任务必须遵守的仓库级约束。
- `tests/test_project_structure.py` 检查顶层白名单、业务包领域、配置位置、旧目录/旧导入、`sys.path` 注入和 CLI 反向依赖。
- 新增顶层文件、顶层目录或一级业务领域不是普通实现细节；必须先获得用户明确批准，并在同一变更中更新本文件、`AGENTS.md` 和结构测试。
- 任何源码或目录调整都至少运行 `pytest tests/test_project_structure.py -q`；提交前运行默认
  活动产品测试、`ruff check .`、`ruff format --check .`、mypy 和 `git diff --check`。
