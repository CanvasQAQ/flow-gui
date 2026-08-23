# Stage 0 和 Stage 1 详细流程

> 状态：讨论稿
>
> 本文从用户视角描述项目如何基于已有仿真目录完成初始化，并通过多次少量补 Code 搜索最佳结果。算法内部暂时留空，只定义算法与流程之间如何交换数据。

## 1. 当前项目的实际启动位置

当前项目不是从一个 SP 文件开始生成所有 Corner。

用户提供的是一个已经准备好的基础文件夹。文件夹中大约包含 300 到 3000 个不同 Corner 的 SP 文件，以及已有仿真产生的对应 MT 文件。

文件形式暂记为：

```text
*.sp$corner
*.mt$corner
```

这些文件已经包含第一轮搜索所需的初始 Code 信息和对应的已有仿真结果。

项目启动时应直接使用这些文件，不重复执行“从一个 SP 文件展开出大量 Corner”的工作。

## 2. Stage 0 的定位

Stage 0 是一项补充能力：

```text
一个基础 SP
      ↓
按照既有规则展开
      ↓
大量不同 Corner 的初始 SP 和结果文件
```

这部分目前已经有成熟流程完成，因此暂时不在当前项目的实现范围内。

当前项目只需要为以后接入 Stage 0 保留合理边界，不能让 Stage 1 强依赖某个只能由人工准备的特殊目录结构。

当前实际入口为：

```text
用户选择已经展开完成的基础文件夹
                    ↓
项目读取其中的 Corner SP 和已有 MT 结果
                    ↓
完成初始化
                    ↓
进入 Stage 1
```

## 3. 初始化时需要读取什么

用户选择基础文件夹后，Backend 需要搜集以下信息。

### 3.1 Corner 清单

扫描基础文件夹中已有的 Corner SP 文件，得到：

- Corner 总数；
- Corner 名称或唯一标识；
- 每个 Corner 对应的 SP 文件路径；
- 每个 Corner 对应的 MT 文件路径。

初始化完成后，界面需要能够让用户看到可选择的 Corner。

### 3.2 初始 Code

初始 Code 不由用户手工填写。

Backend 通过 MT（Measure Data）解析器读取每个 `*.mt$corner` 文件，从中获得第一轮搜索的初始 Code。

目前涉及三类 Code：

```text
rangesel
vrefsel
legsel
```

它们的逻辑结构是：

```text
rangesel：1 个值

vrefsel：四个 Phase 各自拥有一个值
├── Phase 0
├── Phase 90
├── Phase 180
└── Phase 270

legsel：四个 Phase 各自拥有一个值
├── Phase 0
├── Phase 90
├── Phase 180
└── Phase 270
```

如果按上述结构计算，一个 Corner 的一组完整 Code 暂时可理解为：

```text
1 个 rangesel
+ 4 个 vrefsel
+ 4 个 legsel
= 9 个值
```

这里的“一个 rangesel”只是在说明一组 Code 的数据结构，并不表示流程系统需要执行额外的共享约束。

对流程系统来说，一组 Code 就是 9 个可以分别取不同数值的变量。每个变量的业务意义、取值关系和选择方法都由算法负责，流程系统不需要理解。

### 3.3 初始化结果

初始化完成后，系统至少应该形成一份可保存和复查的清单：

```text
基础文件夹
├── Corner 总数
├── Corner 1
│   ├── SP 文件
│   ├── MT 文件
│   └── 初始 Code
├── Corner 2
│   └── ...
└── 解析过程中发现的问题
```

如果某个 Corner 缺少对应 MT、MT 无法解析或初始 Code 不完整，不让整个初始化失败。系统标记这个 Corner 的具体问题，并将它设为不可选择；其他有效 Corner 继续正常使用。

用户修复基础目录中的文件后，可以通过主动刷新扫描重新解析这些异常 Corner。解析成功后，Corner 可以重新启用。

初始化结果需要保存并允许多个 Run 重复使用。正常情况下，用户第一次选择基础文件夹时扫描一次，之后创建 Run 不自动重复扫描。

界面同时保留“重新扫描”的主动操作。用户执行时需要选择“追加”或“覆盖”。重新扫描用于：

- 发现后来新增的 Corner 文件；
- 重新解析以前有问题的 Corner；
- 在覆盖模式下重新解析全部 Corner。

