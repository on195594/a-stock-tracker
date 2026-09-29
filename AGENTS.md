# 仓库级开发约束

本文件适用于整个仓库。修改代码或目录前，先阅读本文件、`docs/architecture.md`
以及与任务直接相关的当前文档。

## 事实来源与当前状态

- 当前目标与状态只在 `README.md` 维护；`docs/project-status.md` 和 `TODOS.md` 仅作入口与维护边界。目录与依赖以 `docs/architecture.md` 为准。个人工作台当前设计和待办归相邻 `a-stock-screen/docs/FLET_DESIGN.md`；本仓 2026-09-22 路线图与 Spec 保留原 peer-screen-v1 规则依据，不再维护产品进展。
- 历史结论以 `docs/evolution-roadmap.md`、`CHANGELOG.md` 和 `docs/reviews/` 为追溯入口；已删除的旧设计、计划和 Spec 只在 Git 历史中保留，不能据此恢复旧入口、旧目录、旧定时任务或已取消阶段。
- 文档冲突时优先采用更新且明确标注为当前状态的文档；不要用历史计划覆盖当前结案决定。
- Framework A 已于 2026-09-21 以 `CLOSED_UNPROVEN` 结案。仓库内活动代码范围仍仅包括通用 TuShare 数据采集、历史数据完整性和安全修复；2026-09-22 获准的个人同业选股工具只在上述正式文档规定的相邻独立 Git 仓库 `a-stock-screen/` 实现。

## 产品边界

- 本仓当前职责限定为 **通用 TuShare 数据采集与历史审计维护**，不是活动选股引擎或通用金融平台。
- 不扩展到自动下单、账户或持仓管理、交易执行、多市场投研、泛财经内容生成、Google Sheets 数据层、LLM 总调度器或自动调权系统。
- 工程完成、测试通过、报告生成或模型调用成功都不代表存在选股 alpha、有效买点或可交易策略。
- Framework A、L3、历史评分、manifest 和 Framework A 策略报告只作为历史证据保留，不得恢复定时评分、策略报告、Telegram 观察名单、cohort 收样、扩池或实盘用途。
- `peer-screen-v1` 是已批准的个人研究方向，不是 Framework A 续期或投资 alpha 证明；当前设计、待办和实现均归相邻 `a-stock-screen/`，本仓仅保留原规则依据。超出已批准范围的新选股研究仍须另立明确假设、可达样本、硬截止日和样本外验证的新项目，不得恢复 Framework A。

## 防止过度工程化

- 先确认任务服务于当前活动链路并有真实消费者；没有当前需求或消费者的能力不实现。
- 优先复用现有代码，其次使用标准库和已安装依赖；没有可验证缺口时不增加依赖、抽象层或配置项。
- 采用能解决问题的最小改动。禁止为“以后可能需要”预建接口、工厂、插件、并行管道、备用入口或脚手架。
- 一个事实只保留一个来源，不建立与 SQLite 或现有数据任务并行的第二套数据与报告口径。
- 只读研究不创建 migration、seal、reviewer、authorization 或发布治理系统；只有具体的生产不可逆风险才允许增加相称的保护。
- 默认不新建 spec、plan、review 或证据目录。小改动只更新行为所属的当前文档，不再复制版本、任务或部署状态。个人工作台的变化更新其 `docs/FLET_DESIGN.md`；只有用户明确要求或涉及高风险协议时才新增文档。
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
