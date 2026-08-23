# LSF Array 提交与局部重跑

> 状态：讨论稿
>
> 目的：说明大量 Corner 和 Testcase 如何批量提交到 LSF，以及补充仿真或部分失败时如何只运行必要项目。

## 1. 为什么不能让每个 Testcase 单独执行一次 bsub

一次 Run 可能选择约 3000 个 Corner。假设每个 Corner 的第一轮算法产生 31 个 Testcase，实际需要进行：

```text
3000 × 31 = 93000 次仿真
```

如果对每个 Testcase 分别执行一次 `bsub`，短时间内创建大量独立 Job 会给 LSF 带来过大压力。

当前采用 LSF Array：

```text
Corner 1
└── bsub -J name[1-31] run.sh
    ├── Element 1：Testcase 1
    ├── Element 2：Testcase 2
    └── ...

Corner 2
└── bsub -J name[1-31] run.sh
    └── ...

...

Corner 3000
└── bsub -J name[1-31] run.sh
```

这样这次算法生成组大约执行 3000 次 `bsub`，每次提交一个包含 31 个 Element 的 Array Job，而不是执行 93000 次独立 `bsub`。

## 2. Array 的分组规则

当前已经确认的常规分组规则是：

```text
一个 Corner
+ 该 Corner 当前一轮尚未提交的 Testcase
= 一个 Batch
```

不同 Corner 不放入同一个 Batch。后续算法调用新生成的 Testcase 不追加到已经提交的旧 Batch，而是创建新 Batch。

如果某个 Corner 当前算法调用只提出一个 Testcase，使用普通 LSF Job；有多个 Testcase 时使用 Array Job。

## 3. 为什么 Testcase 和 Array Element 必须分开记录

Testcase 是算法提出的“需要验证的一组参数”。Array Element 是这个 Testcase 的某一次实际执行。

两者不能合并，因为：

- 一个 Testcase 可能失败后重试多次；
- 每次重试可能属于不同的 Array Job；
- 新增 Testcase 会进入后续的新 Array；
- 已经成功的 Testcase 不能因为同组其他 Element 失败而重新运行。

推荐记录关系：

```text
Testcase
├── Code / Mode
├── 所属 Corner、Stage 和生成它的算法调用
└── 执行尝试 #1
    ├── Array 提交记录
    ├── Array Element 编号
    ├── SP 和工作目录
    ├── LSF 状态
    └── 仿真结果

失败后：

Testcase
├── 执行尝试 #1：失败，属于 Array A 的 Element 7
└── 执行尝试 #2：运行中，属于 Array B 的 Element 1
```

## 4. Batch 表示一次 LSF 提交

本文中的 Batch 指一次实际的 `bsub` 提交：

```text
一个 Testcase：一次普通 Job 提交
多个 Testcase：一次 Array Job 提交
= 都各自记为一个 Batch
```

通常一个 Corner 的一轮 Testcase 会形成一个 Batch，但两者不是永远一对一：

- 后续算法调用新增的 Testcase 可以形成新的补充 Batch；
- 部分失败项可以形成新的重试 Batch；
- 如果以后需要限制单次 Array 大小，同一次算法生成的 Testcase 集合也可以拆成多个 Batch。

Batch 需要拥有自己固定的提交数据，包括：

- Batch 唯一标识；
- 所属 Run、Corner、Stage 和算法调用序号；
- 提交类型是普通 Job 还是 Array Job；
- 本次包含多少个 Testcase / Array Element；
- Array Job 中 `$LSB_JOBINDEX` 与 Testcase 执行尝试的对应关系；
- 每个 Element 使用的 SP 路径、运行路径和预期输出；
- 使用的运行脚本版本；
- LSF Job 名称和提交参数。

Batch 不拥有 Testcase 业务数据，但清单必须同时记录网络侧输入/结果路径、Attempt 唯一标识、Scratch 策略和回传策略。Case 创建后具有固定网络结果目录；计算节点为每个 Attempt 创建独占 Scratch 工作目录。

Batch 保存一次提交关系：本次 `bsub` 包含哪些 Case，以及普通 Job 或各 `$LSB_JOBINDEX` 分别对应哪个网络输入、Attempt 状态、Scratch 路径规则和回传目标。

## 5. 推荐每个 Batch 只使用一份任务清单

