# a-stock-tracker

个人 A 股基本面研究助手：**指定公司 / 发现候选 → 核查证据 → 记录判断与反证 → 主动更新 → 复核变化 / 确认已阅**。

面向沪深主板非金融公司的个人中长期研究，帮助安排核查顺序、保留研究依据并发现事实变化。不提供买卖信号、目标价、仓位建议、交易、账户或回测；同业排名不代表投资价值。金融行业、科创板、创业板和北交所不在新公司研究范围。原 tracker 的 QFQ、基准收益、分红/财务物化采集和评分实验退出活动代码；历史数据库和原始证据不删除、不重算。`a-stock-screen` 的研究能力已收敛到本项目，新代码无需相邻仓库。

## 使用

- **我的研究**：首页优先显示资料缺口、公司风险状态及财报变化；关注卡片概览 PB、三年 ROE、最新财报期、扣非同比和每股经营现金流，异常时注明上次可用资料。仅 PB、日期或来源范围变化折叠展示，不增加“待处理”数量；仅数据日变化可直接在卡片上标记本次已阅，仍校验资料与记录版本。一键关注，无需填表；暂停保留笔记并退出批量更新。
- **指定公司**：输入六位代码，点击“获取公司资料”，只更新这家公司；完成后点击“查看更新结果”打开公司页，由本人决定是否关注。也可在公司页点击“更新这家公司”。
- **发现候选**：选择适用参照，扫描完成后点击“查看更新结果”，查看前三、排名原因和下一步。候选卡直接展示 ROE 趋势及系统未核查业务可比性的限制，不否定本人已做的核验；均值不能掩盖最新或历史年度 ROE 为负。默认选择可用参照，金融行业参照显示暂不适用；PB、逐年 ROE、来源和范围可展开核查。保留 peer-screen-v1 资格、公式、并列与前三规则。
- **主动更新**：首页可更新未暂停的 1—50 家关注公司；独立 worker 按提交时冻结的范围和日期取数。进度留在当前页面，完成后显示结果入口，由本人点击进入，保留当前输入。关闭页面不取消任务，重试同一次提交不重复执行。浏览、保存、已阅和打开公告不触发取数。
- **公司研究**：先看自己的判断、研究理由和下一步，再对照本次变化；记录支持证据、反对证据以及改变判断的条件。下一步为可选自由文本，提示按“触发／核查／反证”记录，不自动填入笔记或设置提醒。状态为继续研究、等待证据、暂不研究；与旧状态字段兼容。
- **财报与原文**：单公司和关注更新增加接口最新已披露报告的营收、归母净利润、扣非归母净利润同比、资产负债率、每股经营现金流；逐项保留报告期、公告日、来源与取得时间。同比与现金流为报告期累计口径，资产负债率不等于有息负债率。提供按代码检索公告的入口和已保存的 HTTPS 笔记链接。
- **已阅与笔记**：已阅确认界面所示事实并更新对照基准，不代表已核验公告原文或作出投资判断；已阅独立操作，保存不推进基准。缺口、损坏、估值日或财报日期倒退会阻止确认。多页面编辑通过版本检查防覆盖。

关注与单公司更新每次重新核查财务；同业扫描对缺 PB 或已知 ST 警示的公司可能跳过财务请求，不将新增财报字段加入排名。最新报告字段缺失或版本冲突时不回填旧值。失败明确显示本次状态，保留旧资料供参考；整批“部分完成”时，资料完整的公司仍可查看和确认已阅。历史快照无需补写新字段，页面明确提示可主动更新。

