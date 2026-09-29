# 活动数据链与模块边界

```text
a_stock_tracker/
  config.py   固定 watchlist、DB 路径、物化开关
  paths.py    项目与交易日历路径
  data/       SQLite、Provider、采集、readiness、原子物化
scripts/      数据链启动与诊断
config/       日历种子、历史权重和实验 manifest
```

scripts 编排 data；业务模块不依赖启动器。生产源码使用包绝对导入，不注入 sys.path。运行结果进入被忽略的 data/logs/artifacts，凭据不得进入版本控制。

Screen 独立工作台通过显式只读连接消费旧库，当前合同和待办归其 `docs/FLET_DESIGN.md`；不导入本仓编排层，不修改本仓 schema、配置或定时任务。

Framework A 已结案。cli/scoring/signals/reporting/qualitative/pipeline.py 已退出活动树，源码在 Git 快照 `7468568fdbad8e3d8396babe0d83d9c1f15cebfc` 中恢复；不保留备用启动器或为退休功能维护测试。历史数据库、manifest、权重、报告及原始审计材料不随源码清理而删除。

SSE 运行态日历和按 hash 保存的来源证据仍由 16:00 数据任务原子刷新；失败恢复上一份文件，不预填未来日期。无人调用的旧 `data/l3_v2_qfq_cache.py` CSV/复权/限流实现也已归档；活动 QFQ 采集仍使用 `scripts/fetch_qfq_daily_bars_tushare.py`，不建立第二套缓存链。

`tests/test_project_structure.py` 约束顶层目录、活动模块集合、凭据隔离和导入方向，并禁止退休入口回流。新增领域须明确授权；验证命令只在根目录 AGENTS.md 维护。