Array Job 中，`run.sh` 对每个 Element 执行相同命令，并通过 LSF 提供的 `$LSB_JOBINDEX` 判断自己应该运行什么。

这个方式是合适的。建议把“外部配置文件”进一步明确为每次 Array 提交专用、提交后不再修改的子任务清单。

推荐关系示意：

```text
Case 固定目录
├── Case 1 网络目录：SP、最终波形、日志、MT
├── Case 2 网络目录：SP、最终波形、日志、MT
└── Case 3 网络目录：SP、最终波形、日志、MT

提交清单
├── batch_001.tsv：引用 Case 1、2、3 的固定路径
└── batch_002_retry.tsv：需要时再次引用其中部分或全部 Case
```

其中：

- `batch_001.tsv` 表示第一次 Array 提交的任务清单；
- 清单第 1 条对应 `$LSB_JOBINDEX=1`；
- 每条记录包含 Testcase ID、执行编号、网络 Case 目录、唯一 SP 路径、Scratch 路径规则和 stage-out 目标；
- SP 在执行 `bsub` 前全部生成完成；
- `run.sh` 根据 Index 从清单中读取对应记录，再调用实际仿真程序；
- Array 提交成功后，清单不再修改；
- 重试或人工重新提交时新增清单，仍然引用原 Case 网络目录，但使用新的 Attempt ID 和 Scratch 路径。

单个 Testcase 使用普通 Job 时，Batch 清单只有一条记录，提交命令明确让 `run.sh` 使用这一条，不要求伪造 `$LSB_JOBINDEX`。清单仍然保留，便于恢复和追查这次提交实际运行了什么。

清单示意：

```text
index  testcase_id  attempt_id  case_path             sp_path
1      tc_001       attempt_1   .../case_001           .../case_001/testcase.sp
2      tc_002       attempt_1   .../case_002           .../case_002/testcase.sp
3      tc_003       attempt_1   .../case_003           .../case_003/testcase.sp
```

这只是说明内容，不是最终规定使用 TSV。也可以使用一个 JSON 数组或现有脚本方便读取的其他格式。

示意逻辑：

```text
LSF 启动 Array Element
        ↓
run.sh 读取 $LSB_JOBINDEX
        ↓
读取本 Batch 清单中的对应条目
        ↓
验证 Index、Testcase 和 SP 路径
        ↓
获得 SP、网络结果目录、Scratch 和运行参数
        ↓
创建 `/SCRATCH/<user>/flowpilot/<attempt_id>`
        ↓
取得原始 hostname，并在网络 Case 目录创建同名零字节标记
        ↓
只把本次仿真的单个 SP 复制到 Scratch
        ↓
在本地磁盘执行仿真
        ↓
把 Scratch 文件夹中的全部内容复制到 Case 网络磁盘的 Attempt 回传目录
```

相比让 `run.sh` 读取一份会被后续算法调用覆盖的公共配置，每个 Batch 一份固定清单更容易复查，也能防止旧 Array 因配置变化而运行错误的 Testcase。

清单使用 JSON、TSV 还是其他格式，可以根据计算节点已有环境决定。关键要求不是具体格式，而是：

- Index 到执行内容的对应关系固定；
- 提交后不能被覆盖；
- 人可以直接查看；
- Backend 重启后仍然能够恢复对应关系。

`run.sh` 不应通过 `eval` 执行清单中的任意字符串。更安全的方式是读取固定字段，再把 SP 路径和工作目录作为明确参数传给仿真命令。

`run.sh` 还需要把清单中的执行编号写入 Status。Backend 读取 Status 时同时核对 Testcase ID 和执行编号，避免把旧提交留下的状态误认为当前执行状态。

### 5.1 小文件数量控制

一次仿真本来就会产生 SP、日志、MT 和其他结果文件，这部分文件数量无法完全避免。但流程系统不应再为每个 Element 额外创建一份 JSON 配置或状态标记文件。

当前建议：

