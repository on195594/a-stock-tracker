---
name: a-stock-tracker · 窗口索引
description: 冷矿蓝、精确日期与成对解释反证的只读核查界面
colors:
  background: "#E6EFF0"
  surface: "#F6FAFA"
  band: "#DBE8EB"
  ink: "#203536"
  muted: "#486168"
  accent: "#006975"
  line: "#BCCDCF"
  caution: "#805500"
  caution-bg: "#FFF1D6"
  error: "#99362B"
typography:
  headline:
    fontFamily: NotoSansSC
    fontSize: 30px
    fontWeight: 700
  title:
    fontFamily: NotoSansSC
    fontSize: 22px
    fontWeight: 700
  section:
    fontFamily: NotoSansSC
    fontSize: 18px
    fontWeight: 700
  body:
    fontFamily: NotoSansSC
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: NotoSansSC
    fontSize: 14px
    fontWeight: 400
rounded:
  action: 6px
spacing:
  related: 8px
  group: 16px
  inset: 20px
  section: 24px
  footer: 32px
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.action}"
    padding: 12px 16px
  button-navigation:
    textColor: "{colors.accent}"
    rounded: "{rounded.action}"
    padding: 12px
---

# Design System: 窗口索引

## Overview

**Creative North Star: 窗口索引。** 用户已选择冷矿蓝色域、深墨文字、固定颜色角色与精确日期标记；代码优先，沿用 Flet 和本地中文字体。帮助核查，不制造交易紧迫感；没有图像或装饰行情图槽位。

本文件维护全局视觉系统，token 实现位于 `a_stock_tracker/opportunity_view.py` 的常量及 `window_theme()`。用户与场景见 [PRODUCT.md](PRODUCT.md)，产品目标与验收由 [README](README.md) 维护；页面阅读顺序和状态见 [surface brief](.impeccable/surfaces/a-stock-tracker-opportunity-view-py.md)。不另立产品路线。

## Colors

accent 用于主要动作与核查分组；background 是冷矿蓝全页底，surface 是条件和依据的局部浅底，band 标明来源与日期。ink 承担正文与标题，muted 只用于代码、日期、范围等辅助信息；line 分开候选和依据，不代替文字。

caution 配 caution-bg 表达反证、缺口和过期，始终附明确标签；error 用于登录失败等错误文字并保留合法恢复路径。禁止用红绿暗示涨跌、胜率或买卖，普通观察不使用成功徽章。前景与实际背景按普通文字至少 4.5:1 校验。

## Typography

仅用已有同源 `NotoSansSC`，无运行时字体 CDN。正文 16px、行高 1.55，辅助字段 13–14px，分组标题 16–18px；不能把风险或限制缩成小字。公司的完整名称与代码分行；标题不截断，解释与反证完整换行，不用密集全粗体。

金额、单位、来源和日期保留程序给出的原文；UI 不重新解析或计算价格。使用语义标题，辅助数据不伪装成标题。不要用只覆盖部分角色的 `TextTheme` 覆盖全局字体继承；字体响应成功不代表全部文字已正确渲染，须看真实截图。

## Layout

手机单列优先、左右留白 20px；桌面居中、外容器最大 760px。关联字段间距 8px，正文组 16px，独立区 24px，底部留白 32px。候选用细线和间距分开，不做 KPI 墙、重复卡片或为了撑满屏幕而扩大留白。

日期分别列明依据截至与有效至，长公司名和长来源自然换行。结构保留短列表的视觉重心，主流程不需要个人录入；触控目标与字号不因手机宽度变小而缩减。

## Elevation & Depth

平面色域替代阴影；按钮 elevation 为 0。来源、日期、反证及价格条件用有意义的色域或少量左侧标记强调，不给每一段都套盒子。普通正文不加纸纹、玻璃、渐变或装饰背景。

## Shapes

主要动作与文字导航的小圆角约 6px；阅读横带保持平直，左侧标记 3px。其余形状保留标准 Flet 控件的功能语义，不增加假图表或浮动交易控件。

## Components

`heading()`、`paragraph()`、`notice()` 与 `window_theme()` 为列表、详情、登录、账户和失败状态共用。动作高度至少 48px，禁用主要动作用 band 背景与 muted 文字，不与可启动动作同色；保留标准键盘与焦点反馈；按钮简短视觉文案可用语义标签补充完整公司身份，不让长名称挤坏动作。

只折叠核查依据、AI 事实引用和模型身份；依据保留选择复制，并使用完整只读文本语义，不让原生选择控件被读成空白禁用输入。解释、最强反证与有效期始终可见。到期或核验失败时主区域与展开内容都不展示可行动条件，保留历史解释和非行动依据。demo 明确显示合成身份，不另造一套演示页面。

研究入口用原生类型选择、代码输入、预算与模型报价、明确成本复选框和启动动作，禁止用命令示例代替工作流。受理未知时只核对原编号；进度/覆盖/失败、最近请求和结果重开在网页完成。候选上的研究动作预填代码，但不替用户勾选成本确认。未配置服务有明确管理员连接说明，无假完成或用户终端要求。

UI 保留认证、数据合同、actor/generation 检查；确认只授权本次后台有界请求，切页或退出后隐藏迟到回复但不自动取消已受理请求。无数据、零观察、失败与过期分别表达，并保留合法重试或返回路径。手机浏览器回归不是生产认证或真机验收。

## Motion

只保留控件必要反馈、展开与加载指示；不增加数字滚动、闪烁信号、倒计时或滚动入场。动画不延迟首屏、遮挡解释或暗示已触发条件。
