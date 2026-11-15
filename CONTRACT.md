# 切片观测归档的升级契约

## 现有业务与材料
archive.py 的 Archive 管理观测批及其共享缓冲；cohort.py 的 Cohort 独立保存分析人员选择的有序成员，并计算整数汇总。samples.py 是冻结的完整旧库构造器，含全部建表语句、原始缓冲及已保存队列，无需外部数据。vendor/sdk1.py、vendor/sdk2.py 是本项目两代真实可运行的批处理依赖，随源码交付，不依赖下载。它们不是 Arrow 兼容实现。本轮新增 migration.py 并适配两个业务组件；不要修改 vendor/、samples.py 或既有 LegacyRegression 的断言。

## 连接及旧存储
使用 Python 标准库和 SQLite。所有对象接收同一个 sqlite3.Connection，创建方式见 samples.memory()。文件库也可用 sqlite3.connect(path) 后调用 samples.make(db)。连接启用外键；调用入口时无未提交的调用方事务。操作返回即已提交；读取不修改业务数据。维护窗口只有一个工作者，不要求在线复制、竞争处理或性能优化。

旧表定义完整保存在 samples.SCHEMA：meta 的 storage_version 为字符串 "1"；buffers1 保存以摘要为标识的 JSON 缓冲；batches1 保存 batch_id、buffer_id、start_row、row_count；cohorts1 保存队列名称；members1 按 name、pos 保存 batch_id、physical_row。pos 从 0 连续编号。batch_id 和队列名分别唯一、非空字符串。多个批可以引用同一个缓冲，并可重叠。

缓冲有五个数组：valid、station、list_valid 长度均为 N，offsets 长度 N+1，values 长度不限。valid 和 list_valid 的全部元素必须为布尔值。这些形状及有效位属于不可遮蔽的结构。批的 offset、length 是非布尔整数，0≤offset≤offset+length≤N；底层行坐标为 [offset,offset+length)。SDK1 的 check 仅校验这些结构，row 按下面的可达规则读取，不全面验证类型。

逻辑 Schema 固定为可空结构 {station: 不可为null的字符串（允许空串）, samples: 可空整数列表}，列表元素是非布尔整数，无数值范围限制。结构为空立即返回 None，不读取该行任何子值；否则 station 可达。samples 为空时不读取该行偏移和元素；否则要求 offsets[i]、offsets[i+1] 是非布尔整数且 0≤a≤b≤len(values)，返回 values[a:b]。切片以外、空父结构下、空列表下和未被可达列表覆盖的值不是逻辑观测，不能据此拒绝迁移。所有批内可达的 station、偏移和元素必须满足逻辑 Schema，即使没有队列选择该行也须核验；可达坏值抛 ValueError 并拒绝整次升级。SDK1 曾允许可达类型错误，samples.memory(bad=True) 保留了这种脏库；不能用旧接口曾返回该值作为放行依据。

## 保留的调用
Archive(db).append(batch_id, buffer, offset=0, length=None)：length 缺省取剩余长度，追加后不可修改，重复 batch_id 抛 ValueError。旧版按完整 JSON 摘要共享缓冲；升级后不要求继续物理去重。
Archive.bounds(batch_id) 返回原 append 的 (offset,length)。
Archive.at(batch_id,physical_row) 返回单行，Archive.read(batch_id) 返回该批切片内按原顺序排列的逻辑行列表。不存在的批抛 KeyError，非整数或切片外坐标抛 ValueError。
Cohort(db,archive).create(name,members)：members 为有序的 (batch_id,physical_row) 对；空队列合法，重复成员分别保留。名称重复或成员坐标非法抛 ValueError，批不存在抛 KeyError，失败不留下半队列。
Cohort.read(name) 返回选中逻辑行列表；summary(name) 返回 members、null_structs、null_lists、empty_lists、value_count、value_sum 六项整数，精确口径见现有实现。不存在队列抛 KeyError。