- 每个 Testcase 保留必须提前生成的 SP 和固定网络 Case 目录；
- 每个 Batch 只增加一份任务清单；
- Array Element 状态主要保存在 SQLite，不为每个状态创建 `.done` 等小文件；
- Batch 清单记录唯一 SP、Scratch 和 stage-out 路径，Runner 只把该 SP 复制到 Scratch；
- 每次 Attempt 使用独立 Scratch 目录；站点存在已知的 24 小时清理机制，但项目不管理或识别它；
- 只有 LSF 子任务自己负责取得实际 hostname，并在 Case 网络目录创建以该 hostname 命名的零字节恢复标记；
- stage-out 复制 Scratch 文件夹中的全部内容，不按扩展名筛选；
- 日志和仿真输出是否能够合并，需要结合仿真器和排错需求决定；
- 大量历史文件的保留和清理规则后续单独设计。

按照 3000 个 Corner、每个 Corner 31 个 Testcase 计算，第一轮仍然会有约 93000 个必须运行的 SP，但任务映射配置约为 3000 份 Batch 清单，而不是再增加 93000 份 Item 配置文件。

## 6. Array 提交前的准备顺序

推荐按以下顺序执行：

```text
确定本批需要运行的 Testcase
        ↓
为每个 Testcase 创建新的执行尝试
        ↓
创建 Array 提交记录和唯一标识
        ↓
确认所有 Case 的固定目录和 SP 已经准备完成
        ↓
为每个 Case 成功写入本次执行的 Submit Status
        ↓
生成并检查本 Batch 的任务清单
        ↓
将本批标记为“可以提交”
        ↓
执行 bsub -J "唯一名称[1-N]" 受控 Runner
        ↓
保存 LSF 返回的 Array Job ID
```

在清单、全部 SP 和必要目录准备完成之前，不能执行 `bsub`。否则某些 Element 可能启动后找不到自己的输入。

如果某个 Case 无法成功写入本次执行对应的 Submit Status，则该 Case 不进入本次 Batch，避免任务已经提交但目录仍显示旧状态。

## 7. Array 名称与提交中断恢复

每次 Array 提交都需要在执行 `bsub` 之前生成唯一标识，并将它保存到数据库和对应的提交清单中。

Job 名称可以包含这个标识的短格式，例如：

```text
flow_<run>_<corner>_<round>_<batch>[1-N]
```

具体名称需要考虑 LSF 对长度和字符的限制。重点是同一个 LSF 集群中不能与其他 Run 或历史提交混淆。

如果发生：

```text
bsub 已成功
→ Backend 在保存 Job ID 前退出
→ Backend 重启
```

Backend 应先根据提交前保存的唯一标识查询 LSF：

- 找到唯一匹配的 Array Job：补写 Job ID 并继续跟踪；
- 最新公共状态快照尚未包含该 Job：考虑约 5 分钟更新延迟，等待下一份新快照；
- 连续新快照仍未找到，但 Backend 关闭时间可能超过快照保留期：标记“状态待确认”，不能直接重复 `bsub`；
- 找到多个匹配或其他证据矛盾：停止自动提交并提示异常。

正常恢复通过 IT 提供的公共 `bjobs.parquet` 快照按唯一名称或其他字段匹配，不依赖频繁执行 `bjobs`。为了进一步缩小“LSF 已接受但 Job ID 尚未写入 SQLite”的窗口，可以让提交命令把原始回执同时写入 Batch 的提交回执文件；具体方式在实现前验证。

## 8. 跟踪 Array 中的每个 Element

不能只保存整个 Array 的总体状态。系统需要知道每个 Element 的状态，因为一个 Array 中可能同时存在：

```text
Element 1：成功
Element 2：运行中
Element 3：LSF 执行失败
Element 4：LSF 显示完成，但缺少输出文件
Element 5：仿真完成，但 Postprocess 失败
```

每个 Element 至少需要关联：

- LSF Array Job ID；
- `$LSB_JOBINDEX`；
- Testcase ID；
- Testcase 的执行次数；
- 当前 LSF 状态；
- SP 文件和工作目录；
- 标准输出和错误日志；
- 预期结果文件；
- 仿真业务是否成功；
- 后处理是否成功。

Array 的“完成数量”和“失败数量”可以由这些 Element 状态汇总得到，不应成为唯一记录。

## 9. Batch 完成不直接触发 Postprocess

Postprocess 不在每个 Element 完成时分别启动，也不以某一个 Array Batch 完成为充分条件。

Batch 只是执行分组。某次算法调用生成的一组 Testcase 可能包含第一次提交、失败重挂等多个 Batch。只有代码确认这组 Testcase 都已经结束，才触发一次批量 Postprocess。