追加扫描加入新 Corner，并重新解析所有以前有问题的 Corner；已经正常的 Corner 保持不变。覆盖扫描重新解析当前目录中的全部 Corner；以前存在但本次未找到的 Corner 标记为文件缺失，不从历史中删除。

扫描结果不会自动改变已经在 Run 中运行的 Corner。Run 可以继续补充新 Corner，但每个 Corner 加入 Run 时固定自己使用的原始 SP、初始 Code 和输入版本。完整规则见《重新扫描、补充 Corner 与 Rollback》。

## 4. 用户开始一次搜索前需要配置什么

初始化完成后，用户需要配置本次测试集的新 PVT 条件。

目前已经明确的条件是：

```text
VDD
VCM
```

用户还需要：

- 从初始化得到的 Corner 清单中选择本次要运行的 Corner；
- 选择本次 Stage 1 使用的算法方案；
- 确认并启动流程。

因此，一次 Stage 1 搜索开始前的输入暂时可表示为：

```text
已初始化的基础文件夹
+ 选择的 Corner
+ PVT 条件（当前是 VDD / VCM）
+ 选择的算法方案
```

一次 Run 只指定一组 PVT 条件。当前 PVT 条件由一组 VDD / VCM 构成。

Corner 可以通过 `1,2,3-5,6-20` 这类编号列表选择，也可以先选择全部再反向排除一组编号。初始化时被标记为异常的 Corner 不参与 Run，修复文件并刷新扫描成功后才能重新选择。

Run 创建后仍然可以再次使用同样的筛选方式补充 Corner。系统只加入该 Run 中尚不存在且当前可用的 Corner；新增 Corner 使用该 Run 既有的 PVT 条件和算法方案，从第一个 Stage 独立开始，不影响已经在运行的其他 Corner。

同一个 Run 下管理的所有 Corner 都使用这组 PVT 条件。如果用户需要另一组 VDD / VCM，应从相同的初始化数据创建另一个 Run。

因此初始化数据和 Run 是两个不同层级：

```text
初始化数据
├── Corner 清单
├── 每个 Corner 的基础 SP / MT
└── 从 MT 解析出的初始 Code 和参考 Loss

Run A
├── PVT 条件 A
├── 选择的 Corner
└── 选择的算法

Run B
├── PVT 条件 B
├── 选择的 Corner
└── 选择的算法
```

## 5. 算法与流程之间的分工

算法应当可以切换。同一批初始数据可以使用不同算法运行，以便比较两种算法最后找到的 Code 和结果。

算法计算量不大，主要执行类似下面的判断：

- 比较最大或最小 Eye Loss；
- 找到对应的 Code；
- 根据当前结果挑选下一批 Code；
- 判断当前 Stage 是否已经完成。

算法应在一个 Corner 当前一组 Testcase 的结果准备好后立即计算，不需要进入单独的排队系统。

各 Corner 的数据和决定完全独立。流程系统必须分别为每个 Corner 调用算法并应用返回结果，不能因为其他 Corner 尚未完成而等待。

不同 Stage 的算法计算规则不同。用户选择一种算法方案时，也就选择了该方案定义的 Stage 流程。不同算法方案可以拥有不同数量的 Stage；具体 Stage 清单和计算约定留到编写相应算法时确定。

### 算法负责

算法接收：

- 初始 Code；
- 之前各轮已经验证过的 Code；
- 这些 Code 对应的四个 Phase Loss 结果；
- 算法自身配置。

初始化 MT 中已有的 Loss 是原始 PVT 条件下的参考值。用户创建 Run 后会设置新的 PVT 条件，当前具体字段是 VDD / VCM，目的是观察并搜索 VT drift 下的最佳 Code。

因此第一次算法调用可以获得初始 Code，但没有当前 Run PVT 条件下的新 Loss。初始化目录中的参考 Loss 与新 PVT 条件不一致，不能当作当前执行流程的验证结果。

算法输出：

- 当前这次调用需要验证的一个或多个 Testcase，每个 Testcase 包含完整 Code / Mode 参数集合；或者
- 数据是否足够、当前 Stage 得到的 Code 结果，以及下一步进入哪个 Stage；
- 最终 Stage 要运行的完整 Code / Mode 参数集合。

算法内部如何选择 Code、如何比较 Loss、何时停止，暂时不在流程设计中规定。

### 流程系统负责

流程系统负责：

