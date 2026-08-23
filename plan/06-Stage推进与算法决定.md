# Stage 推进与算法决定

> 状态：讨论稿
>
> 目的：说明每个 Corner 如何在一个 Stage 内反复少量尝试 Code，直到找到目标并进入算法指定的下一 Stage。本文不讨论具体算法公式。

## 1. 每个 Corner 独立推进

一个 Run 下的每个 Corner 都像独立的 Monte Carlo 采样点。

```text
Run（一组统一 PVT 条件）
├── Corner A：Stage 2，正在等待新 Testcase
├── Corner B：Stage 1，等待 Postprocess
├── Corner C：Stage 4，已经完成
└── Corner D：Stage 1，部分任务重挂
```

某个 Corner 是否继续补 Code 或进入下一 Stage，只由这个 Corner 的当前数据和当前 Stage 算法决定，不等待其他 Corner。

## 2. 不把“轮次”设计成独立业务对象

一次扫完所有可能的 Code 不现实。例如寻找拐点时，算法每次只提出少量 Code：

```text
算法调用 #1
→ 生成 Code 10、11、12
→ 仿真和 Postprocess
→ 没找到拐点

算法调用 #2
→ 生成 Code 13、14、15
→ 仿真和 Postprocess
→ 仍没找到拐点

算法调用 #3
→ 生成 Code 16、17
→ 仿真和 Postprocess
→ 找到拐点，进入下一 Stage
```

用户界面可以把这些过程显示为“第 1 轮、第 2 轮、第 3 轮”，方便理解。

代码和数据库不必建立一个独立的 Round 对象或 Round 状态机。只需要保存：

- 每次算法调用记录；
- 算法调用在当前 Stage 内的顺序号；
- 这次算法调用生成了哪些 Testcase；
- 算法当时读取了哪些结果；
- 算法最后决定继续补点还是进入其他 Stage。

因此 UI 中的轮次可以由算法调用顺序直接得到。

## 3. Stage 执行的文件目录

每次 Stage 执行有自己的专属文件夹。正常向前运行时，一个 Stage 通常只有一次执行；Rollback 后可能再次进入同名 Stage，所以目录需要带执行编号。

Stage 执行文件夹中包含多个 Testcase 子文件夹。每个 Testcase 子文件夹的名称由当前 Stage 的算法给出，子文件夹中至少包含一个 SP。

```text
stage_<stage_id>__<执行编号>/
├── <算法给出的 testcase 名称 1>/
│   ├── testcase.sp
│   ├── status.json
│   ├── 仿真器生成的波形和日志
│   └── Postprocess 生成的 MT
│
├── <算法给出的 testcase 名称 2>/
│   └── ...
│
└── <算法给出的 testcase 名称 N>/
    └── ...
```

Testcase 创建后，目录固定。首次提交、失败重挂、状态未知后的人工重提和已完成后的再次提交都使用同一个目录。

Batch 不管理这些目录。Batch 清单只引用 Testcase 固定路径，并建立 `$LSB_JOBINDEX` 到 Testcase 的对应关系。

## 4. 算法调用、Testcase 和 Batch 的关系

```text
Corner / 当前 Stage
│
├── 算法调用 #1
│   └── 生成 Testcase A、B、C
│       ├── Batch 1：首次提交 A、B、C
│       └── Batch 2：重挂其中失败的 B
│
├── 算法调用 #2
│   └── 生成 Testcase D、E、F
│       └── Batch 3：提交 D、E、F
│
└── 算法调用 #3
    └── 决定进入下一 Stage
```

Testcase 记录“由哪次算法调用生成”。一次算法调用生成的一组 Testcase 可能需要多个 Batch 才执行完。

Batch 只表示一次实际 `bsub`，不能用 Batch 完成代替“这次算法生成的 Testcase 已经全部处理完成”。

## 5. 什么时候执行 Postprocess

Backend 关注当前 Stage 最近一次算法调用生成的 Testcase 集合。

不能采用：

```text
某个 Batch 结束
→ 立即执行 Postprocess
```

应该采用：

```text
读取本次算法调用生成的全部 Testcase
        ↓
检查是否还有：
├── 尚未提交的 Testcase
├── PEND / RUNNING 的执行
├── 状态待确认或状态未知、尚未经过用户处理的执行
└── 用户已经决定重新仿真、但新执行还没有完成的 Testcase
        ↓
这组 Testcase 全部达到结束条件
        ↓
触发一次批量 Postprocess
```

“达到结束条件”不要求所有 Testcase 都成功，但失败项必须经过用户明确处理：

- 选择“重新仿真”：创建新的执行记录和新 Batch，继续等待新执行结束；
- 选择“忽略”：该 Testcase 不再阻塞本次算法调用，后续只使用剩余有效结果；
- 尚未选择：当前 Corner 停在这里等待用户，不执行 Postprocess，也不调用算法。

系统不自动重挂失败项，也不替用户自动忽略缺失结果。

被忽略的 Testcase 即使固定目录里还留有旧 MT，也不能被当作本次有效结果交给算法。

## 6. Postprocess 扫描 Stage 目录

满足条件后，Workflow 使用当前有效 Stage 执行的约定目录触发 Postprocess。

Postprocess 会递归扫描目录及子目录中的波形：

- 需要处理的波形：生成或更新 MT；
- 已经处理过的波形：自动跳过；
- 由之前算法调用生成的 Testcase：由 Postprocess 自己判断是否已经处理。

因此 Workflow 不需要为某次算法调用或某个 Batch 精确生成波形文件列表。

