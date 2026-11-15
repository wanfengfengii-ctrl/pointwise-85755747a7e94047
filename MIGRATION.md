# 升级布局、成员绑定与失败边界

## 目标布局（v2）
- `batches2(batch_id, payload)`：SDK2 规范批，行从 0 压紧。空结构记 `station=""`、`list_valid=false`；空结构与空列表不占元素；空批仅 `offsets=[0]`。空结构 / 空列表（`samples=None`）/ 空数组（`[]`）/ 零值（`0`）物理可区分。
- `slices2(batch_id, start_row, row_count)`：迁移批及升级后 `Archive.append` 批的原切片坐标。SDK2 原生批无此行，坐标恒等 `(0, n)`。
- `cohorts2(name)`、`members2(name, pos, batch_id, physical_row)`：队列与成员绑定。
- `buffers1/batches1/cohorts1/members1` 全部记录原样保留，仅供审计，切换后不再读取；`meta` 仅 `storage_version` 改为 `"2"`。

## 成员绑定
`members2` 保存创建时给定的旧底层行坐标，不预转换。读取按批各自解析：归档批经 `slices2` 平移（`physical_row - start_row`）；原生批无 `slices2` 行，坐标从 0 恒等。新旧坐标不混用，成员身份、顺序、重复次数不变。`Archive`/`Cohort` 每次调用读取 `meta` 版本分发，升级提交后原对象立即走新存储，无需重建。

## 失败边界
- 转换（结构 + 可达值严格核验）在事务外纯计算完成；可达坏值抛 `ValueError` 时不写任何目标状态。隐藏坏值（切片外、空父结构下、空列表下、未覆盖元素）被清除而非拒绝。
- 全部目标写入（`batches2`/`slices2`/`cohorts2`/`members2`/`meta`）在单个 `BEGIN…COMMIT` 内；`before_commit` 在暂存完成、提交前调用一次，异常原样传播并回滚；进程在提交前终止由 SQLite 日志回滚，重开只见完整旧状态，无半批、半队列或版本 2。
- 重试幂等（版本短路 + `IF NOT EXISTS`）；已是版本 2 直接返回 2，不调用回调、不改任何业务记录。