## SDK2 与本次升级
SDK2 的 validate、install、put、get、row 接口及完整实现见 vendor/sdk2.py。put/get 操作 batches2(batch_id,payload)，put 只追加且不自行提交。其物理批从第 0 行开始，五个数组必须通过全缓冲校验：station 全为字符串，values 全为非布尔整数；偏移从 0 单调到 values 长度。空结构的 station 为 ""、list_valid 为 false；空结构或空列表不占元素。空批形状是五个数组中只有 offsets 为 [0]，其余为空。SDK2 不接受切片参数，也不替调用方修复旧布局或保存队列。

实现 migration.upgrade(db,before_commit=None)，成功或已升级均返回整数 2。迁移所有批与队列，不仅迁移被队列引用的部分。每个目标批的行数等于原切片长度，目标行顺序与原切片一致；父空结构、空列表、空数组和含零值数组不得混淆。原四张业务表 buffers1、batches1、cohorts1、members1 及其全部记录原样保留审计；meta 仅将 storage_version 改为 "2"。
目标批必须实际写入 batches2 并通过未修改的 SDK2.validate；队列及原坐标绑定所需目标表可自行设计。迁移后成员身份、顺序和重复次数不变。
数据、队列绑定及版本标记共同提交。before_commit 若非 None，在目标状态全部暂存完成、提交以前调用一次无参回调；回调异常原样传播并回滚，成功提交后才返回。任意转换异常或该回调处进程终止，重开只能读到完整旧状态；不得留下目标半批、半队列或版本 2。重复升级不改变任何业务记录，已是版本 2 时不调用回调。
切换后原 Archive、Cohort 对象立即读取新存储，不能要求调用方重新构造对象。归档和队列的读取及追加只用 SDK2 和目标表，不再查询旧审计表或调用 SDK1，也不得禁用新版校验。迁移可读取原始旧缓冲，但不得调用 SDK1 充当转换器。
保持上述旧调用及坐标含义。升级后 Archive.append 仍接收旧缓冲和切片入参，对可达逻辑值执行相同严格核验；bounds、at 及新建队列仍使用这次 append 的原底层坐标。SDK2.put 直接追加的原生批也须经旧接口可见，其坐标从 0 开始。原生批和 Archive.append 共用 batch_id 唯一性。SDK2 原生写入由调用方提交。

## 可核对样例
samples.BUFFER 有 8 行，旧批 b 使用 offset=2、length=2，a 与 b 重叠且共享同一缓冲。review 选择 [(b,3),(b,2),(b,3),(a,1),(tail,4),(tail,5),(tail,6)]；完整输入和预期在 samples.MEMBERS、EXPECTED_ROWS、EXPECTED_SUMMARY。b 读出北站 [0,-2]、南站 [7]；review 依次读出南站、北站、南站、空结构、空数组、空列表、空结构，value_count=4、value_sum=12。
若目标压紧 b，则旧坐标 3 对应目标行 1。对 verify.py 中 batch 原生写入 native 后，队列 [(b,3),(native,1),(b,3),(native,0)] 应依次返回南站 [7]、None、南站 [7]、甲 [0,-2]；不得把原生行坐标再次按旧切片偏移转换。
unselected 批没有队列成员，仍须迁移和核验；zero 是合法空切片。bad=True 的可达 station=31 必须拒绝；正常样本中的越界负偏移、非字符串及非整数隐藏值不得导致拒绝。

## 交接与核验
保留现有旧回归，新增不调用迁移转换器的独立逻辑行对照，核对每批、每队列和六项汇总，并直接检查目标物理批。核验须实际从冻结旧库升级，覆盖共享切片、重复成员、隐藏坏值、可达坏值、空切片，验证回调异常回滚、文件库子进程在提交前终止后的重开重试、重复升级及 SDK2 原生追加和旧接口追加后的混合队列。冻结构造器以外的边界输入可自行组织。
交付简短布局、成员绑定及失败边界说明。.pointwise-runtime.json 是现有运行清单，version=1、kind=cli；setup 为准备命令的 argv 数组列表，verify/demo 各为一个 argv 数组，不使用 shell 字符串。当前 verify 执行正常业务回归及双代 SDK 检查；demo 输出真实旧批和队列结果。交付时更新它们，让 verify 同时核验真实升级与兼容续写，让 demo 展示升级前后及新旧成员混合分析结果。代码、数据和依赖均已随本基线提供。