```text
当前算法生成组仍有 Testcase 尚未提交、排队、运行、等待用户决定或正在重新仿真
        ↓
继续等待，不启动 Postprocess

当前算法生成组的所有 Testcase 都已结束
        ↓
使用当前 Stage 的约定目录创建一个批量 Postprocess 任务
        ↓
递归扫描目录中的波形
        ↓
Postprocess 自行跳过已经处理的内容
        ↓
处理尚未处理的波形并生成 MT
```

传给 Postprocess 的目录不要求只包含某个 Batch 的 Case。Workflow 不负责为 Batch 精确列出波形文件；Postprocess 脚本自己判断哪些波形需要处理，已经处理过的自动跳过。

因此重复触发目录扫描在业务上应当安全。Workflow 仍然记录每次 Postprocess 启动、退出状态和日志，以便发现脚本本身失败。

这样可以避免大量 Element 在相近时间完成时，同时启动数百个本地 Postprocess 进程，导致用户电脑 CPU、内存或文件系统压力过大。

多个 Corner 仍可能在同一时间满足条件，因此 Backend 还需要一个本地 Postprocess 等待队列，并限制同时运行的批量 Postprocess 数量。例如默认同时只运行一个或少量几个，具体数值以后根据脚本开销设置。

Postprocess 产生的 MT 和 Loss 仍然按 Case 保存：

```text
批量 Postprocess
├── Testcase 1 → loss_0/90/180/270
├── Testcase 2 → loss_0/90/180/270
└── ...
```

如果某个 Batch 中部分 Element 仿真失败，系统先等待用户决定。用户选择重新仿真后：

```text
失败 Testcase 组成新的重试 Array
        ↓
继续等待这组 Testcase 的重试结束
        ↓
当前算法生成组的全部 Testcase 达到结束条件
        ↓
扫描当前 Stage 目录并批量 Postprocess
        ↓
获得当前 Stage 的新增可用结果
        ↓
调用当前 Corner、当前 Stage 对应的算法
```

因此单个 Batch 的完成只会更新执行状态，不直接触发 Postprocess。

## 10. 补充仿真

算法要求补充 Code 时：

```text
算法返回新的 Testcase
        ↓
只创建这些新增 Testcase
        ↓
为新增 Testcase 创建第一次执行尝试
        ↓
按数量组成新的普通 Job 或 Array Job
        ↓
提交新 Batch
```

旧 Array 和旧 Testcase 不修改，也不重新提交。

## 11. 部分失败后的局部重跑

失败项不会自动重跑。用户在筛选页面选择“重新仿真”后，系统为选中的 Case 创建新的执行记录和新 Batch，而不是修改或重新提交原 Array。

例如：

```text
原 Array A：31 个 Element
├── 29 个成功
└── 2 个失败

用户确认重新仿真后的处理：
├── 29 个成功 Testcase 保持不变
├── 为 2 个失败 Testcase 分别创建新的执行尝试
└── 创建 Array B[1-2]，只运行这 2 个新尝试
```

这样做的优点是：

- 不会误跑已经成功的 Testcase；
- 不需要修改已经提交的 Array 配置；
- SQLite 中保留每次提交和执行尝试记录；
- 新 Batch 使用 Setting 中当前有效的 LSF 参数，并保存这份参数副本。

系统不设置自动局部重跑。人工筛选重新提交可以选择任何状态的 Case，包括失败、状态未知、运行中和已经完成的 Case。

每次重新仿真都使用新的 Batch，不依赖 LSF 对旧 Array Element 的原地重挂。

## 12. LSF 调度边界

Workflow 负责正确组织并提交 Job，不负责决定 LSF 内部如何排队和分配计算资源。

当前不把 Array 的 `%N` 并发限制设计成业务能力，也不在流程层自行模拟集群调度。每次提交使用 Setting 中当时有效的 Queue 和资源配置，真正的排队与并发由 LSF 负责。

## 13. 留到代码设计时确认的内容

- 清单采用什么格式，以及 `run.sh` 如何读取普通 Job 或指定 Array Index 的记录；
- 标准输出、错误输出、波形和 MT 的具体命名；
- Setting 中实际支持哪些 Queue 与资源字段。
