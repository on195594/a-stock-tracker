# 运维与有界批处理

本页维护管理员配置、生产部署、故障排查及高级 CLI 操作。产品与网页主流程见 [README](../README.md)，数据和安全合同见 [架构](architecture.md)，离线验证见 [离线验证](offline-validation.md)。所有真实请求、部署、启动和数据操作仍须相应的明确授权；命令示例不是授权。

### 管理员一次配置（不是用户操作）

独立宿主研究进程复用当前已安装的 Python 环境及已授权模型运行时。Web 仅挂载私有 **socket-only** 桥目录，不挂载研究产物或历史数据，也不获得行情/模型凭据。

- 根据 `.research.env.example` 创建 Git 忽略的 `.research.env`，选择全新私有 `RESEARCH_SERVICE_ROOT`、单独的 `RESEARCH_SERVICE_SOCKET` 目录、已有的授权行情/模型和明确 provider/model。共享令牌用 `secrets.token_urlsafe(32)` 生成，不用示例占位值。
- 模型运行时可选已安装的 Hermes 或 pi。pi 配置 `AUTOMATION_PI_RUNTIME`（安装包目录）、`AUTOMATION_PI_AGENT_DIR`（凭据目录）及 `AUTOMATION_PI_EXTENSION`（仅模型提供方入口），并将其默认 provider/model 明确写入 `RESEARCH_PROVIDER` / `RESEARCH_MODEL`；不会随 pi 默认设置变化而静默换模型。仅直接调用提供方推理，不启动编码会话、不加载技能、个人上下文或工具；每次本地最多等待 240 秒，向运行时请求 6000 输出 token；已安装 pi 的 openai-codex 提供方不会把该上限传给服务端，因此不能把它当作 token 或费用硬上限。pi 固定 SSE、关闭传输重试，无模型调用回退。优先使用固定 provider/model 对应的内置模型，不加载无关扩展；仅内置目录不存在该模型时才加载配置的提供方扩展，扩展若注册工具或事件回调则拒绝执行。pi 与 Hermes 的原始调用缓存分开核验。
- `RESEARCH_PIPELINE=legacy`（默认）保持旧写者；`evidence-first-v1` 启用证据选择、正文、独立复核及共享一次修复，正常 3 次、单份最多 5 次模型调用，批次仍最多 10 次。流程版本参与报价，切换后须重新确认；CLI 对应 `--pipeline`，续跑与冻结版本不符时拒绝。本地输入上限按 UTF-8 字节进入报价（选择 75 KB、正文/修复 18 KB、复核/再复核 90 KB），子进程用 `--context-contract` 在采集前核对，变化须重新确认；字节计数不是 token 或费用保证。此开关不是部署或收费授权，启用前须完成下述兼容读者部署和配对实验闸门。
- 新流程的 `RESEARCH_RESPONSE_CACHE=1`（默认）允许在接收器私有根目录 `validated-model-cache/` 复用解析、证据召回及已校验阶段响应。阶段响应须先通过机械校验，且对应事实/正文在最终语义复核通过后未被修复替换，才写入共享索引；请求、响应和用量账本仍保留在各请求目录，缓存命中记录为 `reused_validated_stage`，不冒充提供方调用。模型身份、完整输入和校验版本须完全相同，读取仍执行阶段校验。独立收费比较设为 `0`，每次使用新请求编号；CLI 不传 `--model-cache` 并为每个实验使用独立研究根目录。不得删除原编号账本换取再次收费机会。
- 明确授权后，在宿主启动一次 `.venv/bin/python -m a_stock_tracker.research_service`（仅启动接收器，不主动研究），启动前用 `set -a; source .research.env; set +a` 加载宿主环境，勿将此文件传给 Web。默认仅 loopback；配置 socket 时通过私有 Unix socket 通信。此源码不安装 cron、定时或进程管理器。
- 管理员密码登录（可选，见[架构](architecture.md#安全基线)）：在 `.web.env` 设置 `ADMIN_USERNAME`、`ADMIN_PASSWORD`（用 `secrets.token_urlsafe(24)` 生成）、`ADMIN_LOGIN_SCOPE`（`local` 默认，`any` 为公网开放）、`ADMIN_TRUSTED_PROXIES=nginx`（只采信 Nginx 容器转发的 `X-Real-IP`）与 `ADMIN_ALLOWED_IPS`（逗号分隔的地址或网段，通常为本机出口 IP，可用 `curl -4 -s https://stock.keyi.win/cdn-cgi/trace` 的 `ip=` 查看），重新部署 Web 后生效；清空 `ADMIN_PASSWORD` 并重新部署即关闭。`local` 下宿主本机浏览器正常经 Cloudflare 访问即可，白名单以外的来源看不到该表单。出口 IP 变化时须更新白名单。
- 在 `.web.env` 配置可选的研究网关桥（`RESEARCH_BRIDGE_DIR` 与共享令牌 `RESEARCH_SERVICE_TOKEN`）。生产 Web 显式使用 `docker-compose.research.yml` 覆盖文件。开关写在 `docker-compose.yml` 的 `RESEARCH_WEB_ENABLED: ${RESEARCH_WEB_ENABLED:-1}`（仓库默认启用），已授权部署时直接运行 `./deploy.sh`；单次可用 `RESEARCH_WEB_ENABLED=0 ./deploy.sh` 覆盖。脚本不启动宿主研究进程、不请求行情或模型。compose 中未写该开关时，现有容器已有桥而省略开关会在修改前中止并要求明确选择。
- 本机 Agent 接入（可选，见[架构](architecture.md#mcp-与-agent)）：另用 `secrets.token_urlsafe(32)` 生成一个与 Web 不同的令牌，写入宿主 `.research.env` 的 `RESEARCH_SERVICE_MCP_TOKEN`；按 `.research-mcp.env.example` 创建 Git 忽略、权限 `0600` 的 `.research-mcp.env`（同一 socket 与该令牌）。接收器重启后 `mcp` 渠道生效，未配置则该渠道关闭；撤销时删除该令牌并重启接收器。MCP 服务由本机客户端按需以 stdio 拉起，例如 `claude mcp add a-stock-tracker -e PYTHONPATH=$HOME/a-stock-tracker -- $HOME/a-stock-tracker/.venv/bin/python -m a_stock_tracker.mcp_server`（注册属本机配置变更，须授权）。`submit_run` 不得加入客户端自动放行列表；`mcp` 渠道每日最多受理 3 次（全部研究类型，含当日初筛复用，失败不退还，按上海日期计）。
- 服务一次只接受一个执行请求；无队列、无自动恢复、无自动收费重试。进程异常后记录未确认/中断，不能冒充成功；原始产物保留供核查。正常监护路径下，异常/正常退出均清理工作进程组后才释放执行锁；接收器与批处理主进程先后退出时，支持的直接模型/PDF进程继续持有执行租约直至退出。生产仍使用 systemd 的整个 cgroup 清理与资源约束，不依靠租约终止任意外部运行时的脱离后代。失败调用保留 `.usage.json`；`usage: null` 表示未知而非零费用，已有调用记录但无合格响应时禁止自动再次调用。基础设施配置/启动/部署不授权任何实际研究请求。

接收器的磁盘、单文件与子进程输出配额见下文[磁盘空间与配额监控](#磁盘空间与配额监控)（不会自动删除历史产物）。持久用户服务模板为 [config/a-stock-tracker-research.service](../config/a-stock-tracker-research.service)，适用于项目位于 `~/a-stock-tracker` 的宿主，包含 cgroup 资源隔离与有限异常重启（见[资源限制与故障恢复](#资源限制与故障恢复)）。下文列出部署流程、systemd 服务生命周期与排障。

## 运维与生产部署

生产环境由三层解耦架构构成：现有 Nginx 反向代理、Docker 容器化 Web 服务、以及宿主独立的 `systemd` 用户研究服务。三者通过严格的只读权限、凭据隔离与受限 Unix socket 通信。

### 架构组件与凭据隔离

| 组件 | 运行形态 | 网络与通信 | 凭据与配置文件 | 隔离边界与安全要求 |
|---|---|---|---|---|
| **Web 界面** (`web`) | Docker 容器 (`a-stock-tracker-web`) | 监听宿主 `127.0.0.1:8550`；只接受已验证的 Host，POST 校验 CSRF 与同源 | `.web.env`（GitHub OAuth、白名单用户 ID） | 服务端 HTML，无行情/模型凭据，不打开业务数据库；缺配置拒绝启动 |
| **研究网关桥**（可选） | Docker 挂载 (`docker-compose.research.yml`) | 仅挂载专用私有 Unix socket 目录（只读） | `.web.env`（socket 路径与共享令牌） | 仅转发经用户显式确认的研究请求，无法读取宿主模型/行情凭据 |
| **宿主研究服务** (`research_service`) | 宿主 systemd 用户单元 | 私有 Unix socket（目录权限 `0700`），仅监听本地 | `.research.env`（TuShare Token、模型运行时凭据、私有根目录） | 持有凭据执行有界子进程，绝不进入 Docker 镜像或挂载给 Web；单次执行互斥 |
| **本机 MCP 服务**（可选） | 本机客户端按需拉起的 stdio 进程 (`mcp_server`) | 经私有 Unix socket 调用接收器，不监听端口 | `.research-mcp.env`（socket 路径与独立的 MCP 令牌，`0600`） | 无行情/模型凭据，不读研究根目录；只读工具无法触达提交接口；接收器按令牌识别渠道并单独限额 |
| **反向代理** (`nginx`) | 现有 `nginx` 容器 | 暴露 80/443，反向代理至 `127.0.0.1:8550` | `docker/nginx-stock.conf`（HTTPS 证书、与应用一致的 CSP、早期数据写请求 425） | 阻断非法 Host 与外部直接访问容器端口 |

所有环境变量模板见 `.web.env.example`、`.research.env.example` 和 `.research-mcp.env.example`。敏感配置文件权限须设为 `0600`，宿主数据根目录设为 `0700`，禁止提交至 Git。

### 生产 Web 部署流程（deploy.sh）

部署需在当前会话**明确授权**。`deploy.sh` 具有线上副作用，严禁用作本地日常开发验证。

```bash
# 按 docker-compose.yml 中 RESEARCH_WEB_ENABLED 的默认值部署（仓库默认 1：启用研究网关，
# 需在 .web.env 配置桥接项与宿主接收器）
./deploy.sh

# 单次覆盖：明确停用已有 Web 研究桥（不停止宿主接收器、不删除研究数据）
RESEARCH_WEB_ENABLED=0 ./deploy.sh
```

开关只接受 `0` 或 `1`，空值及其他写法均拒绝。省略开关时，脚本检查现有 Web（含已停止容器）的研究环境项与桥挂载；发现已有桥则在构建、重建或修改 Nginx 前中止，要求显式选择 `1` 保留或 `0` 停用。Docker 状态读取失败也中止。

已发布价格参考（`/published`）随 Flet 退役：`PUBLICATION_WEB_ENABLED` 一经设置即在任何 Docker 命令前报错，重建后旧发布挂载不再保留；宿主上的发布目录原样保留、不删除。脚本按 `.web.env` 读取插值环境，导出的变量优先；不接受额外参数或任意 Compose 叠加文件。

**部署脚本自动执行的防护步骤：**
1. **配置预检**：检查 `.web.env`、`docker-compose.yml`、Nginx 源配置及目标站点；若启用桥接则核验 `docker-compose.research.yml`；
2. **语法与回归**：运行 `docker compose config --quiet` 验证编排语法，执行 `make check`，任一测试、类型或格式检查失败即停止；
3. **镜像构建与容器重构**：构建 `web` 镜像并执行 `up -d --no-deps --force-recreate --remove-orphans`（自动移除已退役的旧 worker 容器，不删除宿主卷或数据）；
4. **本地健康探针**：以生产 Host 轮询探测 `http://127.0.0.1:8550/`（最多 10 次，每次间隔 2 秒），失败则中断并保留日志；启用桥时，再从新 Web 容器以现有令牌和 socket 只读请求 `GET /info`，核对认证与桥响应；不打印令牌或响应、不启动研究或调用行情/模型。探针失败不会修改/重载 Nginx 或报告部署成功，但 Web 此时已重建，须修复宿主接收器/桥配置后显式启用重部署；该探针仅证明桥可达，不证明市场/模型凭据有效或真实研究可成功；
5. **Nginx 配置平滑切换**：自动备份原配置为 `$site.bak.<时间戳>.<PID>`，写入新配置并执行 `docker exec nginx nginx -t`。校验失败自动恢复备份；通过后执行 `docker exec nginx nginx -s reload`；
6. **本机 HTTPS 源站探针**：以 `--resolve stock.keyi.win:443:127.0.0.1` 探测源站（最多 5 次），仅此本机探针使用 `-k` 兼容 Cloudflare Origin CA 证书；这不是公网端到端验证，确认容器持续运行且重启计数为 0。

### 宿主研究服务运维（systemd 用户单元）

持久用户服务模板位于 [config/a-stock-tracker-research.service](../config/a-stock-tracker-research.service)，适用于项目位于 `~/a-stock-tracker` 的宿主。

#### 安装与常驻配置
```bash
# 1. 复制单元模板至用户 systemd 目录
mkdir -p ~/.config/systemd/user
cp config/a-stock-tracker-research.service ~/.config/systemd/user/

# 2. 启用用户 linger（确保用户登出或未登录时，后台服务仍持续运行）
loginctl enable-linger "$USER"

# 3. 重载并立即启动服务
systemctl --user daemon-reload
systemctl --user enable --now a-stock-tracker-research.service
```

#### 生命周期管理
```bash
# 查看服务运行状态
systemctl --user status a-stock-tracker-research.service

# 查看实时跟踪日志
journalctl --user -u a-stock-tracker-research.service -f

# 临时停止服务（注意：手动停止不会触发自动重启）
systemctl --user stop a-stock-tracker-research.service

# 重新启动服务
systemctl --user restart a-stock-tracker-research.service

# 彻底停用开机自启并停止
systemctl --user disable --now a-stock-tracker-research.service
```

#### 资源限制与故障恢复
- **cgroup 硬限制**：由单元文件设置 `MemoryMax=1G`、`CPUQuota=100%`、`TasksMax=64`，覆盖包含所有研究子进程；普通 Python 启动不会自动获得这些限制；
- **异常重启策略**：服务异常退出后等待 10 秒重启，5 分钟内最多重启 3 次（`StartLimitBurst=3`）。**重启仅恢复请求接收器监听**，不自动恢复、重试或新建研究批次；
- **进程组安全清理**：停止或异常退出时通过 `KillMode=control-group` 清理全部子进程组，超时 90 秒后硬清理（`TimeoutStopSec=90`），释放执行锁。

### 日常维护与健康巡检

#### 磁盘空间与配额监控
研究产物受内置有界预算控制，但系统**不会自动删除历史产物**，运维需定期巡检磁盘：
- **目录配额**：研究根目录预算 2 GB，新批次须保留至少 256 MB 可用空间；单批次目录超过 256 MB 自动停止（每两秒轮询检查，可能短暂超出）；
- **文件与输出限制**：单文件写入上限 32 MB，PDF 提取与模型子进程 stdout/stderr 合计最多读取 8 MB；
- **磁盘巡检命令**（`RESEARCH_SERVICE_ROOT` 取自宿主 `.research.env`，以下命令示例不读取该文件）：
  ```bash
  # 检查研究根目录与各请求占用
  du -sh "$RESEARCH_SERVICE_ROOT" "$RESEARCH_SERVICE_ROOT"/*
  # 检查宿主历史数据与缓存占用
  du -sh data/*
  ```

#### 服务状态探针与排障
- **Web 服务直接探针**：
  ```bash
  curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' \
    -H 'Host: stock.keyi.win' http://127.0.0.1:8550/login
  ```
- **Nginx 语法核对**：
  ```bash
  docker exec nginx nginx -t
  ```
- **Socket 桥接连通性排查**：
  若 Web 提示“未配置研究服务”，核对：
  1. 宿主接收器服务是否处于 active 状态：`systemctl --user status a-stock-tracker-research.service`；
  2. 桥接目录权限是否为 `0700`，且仅包含目标 socket 文件（无符号链接或杂乱文件）；
  3. `docker-compose.research.yml` 中挂载的 socket 路径与宿主 `.research.env` 中 `RESEARCH_SERVICE_SOCKET` 是否完全一致。

### 运维安全红线与历史隔离

遵循 [AGENTS.md](../AGENTS.md) 约束：
- **明确授权原则**：代码修改、命令示例与测试通过均不构成生产操作授权。提交、推送、部署、重启、定时/投递启停及历史数据归档/删除，均须当前会话明确授权；
- **数据不可动原则**：历史数据库（`tracker.db` / `tushare-primary.db`）、笔记、不可变事实 JSON、历史日志与备份原样保留在宿主目录，不挂载给 Web，禁止线上迁移、重算或删除；
- **凭据零泄露原则**：所有凭据与私钥不得进入 Git、Docker 镜像或静态资源包；
- **容器与计划任务**：部署脚本仅按需清理已退役的旧 worker 容器，不得删除宿主卷或宿主数据；源码退役不等于生产容器或系统 cron 已自动停用，已安装定时任务须另行核验与授权停用。

## 价格纪律参考批处理（维护与高级使用）

批处理与 Web 分离；打开页面不请求行情或模型。demo 使用明确标记的合成资料，包含较大距离、缺口和过期状态。真实运行须另行授权，并准备环境变量 `TUSHARE_TOKEN` 及 TuShare `daily/index_daily/stock_basic/trade_cal` 权限；不需要模型，不自动读取账户配置。首次 root 须全新、私有（0700），不要指向历史私人目录。

```bash
# 用最近7天内已完成的交易日替换 YYYY-MM-DD；当日批次须在上海时间18:00以后
# 示例不是执行授权
.venv/bin/python -m a_stock_tracker.opportunities \
  --root /private/price-reference --codes 600519.SH,600900.SH --as-of YYYY-MM-DD \
  --request-budget 20 --seconds 3000
```

每次最多 50 次行情请求、3600 秒、5 家公司；默认 20 次请求、3000 秒，不调用模型，无隐式重试或 resume。退出码 0 表示资料完整，2 表示至少一家公司有资料缺口，1 表示整次运行异常。单家公司不支持或缺资料记为 gap，不丢弃其他公司。

`price-reference-v2` 展示事实（`price-reference-v1` 历史产物按原样读取，不改写）：

- 61 个完整交易日日线，收盘价、MA20/MA60 及是否向上排列、近20日相对沪深300价格涨幅差。身份冲突、ST/退、缺日线、停牌零量仍拒绝。个股日线的 `pre_close` 与前一日收盘不一致时，视为交易所除权参考价：系数 `pre_close / 前收盘` 在 [0.3, 1) 且窗口内不超过 2 次时，事件日之前的价格按系数前复权到最新价格口径，并在事实中逐次披露日期与系数（调整后价位可能不是历史实际成交价）；系数 ≥1、低于 0.3、超过 2 次或沪深300基准不连续仍按来源缺口拒绝。
- 观察线为近20日最高价 + 0.01；失效线为近10日最低价；距离为 `(收盘价 - 失效线) / 收盘价 × 100%`，不因超过 8% 排除。距离不是最大损失承诺，T+1、跳空、停牌和涨跌停可能阻止退出。
- 有效期为后续5个交易日。到期隐藏观察线、失效线和距离，保留收盘、均线、相对强弱等历史事实；首页过期摘要不显示价格。不监控后续行情，不判断盘中状态。

冻结旧规则的公式与验证仅维护在 [离线验证](offline-validation.md)。网页与批处理新输出使用独立规则身份，不混为同一策略版本。

每个 `swing-*` 目录保存证据与结果（保留目录格式以兼容既有读取）。原子发布 `result.json` 后再切换带哈希的 `latest.json`，文件与目录同步失败不报告成功。接收器只读校验路径、哈希、规则与有效期（旧规则批次仍验证原解释/复核结果，不改写原文件）；Web 只校验本人会话，并在服务端与已打开页面按上海日期隐藏过期价位。生产 Web 不持行情/模型凭据、不将私人报告放入静态资源。

## 自动选股与机器研究（独立批处理）

网页门槛对应的 CLI 是 `--pb-mv-screen --max-research 0`，新批次与续跑均须带此标志；不带标志的命令保留原始全市场/样本规则。以下命令仅为示例，不构成真实请求授权。

不自动读取账户配置。需要环境中的 `TUSHARE_TOKEN`；生成报告另需现有模型授权、已安装并配置的 Hermes 或 pi 运行时，以及 `pdfinfo/pdftotext`。只扫描不调用模型。运行时配置见上文管理员配置，provider/model 必须明确且与冻结值一致。以下是真实请求示例，不是执行授权；运行前须确认累计请求预算、模型身份及费用边界。

```bash
# 以下以 Hermes 为例；已配置 pi 时使用对应 pi 环境
export AUTOMATION_HERMES_RUNTIME=/absolute/path/to/installed/hermes-agent
# 显式小样本验收；仍枚举全市场，财务覆盖永远 partial，累计最多研究 1 家
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --sample 600519.SH,600900.SH,600019.SH --request-budget 100 --seconds 600 --max-research 1

# 全市场只扫描：不请求公告或模型
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --request-budget 4000 --seconds 14400 --max-research 0

# 恢复同一冻结批次；显式重设本次预算，批次累计最多研究 1 家
# 不改范围、日期或模型，超过 7 天拒绝恢复；预算不足时保留检查点
.venv/bin/python -m a_stock_tracker.automation --root /private/market-research --resume RUN_ID --request-budget 100 --seconds 600 --max-research 1
```

- 首次 root 必须为空且私有（0700），拒绝符号链接及其他已有数据；不要指向 `data/research`。不读取/写入个人笔记、已阅、持仓或业务数据库。
- 逐证券记录 qualified/excluded/gap/pending 并核对总数。未启用前置门槛时，全市场模式只有完整行业组发布候选、不按总市值截取同业；启用后仅代表筛后池，同样要求该池行业资料完整。在相同估值日、行业、三年年报窗口内复用 PB/ROE 平均名次。每组最多前三，行业轮转，批次队列最多 5 家；不是跨行业投资总榜。抽样模式按输入顺序研究合格样本，不要求行业组完整，也不代表正式筛选结果。
- 默认请求预算 4000、时间 14400 秒、最多研究 5 家；支持范围分别为 3–10000、60–21600 秒、0–5 家。**请求/时间预算按每次调用重新计，恢复不代表批次总预算不变**；`--max-research` 是该批次累计报告数上限，包含已提交的失败报告，不是“本次再加 N 家”。恢复时可显式调整此上限；已有 1 家报告时设为 1 不再生成新报告。行情/公告请求失败最多重试一次。
- 默认模型为 `openai-codex / gpt-6-astra`，可显式传 `--provider` / `--model`，恢复必须与冻结值一致。每个冻结批次累计最多 10 次模型调用，每次本地等待 240 秒，并请求 6000 输出 token（pi openai-codex 不保证此硬上限）；生成与复核分别计数，失败或被终止的调用也计数。调用上限不是费用上限，未报告费用不能写成免费。
- 巨潮定期公告分页，冻结公告列表、PDF 哈希及逐页文本。报告年/期仍限前六页，证券代码须为原文连续完整数字。简称/代码后移时，仅允许第 7–16 页同页唯一的正式简称、代码、公司中文名称字段；简称/代码须与公告一致，公司名称完整值须在前六页同一页与对应年/期组成标题（同一行或相邻非空行），不是正文子串或其他公司名称的前缀/后缀。字段最多 64 个空白/冒号，中文名称整行最多 80 字符；冲突、前缀值、跨页拼接及全文宽松查找不接受。漏页拒绝原文，短页/空白页保守记 incomplete；证据包受字符预算限制，未送入模型的页码必须公开。重大临时公告、诉讼、新闻未全面覆盖，不能声称通读全部资料。
- `scripts/automation_model*.{py,mjs}` 只运行无工具推理、返回固定 JSON。证券、页码、数字/单位和逐字引文先程序校验，程序校验未通过时最多一次无工具定向修复，仍不合格的事实被剔除，再经独立复核；未通过不发布确定结论。原文/模型输出不具备命令或个人状态写入权限。
- `summary.md` 是批次入口，`report-代码.md` 展示判断、估值假设、反证、下一步及缺口；先发布/校验报告，再提交候选状态与游标。相同证据/模型/模板不重复推理。
- CLI JSON 与 `invocation.json` 的 `report_counts` / `failed_reports` 区分报告状态与复核失败。退出码 0 要求覆盖完成、无待研究且复核通过；2 表示部分完成或存在失败；运行异常为 1。只扫描也会因候选待研究返回 2。已提交的失败报告是终态，`--resume` 不重跑；修复原因后须显式新建批次。通过复核不代表全文完整、投资可用或获得 `a-stock-qa` 标签。
- 代码交付不启用周度定时或投递。一次性验收不等于生产切换；未来确认预算、时间和目的地后，原生定时器调用同一入口即可。

## 升级与回滚要求

验收门槛见 [README](../README.md#回归与发布门槛)，历史发布记录见 [CHANGELOG](../CHANGELOG.md)。不运行 `deploy.sh` 做开发验证。

### 接收器升级与回滚

接收器负责研究执行、报价、渠道限额和结果读取；升级时须保留提交协议与历史数据兼容性。宿主接收器以 `~/a-stock-tracker` 为工作目录并从中启动批处理：开发与 `uv sync` 只在独立 git worktree 中进行。每次升级单独授权：

1. 先在合成接收器上验证新接收器与现网 Web 兼容：报价、提交、状态与结果读取不受影响。
2. 只在 `get_service_info` 显示无任务执行时更新宿主代码并重启接收器；批处理子进程从宿主工作区启动，任务运行中不改动工作区。
3. 记录接收器当前提交作为回滚点；启用 MCP 时 `.research-mcp.env` 为 0600，并已被 Git 与 Docker 构建排除。
4. 新流水线先部署可读旧格式和 `cited-machine-research-v4` 的兼容读者，保持 `RESEARCH_PIPELINE=legacy`；核验 Web/MCP 的分片引文和旧报告读取后，记录该提交与镜像为回滚下限，再按独立授权启用新写者。产生新格式报告后不得退回不认识 v4 的读者。
5. 回滚等待接收器空闲；如另获授权紧急中断，保留新版本非终态中间产物，只读展示停止/中断状态，不交给旧写者续跑。不改写、不删除研究目录，不自动重跑任务。

### Web 切换前

- 候选只在 loopback、临时目录与合成接收器上验证，不连接生产桥，不同时开放两个可提交入口。
- 镜像按锁文件安装运行依赖，不安装开发依赖；构建后核对 FastAPI、模板与 OAuth 所需模块可导入。同步宿主环境属于接收器变更，按上一节执行。
- Nginx（`docker/nginx-stock.conf`）：`/oauth_callback` 路径不变；CSP 与应用一致（无 `unsafe-inline`、`unsafe-eval`、`ws:`）；`Referrer-Policy: same-origin`（保留同源 Referer，作为来源核查的回退依据）；`$ssl_early_data` 为 `1` 的非 GET/HEAD 请求返回 425，并向上游传递 `Early-Data`；`/api/session` 位置已删除。共享 `nginxconfig.io/proxy.conf` 须先核对其他站点依赖，不随本站升级清理。
- 记录现网镜像标识作为回滚点；回滚资料不进 Git、镜像或公开资源。

### 授权切换

1. 交付差异、离线验收结果与未验证项，取得本次部署的明确授权。
2. 只替换 Web 容器与对应 Nginx 配置（`./deploy.sh`，研究网关开关取自 `docker-compose.yml`；不再设置已退役的 `PUBLICATION_WEB_ENABLED`）；接收器不随之重启（除非另行授权），已受理任务继续执行。宿主上的已发布价格参考目录原样保留。
3. 旧会话全部失效，本人重新登录；按原编号确认旧任务与报告可读。不为验收发起新的收费任务。
4. 验证 OAuth、退出与 `no-store` 之后再开放提交。

### 回滚

出现权限泄漏、幂等退化、旧任务不可读或主流程失败时，停止提交并提出回滚（须授权）：恢复上一镜像与代理配置，不改写研究目录，不重跑任务。Web 不新增持久状态，回滚不涉及数据迁移。稳定后清理旧镜像、容器须另行批准；Flet 专属代码与资源已退役，回滚只恢复上一镜像，不恢复源码。

### 离线性能对照

`make test-mobile` 在 10 Mbps 下行 / 1 Mbps 上行 / 80 ms 延迟、禁用缓存的新浏览器上下文中，测量已认证首页可操作的耗时与传输字节，超过 2 秒即失败；外部域名请求一律拦截并计为失败。这是合成冒烟，不代表线上或真机指标；真实网络、并发负载和生产反向代理仍需另行验收。