- 准备算法需要的输入数据；
- 调用用户选择的算法；
- 保存算法输入、输出和判断原因；
- 把算法输出的 Testcase 变成实际 SP 文件；
- 提交 LSF 仿真；
- 等待和记录仿真状态；
- 执行后处理；
- 把 Loss 结果重新交给算法；
- 根据算法输出继续下一轮或结束当前搜索。

算法不直接修改文件、执行 `bsub`、读写数据库或操作界面。

## 6. Testcase 表示什么

Testcase 是算法要求实际验证的一组仿真配置。

一个 Testcase 主要通过修改 SP 文件中的参数产生，例如：

```text
.param xxx=xxx
```

修改内容通常包括：

- `rangesel`；
- 四个 Phase 各自的 `vrefsel`；
- 四个 Phase 各自的 `legsel`；
- 必要的 Mode 配置；
- 本次 Run 使用的 PVT 条件。

算法一次可以提出一个 Testcase，但更常见的是同时提出多个 Testcase。

一个 Testcase 表示“一套准备实际运行并获得四个 Phase Loss 的完整参数”，但它不与一个 LSF Job 单独对应。

例如选中 3000 个 Corner，并且每个 Corner 的第一轮算法都提出 31 个 Code 组合时，不会直接提交 93000 个彼此独立的普通 Job。常见做法是：

```text
Corner 1：一个 Array Job，包含 31 个 Testcase
Corner 2：一个 Array Job，包含 31 个 Testcase
...
Corner 3000：一个 Array Job，包含 31 个 Testcase
```

也就是提交大约 3000 个 Array Job，每个 Array 中包含 31 个需要实际运行的 Code 组合。

因此必须把“算法要验证的 Testcase”和“为了执行而创建的 LSF Array Job”分开记录。

## 7. Stage 1 每一轮的固定流程

Stage 1 由一轮或多轮重复步骤组成。

```text
准备本次算法输入
        ↓
算法提出一个或多个 Testcase
        ↓
为每个 Testcase 创建新的 SP 文件
        ↓
把仿真任务提交到 LSF
        ↓
等待 LSF 仿真结束
        ↓
执行 Postprocess
        ↓
获得四个 Phase 的 Loss
        ↓
把当前 Stage 已有 Code 和 Loss 交给算法
        ↓
算法决定：补充 Code 或确认最佳 Code
```

### 7.1 准备算法输入

第一轮至少向算法提供当前 Corner 的初始 Code。

后续算法调用还需要提供：

- 已经验证过的 Testcase；
- 每个 Testcase 的 Code；
- 每个 Testcase 的四个 Loss；
- 已经由用户选择“忽略”的失败或缺失结果 Testcase 信息。

### 7.2 产生本次 Testcase

算法输出本次需要验证的一个或多个 Testcase。每个 Testcase 记录生成它的算法调用 ID 和调用顺序号，UI 可以据此显示“第几轮”。

系统保存这次算法调用的完整输入和输出，然后为每个 Testcase 建立独立记录。重复执行流程检查时，不能因为再次调用或再次应用同一输出而重复创建 Testcase。

### 7.3 生成 SP 文件

对于本次生成的每个 Testcase，系统始终从该 Run Corner 加入时引用的不可修改原始 SP 版本重新开始，按顺序应用：

- 本次 Run 的 PVT 条件；
- Testcase 指定的 Code；
- Testcase 指定的 Mode；
- 其他以后确认的参数修改。

新 Testcase 不继承上一个 Testcase 或上一 Stage 已经修改过的 SP。前面 Stage 确定的 Code 会作为算法输入，由当前 Stage 算法输出本 Testcase 所需的完整 Code / Mode 参数集合。

Workflow 不负责推断、继承或合并 Code。它只负责：

1. 读取对应 Corner 的原始 SP；
2. 应用本 Run 的 PVT 条件；
3. 按算法给出的完整参数集合执行替换；
4. 将结果写入 Testcase 固定目录。

如果算法缺少该 Stage 规定的必要参数，Workflow 应在生成 SP 前报错，不能使用上一 Testcase 或原始 SP 中的旧值偷偷补齐。

生成新的 SP 文件时不能修改用户提供的原始文件。

本次算法生成的全部 Testcase SP 在提交 LSF Batch 前生成完成。每个 Case 创建后拥有固定网络结果目录；每次 Attempt 在计算节点本地建立独立 Scratch 工作目录，仿真完成后再安全回传波形和日志。后续重挂或人工重新提交继续对应同一个逻辑 Case，但不会复用另一次 Attempt 的 Scratch 目录。

