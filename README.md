# a-stock-tracker

个人 A 股研究工作台：**发现候选 → 关注公司 → 更新事实 → 看懂变化 → 记录下一步 / 确认已阅**。

只维护这条研究流程。不提供买入评分、交易、账户或回测。原 tracker 的 QFQ、基准收益、分红/财务物化采集和评分实验退出活动代码；历史数据库和原始证据不删除、不重算。`a-stock-screen` 的研究能力已收敛到本项目，新代码无需相邻仓库。

## 使用

- **同业发现**：从项目参照清单选公司，主动扫描，查看前三与参照的 PB、三年逐年 ROE、数据日、来源与缺口。排序只安排核查顺序。
- **我的关注**：一键关注，无需填写理由；按异常、公司状态、事实变化、仅日期变化显示。暂停后停止参与更新，保留笔记。
- **更新资料**：冻结当时未暂停的 1—50 家公司，由独立 worker 获取资料。关闭页面不取消任务，重试同一次提交不重复执行。
- **看变化 / 已阅**：详情先显示当前事实和相对上次已阅的变化；“标记本次变化已阅”直接显示在事实区，不必展开或保存笔记。缺口、损坏、日期倒退会阻止确认。
- **下一步与笔记**：可选、可空；保存不推进已阅，多页面编辑不互相覆盖。原笔记链接继续保存，暂不提供打开入口。

每次主动更新都重新核查财务。失败保留上一份可用资料，并明确显示本次失败，旧资料不冒充最新。PB/ROE 没覆盖的现金流、杠杆、资产质量与审计意见仍需人工核查。

## 本地演示

Python 3.13、uv；不需要 Token、旧数据库或 screen 仓库。

```bash
make setup
.venv/bin/python -m playwright install chromium
make demo
# http://127.0.0.1:8550
```

演示使用 `.local/demo` 和合成快照；重复启动保留演示笔记。只绑定 loopback，不把 demo 暴露到公网。

```bash
make check
make test-mobile
# 360/390/430px 各十轮输入、保存、断线重连与返回
.venv/bin/python -m tests.test_mobile --reliability
```

浏览器测试用临时目录、合成数据并阻断外部请求；移动尺寸 Chromium 不等于本人手机、OAuth 或真实数据验收。

## 配置与运行

- `config/anchors.json`：同业发现参照清单，区别于界面内的个人关注。首次沿用原清单，不再执行或解析其他项目的 Python 文件。
- `config/trading_calendar.json`：仅提供日历采集的历史起始日，不作为在线研究日期的证据。
- `data/calendar/`：交易日历与原始来源证据。Web 只读；日历任务可写。默认研究日是上海今天之前、日历已证明的最近交易日。
- `data/research/`：私人 SQLite、不可变快照与工作区模式标记。只允许 Web/worker 访问，不放入静态资源或 Git。
- `.web.env`：从 `.web.env.example` 配置 GitHub OAuth 与数字用户 ID。
- `.worker.env`：从 `.worker.env.example` 配置 TuShare Token；Web 不接收该文件。

Web/worker 使用 `STATE_DIR` 或 `TRACKER_DATA_DIR` 下的 `research/`；日历使用 `TRACKER_DATA_DIR`，参照清单和日历种子使用 `TRACKER_CONFIG_DIR`。管理命令使用显式 `--state-dir`（默认 `.local/demo`），备份/恢复使用 `--source` 与 `--destination`。Compose 固定容器内挂载路径，不跟随主机 shell 的这些变量。

```bash
# 仅新建一个明确为空的生产工作区；已有 screen 用户应走下述迁移，不能重新初始化
.venv/bin/python -m a_stock_tracker.manage init --mode production --state-dir data/research --journal-mode DELETE
# 主动更新日历；从环境读取 TUSHARE_TOKEN，不读取旧项目 .env
make calendar
# Compose 中的独立日历任务（需已有镜像）
docker compose --profile maintenance run --rm calendar
```

生产部署沿用 Compose + Nginx。`./deploy.sh` 需要明确部署授权；它检查初始化工作区与日历后构建 Web、验证健康、更新代理并启动 worker。发现旧 screen Web 仍运行会拒绝部署；不会自动停止旧项目。失败会非零退出，代理配置校验失败会恢复原配置，但不承诺自动回滚已启动的新 Web。

## 旧研究记录迁移与恢复

保留原研究 schema、快照与已阅语义，无需回填或重算。复用经过校验的离线备份/恢复入口，将旧工作区复制到全新目录；不会覆盖源目录，恢复的未完成任务标为 interrupted，不自动重新取数。

```bash
.venv/bin/python -m a_stock_tracker.manage backup --source /path/to/old-workspace --destination /path/to/new-backup --mode production
.venv/bin/python -m a_stock_tracker.manage restore --source /path/to/new-backup --destination /path/to/new-research --mode production
```

首次从旧 screen 迁移时，先停止旧 Web/worker 和旧采集任务，再取最终备份、恢复到新的 `data/research`，核验后启动新版本。后续常规部署直接使用现有工作区，不重复迁移或初始化。研究工作区必须可由执行命令的用户访问。

日历是唯一周期任务；主机时区为 `Asia/Shanghai` 时，工作日 16:00 的示例如下（先创建 `logs/`；按本机安装位置调整 Docker 绝对路径）：

```cron
0 16 * * 1-5 cd /home/lin/a-stock-tracker && /usr/bin/docker compose --profile maintenance run --rm calendar >> logs/calendar.log 2>&1
```

新日历应通过一次显式更新生成，不能把历史日历改日期后当作新证据。旧 tracker 数据库、财报观测、日志和 screen Git 历史都应先归档，再退役旧项目。数据迁移、替换生产、cron 修改及旧目录归档分别按切换授权执行。

- [模块与数据合同](docs/architecture.md)
- [开发约束](AGENTS.md)
- [peer-screen-v1 规则依据](docs/specs/2026-09-22-peer-screen-spec.md)

2026-09-29 已完成本机正式切换。原 tracker 源码、旧评审、实验 manifest/权重与结案记录可由 Git 提交 `9ea2f45` 恢复；screen 来源提交为 `7be4ffd`，本地历史保存在 `refs/archive/a-stock-screen`。旧文档不再留在当前目录充当操作指引；Framework A 仍为 `CLOSED_UNPROVEN`。运行数据与回滚副本独立保存在私有归档，不随源码清理。
