---
version: 1
slug: "a-stock-tracker-opportunity-view-py"
primary_target: "a_stock_tracker/opportunity_view.py"
related_targets: ["a_stock_tracker/app.py","a_stock_tracker/research_view.py"]
---

# 波段机会窗口

**Visitor mode: Operate.**

## Task and Context

用户已确认：本人、手机优先、收盘后快速核查；候选 → 解释与反证 → 价格条件与依据 → 返回或退出。产品范围和数据合同引用 README 与架构；本 brief 只维护这组页面的表达。

## Chosen Expression

用户选择“窗口索引”，与 DESIGN.md 的全局世界一致。来源/资料身份用矿蓝横带，日期和有效期分别标明；候选用完整名称、代码和细分隔线，不使用交易卡片或可操作信号。详情先读解释与最强反证，后读浅底价格条件与可展开依据。没有行情图像或图表槽位。

## Hierarchy

1. 壳层显示品牌、明确 demo 身份及账户入口。
2. 首页提供网页研究入口，列表显示任务、股票池边界与批次来源；公司行显示状态、日期、完整解释和反证，再提供详情动作。
3. 详情按解释/反证/等待事项 → 程序价格条件与有效期 → 形成依据排序；依据可展开，风险不折叠。
4. 登录、账户、空状态和读取失败沿用同一字体、颜色及动作规格，不显示未授权批次内容。

## States and Interaction

分别表达加载、未配置批次、零有效观察、未入选、资料或解释失败、过期、未知公司、读取失败。每个状态有明确原因或合法返回路径；读取失败允许重试，但不恢复旧工作台入口。过期仍可读历史解释，主区域及展开依据都隐藏可行动条件。demo 仅使用现有合成数据。

保留切页/退出/断线/撤权后迟到结果隔离；Web 不直接增加价格计算、采集、模型调用、交易操作或个人状态；新增网关只提交本人已确认的有界后台研究请求。48px 动作目标、语义标题和可读的展开名称支持键盘与触控。长公司名、长来源和多行反证必须换行，不横向溢出。

## Code-first Quality Bar

按真实 Flet 控件实现，不以概念图当完成页；颜色/字体角色用共用定义，真实状态和真实点击可测试。验证 360/390/430px 与桌面，检查日期、反证、按钮、展开依据以及退出后内容隔离；不允许隐藏到期价格的规则因布局变化失效。

## Direction contract

THESIS: 日期与观察窗口成为核查索引，拒绝把候选排成交易卡片或 KPI 仪表盘。

OWN-WORLD: 冷矿蓝整页底、深墨正文、矿蓝来源/日期横带、琥珀反证、浅底左标记价格区；细线分候选，无阴影。

STORY: 先确认资料身份和有效期，完整读解释、反证与等待事项，再核对程序条件及 AI 引用；没有资料不凑推荐。

FIRST VIEWPORT: 手机 20px 边距，顶部品牌与账户、30px 页面标题、来源横带、数量、22px 公司名与独立代码/日期行；解释与反证向下连续阅读，详情动作跟随内容，价格不抢先于反证。桌面限制 760px，不用卡片补满空白。

FORM: 有界日期索引，grounded 顺序第 3，seed `8db48fc6`；用户选择代码优先的“窗口索引”，不是 image-first comp。签名交互是可展开的核查依据与 AI 引用，到期只保留非行动依据。

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Direction Provenance

本轮用户显式选择“窗口索引”；概念 seed 为 `8db48fc6`。跨域挑战带入：价格同尺度、完整解释不截断、固定状态颜色。采用这些纪律而不带入字体滑杆、媒体滚屏或不存在的核查地图。没有锁定 image-first comp；最终质量以代码、实际浏览器和当前安全测试为准。

## Web research extension

用户已明确要求纠正终端与网页割裂：首页增加“开始选股与研究”，原生选择类型/输入代码，预算与模型报价横带，复选框明确确认后才提交。网页展示进度、缺口与覆盖，候选可预填量价或财报研究，最近请求可重开。入口与结果使用同一窗口索引世界；无 CLI 用户步骤、无旧工作台、无自动调度。受理未知核对同一编号；预算变化重新确认。独立接收器保存凭据，Web 无行情/模型凭据。保留所有 actor/generation/connection 与到期隐藏反例。