重新提交时不主动删除旧波形和 MT。仿真器覆盖波形，Postprocess 覆盖 MT。系统不能因为目录中仍存在旧 MT 就提前把新执行判断为已有结果。

### 7.4 提交 LSF

SP 文件准备完成后，系统将需要执行的 Testcase 组合成 LSF Array Job，再通过 `bsub` 提交。

当前已知的常见组合方式是：同一个 Corner、同一次算法调用产生的多个 Testcase 组成一个 Array Job。每个 Array Element 执行一个 Code 组合。

系统至少需要记录下面的关系：

```text
Testcase
└── 本次执行尝试
    ├── 所属 LSF Array Job ID
    ├── Array Element 编号
    ├── 实际使用的 SP 和工作目录
    └── 当前状态与结果
```

这样即使多个 Testcase 共用一个 Array Job，系统仍然可以分别判断每个 Testcase 是否成功。

提交前先保存“准备提交”或“正在提交”的记录。提交成功后保存 LSF Job ID。

如果 Backend 在 `bsub` 成功后、保存 Job ID 前退出，重启后必须先尝试找回已经提交的 Job，不能直接再次提交。

### 7.5 补充仿真和失败重挂

算法补充 Code 时，只为新增的 Testcase 创建 SP 和执行尝试，然后把这些新 Testcase 组成新的 Array Job。已经成功完成的 Testcase 不能重新提交。

如果原 Array Job 中只有部分 Element 仿真失败，系统不会自动重挂。用户通过筛选页面检查后，可以选择忽略，也可以只为选中的 Testcase 创建新的执行尝试。重新提交时把这些失败项组成一个新的 Batch，而不是把原来的全部 31 个 Testcase 再运行一遍。

例如：

```text
原 Array：31 个 Element
├── 29 个成功
└── 2 个失败

重新提交：只包含失败的 2 个 Testcase
```

每次重新提交都创建新的执行记录和新的 Batch，不依赖 LSF 对旧 Element 的原地重挂。

### 7.6 等待仿真完成

系统批量查询本次仍在进行中的 LSF Job，并更新：

- 排队；
- 运行；
- 完成；
- 失败；
- 被取消；
- 暂时无法确认。

仿真业务成功的定义是：当前执行对应的 `status.json` 已经写为 `status_complete`。LSF 的 DONE/EXIT 和公共快照只用于跟踪与排查，不能代替 `status_complete` 把 Case 判为仿真成功。

### 7.7 执行 Postprocess

Postprocess 不以某个 Array Batch 完成为条件。系统检查某个 Corner、当前 Stage 最近一次算法生成的全部 Testcase；只有这组仿真都结束，才启动一次批量 Postprocess。

同一组 Testcase 可能包含第一次提交、部分失败重挂等多个 Batch。其中任意一个 Batch 完成都不代表这组 Testcase 已经完成。

本次算法生成的 Testcase 全部满足结束条件后，系统使用当前 Stage 的约定目录触发一次 Postprocess。扫描目录不要求只包含某个 Batch 的 Case。Postprocess 会递归检查其中的波形，并自行判断哪些需要处理；已经处理过的内容自动跳过。

Postprocess 对需要处理的波形批量生成 MT 文件，并分别得到四个 Phase 的关键指标：

```text
loss_0
loss_90
loss_180
loss_270
```

系统需要同时保存：

- 四个 Loss 数值；
- Postprocess 是否成功；
- 原始结果文件路径；
- Postprocess 日志；
- 使用的脚本或解析器版本。

如果多个 Corner 同时满足处理条件，批量 Postprocess 也需要排队并限制本机并发数量，避免短时间启动大量进程导致本机性能耗尽。

如果当前算法生成组中存在失败 Testcase，当前 Corner 等待用户决定。用户选择重新仿真时，先提交新的 Batch 并等待；用户选择忽略时，该 Testcase 不再阻塞当前组。只有全部失败项都已经被用户处理，且需要重新仿真的项目也已结束，才扫描当前 Stage 目录。

Postprocess 自身失败时也不自动再次扫描。当前 Corner 暂停等待用户检查；用户可以重新执行 Postprocess，或在确认源仿真有问题后筛选相应 Testcase 重新仿真。

### 7.8 做出算法决定

