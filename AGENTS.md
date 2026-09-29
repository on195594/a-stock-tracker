# 开发约束

先读 README 和 `docs/architecture.md`，再查改动模块及全部调用方。不全量预读历史设计、阶段计划和审查记录。

## 唯一目标

只维护通用 TuShare 数据采集、历史数据完整性和安全性。Framework A 已 `CLOSED_UNPROVEN`；评分、L3、定性选择、策略报告及 Telegram 观察名单的退休源码和启动器由 Git 历史保留，不再留在活动目录。此归档政策取代旧的“历史代码原位保留”要求。

- 不恢复实验、cohort、扩池、调权、自动交易、账户/持仓管理或 LLM 调度平台。
- Screen 当前设计/待办/实现归其 `docs/FLET_DESIGN.md`；本仓 `docs/specs/2026-09-22-peer-screen-spec.md` 仅保留原 peer-screen-v1 规则合同，不维护第二份产品状态。
- 不把工程完成、风险提示或历史收益称为投资 alpha、有效买点或可执行回测；超出已批准个人工具范围的新投资研究须另立明确假设、可达样本、硬截止日和样本外验证的项目。

## 数据与权限

- 数据来源、采集时间、财报期、缺失、fallback 必须可追溯。失败保留上一份有效证据并告警；不预填未来日历、不伪造 readiness、不静默改变口径。
- 历史数据库行、manifest、权重、报告、日历证据和原始审计记录保持不动；不回填、不重算，不借归档修改 schema、部署、cron 或生产状态。
- 凭据、私钥、token 不进 Git；仅使用环境或已忽略的本地配置。运行资料位于已忽略的 data/logs/artifacts，不是可顺手删除的垃圾。
- 测试只用临时库和 fixture/mock，不访问真实网络、通知、模型或生产数据库。

## 实现与文档

- 源码统一在 `a_stock_tracker/`：config/paths 提供配置和路径，data 提供数据库、Provider、采集、readiness、物化及缓存；scripts 是启动入口。
- 保持包绝对导入，不修改 sys.path；不重建根目录 Python、旧 lib、退休业务领域或兼容启动器。
- 活动代码不得导入退休的 cli/scoring/signals/reporting/qualitative；结构测试必须阻止它们回流。
- 复用现有实现、标准库和已安装依赖；没有真实消费者，不添加抽象、配置、备用入口或第二套数据口径。
- 小改动只更新当前 owner 文档；不再生成 spec/plan/review/seal/authorization/migration 流程。真正的高风险协议或用户明确要求除外。
- 新增业务领域须明确授权，并同步架构和结构测试；历史源码恢复用 `git show`，不恢复为默认运行路径。

## 验证

文档修改至少 `git diff --check`。源码、配置、脚本或结构改动先窄测，再运行：

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
git diff --check
```

不削弱活动数据安全测试；未运行和失败如实报告。提交、推送、部署、重启和数据操作分别以本次用户授权为准。