Web 内的系统提示只依据已取得的字段提出核查问题，不自动阅读公告，也不声称已验证业务可比性、审计意见、资产质量或合理估值。完整投资判断仍需本人核对业务、现金流量表与附注、审计意见、估值假设和反证。指标定义以 [TuShare 财务指标文档](https://tushare.pro/document/2?doc_id=79) 为依据，公告入口为 [巨潮资讯](https://www.cninfo.com.cn/) 的检索页，须自行确认证券身份与具体原文。

## 当前阶段与下一步

本轮实现研究入口与公司页重排、单公司更新及最新财报证据，并已于 2026-09-30 部署生产 Web/worker。切换沿用现有工作区和依赖，无数据库迁移或历史重算；Nginx 配置验证及本机 HTTPS 源站检查通过。自动化测试覆盖合成数据与移动尺寸浏览器；不能据此认定本人 OAuth、真实接口和手机使用已经验收。

随后代码审查修复了失败更新的财报展示、异常证据校验、历史 ROE 排序及完成任务自动跳页问题，并对齐文档、清理旧展示分支与重复测试；这些修复和清理已随提交 `ea93da9` 于 2026-09-30 上线。Web/worker 源码核对一致、运行正常，源站与公网 HTTPS 检查通过。

## 自动选股与机器研究（独立批处理）

新增 `market-research-v1`：沪深主板非金融全市场枚举，不依赖参照清单、不截取50家；同一估值日、同行业、相同三年年报窗口内按 PB 升序和 ROE 均值降序的平均名次安排研究。每组最多前三，行业轮转分配最多5家公司；这不是跨行业投资总榜或收益已验证策略。`peer-screen-v1` 和个人工作区不变。

```bash
# 凭据只从环境读取；不自动读取 .worker.env 或账户配置。
# 安装的 Hermes runtime 仅供无工具推理；需现有授权和 pdfinfo/pdftotext。
export AUTOMATION_HERMES_RUNTIME=/absolute/path/to/installed/hermes-agent
.venv/bin/python -m a_stock_tracker.automation \
  --root /absolute/private/automation-root \
  --request-budget 4000 --seconds 14400 --max-research 5
# 中断后沿用输出的 run-id；不改冻结证券范围/估值日，超过7天拒绝续跑。
.venv/bin/python -m a_stock_tracker.automation \
  --root /absolute/private/automation-root --resume RUN_ID
```

- 首次 root 必须为空且私有（0700），拒绝符号链接和已有其他数据目录；不读取/写入个人笔记、已阅、持仓、`set-analysis` 或业务数据库。不要把 root 指到 `data/research`。行情凭据 `TUSHARE_TOKEN` 和模型认证各由现有 owner 配置。
- 每轮冻结 `universe.json`；逐证券原子保存财务证据和清单，记录 qualified/excluded/gap/pending 并核对总数。完整行业才给候选；预算不足明确 partial，不把未处理当排除。候选队列每轮最多5家，续跑可补入新完成行业但不扩大名额。请求预算按一次调用计数，失败最多重试一次；模型预算每个冻结运行最多10次、每次240秒/6000输出token，进程总超时外加硬截止。费用未被订阅API报告时不写成免费。
- 巨潮公司映射/定期公告分页、官方PDF身份和报告期检查，冻结公告列表、PDF哈希及逐页文本；每份报告的模型证据包有字符上限。漏页拒绝该原文；短页/空白页不能自动证明是排版留白而非扫描缺失，保守记为 incomplete。未送入模型的页码明确列出，部分阅读为 incomplete，不冒充通读附注。范围仅定期报告，重大临时公告/诉讼/新闻未全面覆盖。
- 模型只返回固定JSON，**没有工具、账户、通知或个人状态写入能力**；逐字引文、证券、数字/单位、页码先程序校验，再另一次模型调用复核语义。未通过不发布确定结论；通过也仅是引用范围内的检查，不是审计保证、买入许可或 `a-stock-qa` 标签。
- `summary.md` 是批次入口，`report-代码.md` 展示入围线索、业务/财务判断、估值假设、最强反证、改变判断的证据和缺口；机器报告与我的研究完全隔离。报告先发布/校验，再原子提交候选状态与游标；相同证据/模型/模板不重复推理，更正使内容哈希变化。
- `--max-research 0` 只扫描并发布 `summary.md`，不请求公告或模型，候选保留待研究。CLI JSON 与 `invocation.json` 的 `report_counts` 区分报告状态，`failed_reports` 统计原文/模型错误及引用复核未通过；退出码 `0` 要求覆盖完成、无待研究且报告复核通过，`2` 表示部分完成或存在失败，运行异常为 `1`。复核通过不代表全文完整或投资可用，仍须检查报告缺口。
- `--sample 600519.SH,600900.SH,600019.SH` 仅用于显式授权的真实小样本验收，仍枚举全市场但不伪称全量财务扫描；离线测试不会自动请求真实接口或模型。
- **未启用周度定时或投递**。本次实现和一次性验收不等于切换生产。后续确认预算/时间/目的地后由原生定时器调用同一入口；无需新 Agent 平台。停止命令/定时器不影响现有 Web、worker、账户和历史记录。

本地交付的实际验收结果见交付说明；线上 Web/worker 仍以既有部署为准，不会因本次源码变更自动更新。部署、重启、定时安装、生产切换及归档仍需当前会话的明确授权。

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

- `config/anchors.json`：同业发现参照清单，区别于界面内的个人关注。保留原有代码，金融参照用 `discovery_unavailable` 标记停用原因；不执行或解析其他项目的 Python 文件。
- `config/trading_calendar.json`：仅提供日历采集的历史起始日，不作为在线研究日期的证据。
- `data/calendar/`：交易日历与原始来源证据。Web 只读；日历任务可写。默认研究日是上海今天之前、日历已证明的最近交易日。
- `data/research/`：私人 SQLite、不可变快照与工作区模式标记。只允许 Web/worker 访问，不放入静态资源或 Git。
- `.web.env`：从 `.web.env.example` 配置 GitHub OAuth 与数字用户 ID。
- `.worker.env`：从 `.worker.env.example` 配置 TuShare Token；Web 不接收该文件。

Web/worker 的 `STATE_DIR` 直接指定工作区目录；未设置时，生产默认使用 `TRACKER_DATA_DIR/research`，demo 默认使用 `.local/demo`。worker 的显式 `--state-dir` 优先。日历位于 `TRACKER_DATA_DIR/calendar`，参照清单和日历种子位于 `TRACKER_CONFIG_DIR`。管理命令使用显式 `--state-dir`（默认 `.local/demo`），备份/恢复使用 `--source` 与 `--destination`。Compose 固定容器内挂载路径，不跟随主机 shell 的这些变量。

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

日历是既有周期维护任务；自动研究定时尚未启用。主机时区为 `Asia/Shanghai` 时，周一至周五（含法定假日）16:00 的日历示例如下（先创建 `logs/`；按本机安装位置调整 Docker 绝对路径）：

```cron
0 16 * * 1-5 cd /home/lin/a-stock-tracker && /usr/bin/docker compose --profile maintenance run --rm calendar >> logs/calendar.log 2>&1
```

研究目标日始终早于上海今天，16:00 后也不会自动改用当天估值。日历覆盖之后仅缺周末时可沿用；缺少任何周一至周五日期的证据就拒绝新任务，节假日也需要日历证据。新日历应通过一次显式更新生成，不能把历史日历改日期后当作新证据。旧 tracker 数据库、财报观测、日志和 screen Git 历史都应先归档，再退役旧项目。数据迁移、替换生产、cron 修改及旧目录归档分别按切换授权执行。

- [模块与数据合同](docs/architecture.md)
- [开发约束](AGENTS.md)
- [peer-screen-v1 规则依据](docs/specs/2026-09-22-peer-screen-spec.md)

历史恢复入口与退役边界见 [架构文档](docs/architecture.md#历史与退役)。CHANGELOG 保留各时期事实，不作为当前运行指引。Framework A 仍为 `CLOSED_UNPROVEN`；运行数据与回滚副本独立保存在私有归档，不随源码清理。