本次生成的 Testcase 有效结果准备完成后，再次调用算法。算法可以读取当前 Stage 到目前为止的全部有效结果。

算法有两类主要输出：

#### 补充 Code

算法认为现有结果不足，输出下一批 Testcase：

```text
当前算法判断完成
    ↓
创建补充 Testcase
    ↓
重复生成 SP、提交、Postprocess 和判断
```

补充 Testcase 会形成下一次算法调用所生成的新 Testcase 集合。UI 可以显示为下一轮，但代码侧不创建独立 Round 对象。

#### 进入算法指定的下一 Stage

算法认为当前 Stage 数据足够：

```text
保存当前 Stage 的 Code 结果
+ 保存支持该判断的 Loss
+ 保存算法判断依据
→ 进入算法指定的下一 Stage
```

前面 Stage 给出的 Code 不会被 Workflow 锁死；它只是后续算法的参考输入，后续 Stage 仍然可以选择不同值。最终运行同样看作一个 Stage，其完整 Code 由算法定义。

当前 Corner 一旦满足进入下一 Stage 的条件，就立即进入自己的下一 Stage，不等待同一个 Run 中的其他 Corner。

## 8. 多个 Corner 如何运行

用户可以从 300 到 3000 个可用 Corner 中选择本次需要运行的部分 Corner。

目前可以确认每个被选中的 Corner 都有：

- 自己的基础 SP；
- 自己的初始 MT；
- 自己解析出的初始 Code；
- 后续由算法提出的 Testcase；
- Testcase 仿真产生的四个 Phase Loss；
- 最终找到的 Code。

每个 Corner 的结果彼此独立，并分别反复调用算法、补充 Testcase 和得到最终 Code。

因此同一个 Run 内可以出现：

```text
Corner A：算法调用 #3 生成的 Testcase 运行中
Corner B：算法调用 #1 生成的 Testcase 运行中
Corner C：已经找到最终 Code
Corner D：部分 Array Element 失败，等待用户决定
```

为了提高 LSF 提交效率，多个 Corner 可以在同一时间批量准备和提交，但这种执行优化不能改变每个 Corner 独立决策的业务关系。

可以把每个 Corner 理解为一个独立的 Monte Carlo 采样点。同一个 Run 内，各 Corner 只共享本次 Run 的 PVT 条件和所选算法方案，不共享算法结果或推进状态。

## 9. 两种算法如何比较

系统需要支持选择不同算法方案，以比较它们最后找到的 Code 和仿真结果。

为了保证比较有效，至少应记录：

- 算法名称；
- 算法配置；
- 使用的基础文件和初始 Code；
- PVT 条件；
- 选择的 Corner；
- 每轮提出的 Testcase；
- 每轮得到的 Loss；
- 最终 Code；
- 总轮数、总仿真数和失败数。

代码层面不建立专门的“算法对比”对象。用户分别创建两个独立 Run，自行设置相同的基础数据、Corner 和 PVT 条件，再选择不同算法观察最终结果。系统只需如实记录各 Run 的设置和结果，不负责判断这两个 Run 是否满足公平比较条件。

## 10. 当前已经明确的主流程

```text
用户选择已有基础文件夹
        ↓
扫描 300～3000 个 Corner SP
        ↓
找到对应 MT，并解析初始 Code
        ↓
用户设置 PVT 条件（当前是 VDD / VCM）
        ↓
用户选择要运行的 Corner
        ↓
用户选择算法
        ↓
为每个 Corner 准备第一轮算法输入
        ↓
算法提出一个或多个 Testcase
        ↓
根据 Testcase 修改参数并创建新 SP
        ↓
按 Corner 提交：一个 Testcase 用普通 Job，多个用 Array Job
        ↓
分别跟踪每个 Testcase 的执行状态
        ↓
等待单个 Corner 当前 Stage、本次生成的全部 Testcase 结束
        ↓
对该 Stage 的约定目录执行一次批量 Postprocess
        ↓
获得 loss_0 / 90 / 180 / 270
        ↓
算法判断
   ┌────┴────┐
   ↓         ↓
补充 Code   进入下一 Stage
   ↓         ↓
下一轮      进入下一阶段
```

## 11. 后续讨论入口

当前初始化、补充 Corner 和 Run 的关系已经形成初步规则，见《重新扫描、补充 Corner 与 Rollback》。Array 映射和文件命名属于以后代码设计时再确认的内容。
