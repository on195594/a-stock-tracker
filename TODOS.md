# TODOS

当前阶段和顺序以 `docs/evolution-roadmap.md` 为准。

## 阶段一当前动作

1. 等待自动 QFQ 任务积累并复核 30/60/90 日 IC、spread、非重叠批次收益和回撤。
2. 比较固定 watchlist 等权组合与沪深 300 全收益基准，拆出股票池效应。
3. 补充跨月份稳定性和同股事件去重敏感性分析。
4. 判断 L3 v2 是有效风险门禁还是无实质选择性的规则；strong 候选长期无拒绝时直接收敛结论。
5. 根据阶段一证据决定 Framework A 继续、简化、重做或停止。

## 禁止

- 不恢复已删除的手工或 report-only 入口；
- 不新增 Framework C/D/E/F；
- 不调整权重或买入阈值；
- 不建设生产动态股票池；
- 不追加 shadow、seal、reviewer、authorization 或 migration；
- 不把旧未复权 outcome 当作最终策略证据。
