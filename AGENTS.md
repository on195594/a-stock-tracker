# 仓库级开发约束

本文件适用于整个仓库。修改代码或目录前，先阅读本文件、`docs/architecture.md`
以及与任务直接相关的当前文档。

## 事实来源与当前状态

- 当前状态以 `README.md`、`docs/project-status.md` 和 `TODOS.md` 为准；目录与依赖边界以
  `docs/architecture.md` 为准。
- `docs/design.md`、`docs/impl-plan.md` 及状态文档中标为“历史”的章节只供追溯，不能据此恢复旧入口、旧目录、旧定时任务或已取消阶段。
- 文档冲突时优先采用更新且明确标注为当前状态的文档；不要用历史计划覆盖当前结案决定。
- 项目已于 2026-09-21 以 `CLOSED_UNPROVEN` 结案。当前活动范围仅包括通用 TuShare 数据采集、历史数据完整性和安全修复。

## 产品边界

- 项目的产品身份限定为 **A 股选股研究与候选审查工具**，不是通用金融平台。
- 不扩展到自动下单、账户或持仓管理、交易执行、多市场投研、泛财经内容生成、Google Sheets 数据层、LLM 总调度器或自动调权系统。
- 工程完成、测试通过、报告生成或模型调用成功都不代表存在选股 alpha、有效买点或可交易策略。
- Framework A、L3、历史评分、manifest 和 Framework A 策略报告只作为历史证据保留，不得恢复定时评分、策略报告、Telegram 观察名单、cohort 收样、扩池或实盘用途。
- 新的选股研究应另立有明确假设、可达样本、硬截止日和样本外验证的新项目。不得借维护任务在本仓库原地续期；若用户提出重启研究，停止本仓库实现并先确认新项目边界，不得仅通过修改本仓库状态文档原地复活。

## 防止过度工程化

- 先确认任务服务于当前活动链路并有真实消费者；没有当前需求或消费者的能力不实现。
- 优先复用现有代码，其次使用标准库和已安装依赖；没有可验证缺口时不增加依赖、抽象层或配置项。
- 采用能解决问题的最小改动。禁止为“以后可能需要”预建接口、工厂、插件、并行管道、备用入口或脚手架。
- 一个事实只保留一个来源，不建立与 SQLite 或现有数据任务并行的第二套数据与报告口径。
- 只读研究不创建 migration、seal、reviewer、authorization 或发布治理系统；只有具体的生产不可逆风险才允许增加相称的保护。
- 默认不新建 spec、plan、review 或证据目录。小改动直接修改代码、测试及已有当前文档；只有用户明确要求或变更涉及高风险协议时才新增文档。
- 除当前状态文档明确要求原位保留的历史代码、manifest、报告和审计证据外，其他零消费者和已退休代码由 Git 历史承担恢复职责，不保留“冻结备用”副本，也不为死代码增加兼容层。
- 测试应覆盖活动行为和数据安全边界，不为已取消功能维护测试体系，也不通过削弱结构断言让无关改动通过。

## 数据与投资语义

- 数据来源、采集时间、财报期、fallback 和缺失原因必须可追溯；展示层不能成为数据真相来源。
- 数据任务失败时保留上一份有效证据并告警；不得预填未来交易日、伪造 readiness、静默改变字段口径或改写历史记录。
- 不修改历史核心评分字段，不回填历史输入，不改活动权重、阈值或 manifest 来延续已结案实验。
- 不把风险提示称为已验证买点，不把固定股票池结果外推为全市场选股能力，不把历史收益称为可执行回测。
- 测试不得访问真实网络、生产数据库、通知服务或模型 API；在 provider 边界使用 fixture/mock。

## 目录与依赖边界

- 生产 Python 代码统一放在 `a_stock_tracker/`；`pipeline.py` 只保留为向后兼容的薄启动器。
- 按既有职责放置模块：
  - `a_stock_tracker/data/`：数据库、provider、采集、readiness、物化和缓存；
  - `a_stock_tracker/signals/`：确定性风险信号；
  - `a_stock_tracker/reporting/`：通知与展示；
  - `a_stock_tracker/qualitative/`：已有本地定性分的只读选择。
- 受版本控制的配置放在 `config/`；运行产物放在已忽略的 `data/`、`logs/` 或 `artifacts/`；运维/研究入口放在 `scripts/`，测试放在 `tests/`，维护文档放在 `docs/`。
- 凭据、私钥、token 和 service-account JSON 绝不能进入版本控制；本地凭据只能位于已忽略的 `credentials/` 或环境变量中。
- 不重建 `lib/`，不新增根目录业务 Python、根目录 JSON 或 `qualitative_v2_*.py`。
- 生产代码使用以 `a_stock_tracker` 开头的绝对导入，不修改 `sys.path`，不使用已退休的 `lib`、`config`、`scorer`、`gemini_scorer`、`telegram_push`、`sheets_sync` 或 `qualitative_v2_*` 导入名。
- 只有启动器可以导入 `a_stock_tracker.cli`；可复用模块不得反向依赖应用编排层。
- `data/` 与 `signals/` 不依赖 `reporting/`；`qualitative/` 不联网、不调用模型、不写定性缓存。保持 `docs/architecture.md` 中的依赖方向。

## 结构变更

- 不为方便而新增顶层文件、顶层目录或一级业务领域。
- 新架构区域必须先获得用户明确批准，并在同一变更中更新 `docs/architecture.md`、本文件和 `tests/test_project_structure.py`。
- 修改共享行为前先查找所有调用方，在共同根因处修复；不要为单一路径复制补丁。

## 验证

纯文档变更至少运行：

```bash
git diff --check
```

源码、配置、可执行脚本或结构变更运行完整活动产品检查：

```bash
.venv/bin/python -m pytest tests/test_project_structure.py -q
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```
