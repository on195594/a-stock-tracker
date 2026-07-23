# Historical outcome shadow Phase 2：生产加法迁移规范

日期：2026-07-24
状态：阶段 A 已批准；生产执行未授权
依赖：Phase 1 commit `c301792`

## 1. 目标

提供可审计、可回滚、fail-closed 的工具，把已冻结且复审通过的一个 outcome shadow run 原子导入生产 `tracker.db`。阶段 A 只实现、测试、在副本演练、复审并提交工具；阶段 B 必须再次获得用户明确授权后才可写生产。

## 2. 非目标

- 不修改 `predictions`、旧 outcome 或历史标签。
- 不切换 accuracy report、Sheets、cron 或任何读取口径。
- 不引入 QFQ、分红再投资或总回报版本。
- 不自动选择默认生产路径，不自动重试锁冲突，不 push。

## 3. 命令合同

`outcome-shadow-import` 必须显式提供：

- `--production-db`
- `--candidate-db`
- `--expected-run`
- `--expected-manifest`
- `--mode inspect|apply`
- apply 时必须额外提供 `--backup-db` 与 `--evidence-json`

`outcome-shadow-revert` 必须显式提供 production/run/manifest/mode；仅 apply 要求 evidence。inspect 不校验或创建 evidence 路径。只允许删除完全匹配且无额外 run 的本次 shadow 对象。

## 4. Candidate 合同

Candidate 以 SQLite `mode=ro` 打开，必须满足：

- `PRAGMA quick_check=ok`
- 三表、六触发器结构完整
- 仅存在 expected run/manifest
- results 数等于 run.event_count
- status 计数等于 computed_count/missing_count
- 每条 result/observation 的持久化 hash 可复算
- `source_impure=0`
- predictions protection hash 与 run 记录一致
- 文件读取前后 SHA-256 一致

任何失败均不得打开 production 写连接。

## 5. 生产状态机

- `absent`：无 shadow 对象，允许 apply。
- `already_present`：三表六触发器、唯一 run、manifest、计数和 hash 全一致；只读返回，不写。
- `partial_present`：shadow 对象部分存在；停止。
- `already_exists_diverged`：对象完整但签名、结构、run 或 hash 不一致；停止。

生产 apply 使用独立连接、短 busy timeout、`BEGIN IMMEDIATE`、no retry。顺序固定为：runs 表+两触发器 → observations 表+两触发器 → results 表+两触发器 → runs → observations → results → 事务内核验 → commit。失败 rollback。

数据库提交后若 evidence 原子落盘失败，必须抛出携带 `write_performed=true`、run、manifest、status 和目标路径的 `MigrationEvidenceError`；操作者随后用 inspect 复核真实状态，不得把该异常解释为数据库未提交。

## 6. 备份

apply 前必须创建 timestamped SQLite online backup，并验证：

- backup `quick_check=ok`
- user_version、page_size、encoding 与生产一致
- non-shadow sqlite schema 快照一致
- 所有 non-shadow 表计数与逻辑 hash 一致
- predictions protection hash 一致

源文件与 backup 的字节 SHA 只记录，不作为相等硬门禁；journal mode 记录但不要求相等。

## 7. 回滚

优先使用 drop-only additive revert。仅在唯一 expected run/manifest、三表六触发器和所有 shadow hash 完全匹配时允许。单事务按 results → observations → runs 删除表；回滚后验证 shadow 对象为零、non-shadow schema/table hash/sqlite_sequence 不变、quick_check=ok。

全库 backup restore 仅作灾难恢复；要求 writer 静默且确认迁移后不存在其他生产写入。生产后不为证明回滚而实际回滚，使用副本 `import → verify → revert → verify` 证据。

## 8. 阶段 A 验收

1. RED/GREEN 测试覆盖 inspect 只读、缺参、candidate 不匹配、partial/diverged/already_present、锁冲突、事务失败回滚、backup、drop-only revert、non-shadow 零漂移。
2. 在 `tracker.db` 副本和 `candidate-v4.db` 上完成 import/replay/revert 演练。
3. 生成 rehearsal、rollback simulation 与 manifest 证据。
4. 全量 pytest、Ruff、format、mypy、pip check、shell syntax、diff check 通过。
5. 独立只读复审无 P0/P1。
6. 迁移工具/spec/review 先提交；生产库 SHA 与 Phase 1 基线一致。

## 9. 阶段 B 执行门禁

阶段 A 完成后再次向用户报告 commit、review stamp、artifact dir 和建议执行窗口。只有用户明确批准生产导入，且执行前 writer/锁、时间窗、candidate、production、backup 全部复验通过，才允许 apply。

## 10. 停止条件

Candidate/生产合同漂移、partial/diverged、writer或锁、处于工作日16:00–18:15、backup验证失败、事务核验失败、生产保护hash变化或独立复审存在P0/P1时立即停止。