多个 Corner 同时满足条件时，Postprocess 进入本地等待队列，并受本地并发上限控制。

## 7. Postprocess 后再次调用算法

Postprocess 完成后，Backend 准备当前 Stage 的算法输入：

- 当前 Stage 已经验证过的所有 Code；
- 对应的 `loss_0`、`loss_90`、`loss_180`、`loss_270`；
- 前面 Stage 给出的 Code 结果，作为当前算法做决定时的参考；
- 当前 Stage 的算法配置；
- 当前 Corner 和 Run PVT 条件。

算法可以使用当前 Stage 到目前为止的全部有效结果，不只读取最新生成的一组 Testcase。

算法计算量较小，在输入准备好后立即执行，不进入 LSF 或独立计算队列。

## 8. 算法决定的统一形式

每个 Stage 使用自己的计算规则，但向 Workflow 返回统一形式的决定。

算法首先回答：

> 当前已有数据是否足够？

### 8.1 数据不足：继续补 Code

算法返回：

- `enough = false`；
- 新增的一个或多个 Testcase；
- 每个 Testcase 的文件夹名称；
- 每个 Testcase 所需的完整 Code / Mode 参数集合；
- 选择这些 Code 的原因。

Workflow 随后：

```text
保存本次算法调用和决定
        ↓
在当前 Stage 执行文件夹下创建新 Testcase 子文件夹
        ↓
生成 SP
        ↓
把新增 Testcase 组成新的 Array Batch
        ↓
提交 LSF
        ↓
等待这次新增的 Testcase 全部结束
```

这就是 UI 中看到的“进入下一轮”，但代码侧只是产生了下一条算法调用记录和一组新 Testcase。

### 8.2 数据足够：进入算法指定的 Stage

算法返回：

- `enough = true`；
- 当前 Stage 得到的 Code 结果；
- 这些 Code 的判断依据；
- 下一步进入哪个 Stage；
- 如果下一步是最终验证，则返回最终 Stage 及其要运行的完整 Code / Mode 参数。

Workflow 随后：

```text
保存本 Stage 得到的 Code 和算法决定
        ↓
保存算法使用的数据、Loss 和算法决定
        ↓
读取算法指定的下一 Stage
        ↓
创建该 Corner 的下一 Stage 记录和专属目录
        ↓
调用下一 Stage 对应的算法生成第一组 Testcase
```

下一 Stage 不要求是数字顺序上的 `当前 Stage + 1`。具体进入哪个 Stage，以算法输出为准。

最终运行也视为一个普通 Stage。它运行哪些 Code 仍然由算法决定，不需要 Workflow 在所有 Stage 结束后自行拼接一套“最终 Code”。

## 9. Stage 之间传递什么

当前可以确认至少传递：

- 前面 Stage 得到的 Code 结果；
- 当前 Run 的 PVT 条件；
- 当前 Corner；
- 下一 Stage 标识；
- 下一 Stage 算法需要的配置。

Workflow 不需要理解 Code 的电路意义，只负责保存、传递并根据算法输出生成文件。

每个新 Testcase SP 都从当前 Run Corner 引用的不可修改原始 SP 版本重新生成：

```text
当前 Run Corner 的原始 SP 版本
→ 应用本 Run 的 PVT 条件
→ 应用当前 Stage 算法决定扫描的 Code / Mode
→ 写入算法命名的 Testcase 固定目录
```

新 Stage 不使用上一 Stage 的 Testcase SP 作为模板。前面 Stage 得到的 Code 只是算法输入之一，当前算法可以继续修改这些值，并输出新 Testcase 所需的完整参数集合。

Workflow 不负责合并前面 Stage 的 Code，也不从旧 Testcase 猜测缺失值。文件生成部分只执行：

```text
原始 SP
+ Run PVT 条件
+ 算法输出的完整 Code / Mode 参数集合
→ 新 Testcase SP
```

算法输出缺少必要参数时，文件生成失败并记录算法输出错误。

## 10. Stage 完成条件

一个 Stage 不能因为某个 Batch 完成就标记为完成。

Stage 完成至少需要：

```text
当前一组 Testcase 的 Postprocess 已完成
+ 当前 Stage 算法已经再次执行
+ 算法返回 enough = true
+ 本 Stage 的 Code 结果已保存
+ 下一 Stage 或最终结束信息已保存
```

创建下一 Stage 必须允许重复检查，不能因为 Backend 重启或重复处理同一算法结果而创建多个相同的下一 Stage。

后端调试用 Rollback 可以一次选择多个 Corner。它不删除旧 Stage 执行目录，而是分别将各 Corner 旧决策后的路径标记为已被替代；算法重新进入某个 Stage 时创建新的 Stage 执行编号。目标决策点以前已经完成的 Testcase、MT 和 Loss 继续复用，不重新仿真。

## 11. Stage 清单由算法方案决定

Workflow 不预设所有算法都有相同数量、相同名称或相同顺序的 Stage。

每种算法方案在以后接入时，需要同时约定：

- 它有哪些 Stage；
- 从哪个 Stage 开始；
- 每个 Stage 使用哪段计算规则；
- 每个 Stage 需要哪些输入并返回什么；
- 哪些决定会进入其他 Stage；
- 哪个 Stage 属于最终运行。

因此用户选择算法方案后，也就同时选择了该算法对应的 Stage 流程。不同算法可以有不同的 Stage 数量。

已确认：算法保证 Testcase 文件夹名称在同一个 Stage 内唯一，不会重复提出同名 Testcase。
