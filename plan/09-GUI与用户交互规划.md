# GUI 与用户交互规划

> 状态：基于当前主流程的第一版界面规划
>
> 目的：让 GUI 围绕真实 LSF 工作流服务，而不是让用户手工模拟 Backend 的内部步骤。

## 1. 设计原则

### Backend 是唯一状态来源

React 只展示 Backend 返回的状态和允许的操作，不根据文件、计时器或前端内存自行判断 Stage 是否完成。

界面不提供“生成 Testcase”“提交 LSF”“Postprocess”“Analyze”这类常规手工推进按钮。它们是流程推进器自动执行的小步骤，用户只需要看到进度、证据和异常原因。

### Run 是主要工作上下文

VCM 和 VDD 是一组 Run 的 PVT 配置，不是顶层标签。用户在界面中的主要层级是：

```text
初始化数据
└── Run（一组 VDD / VCM + 一个算法方案）
    └── Corner（独立推进）
        └── Stage 执行
            └── 算法调用 / Testcase / 执行尝试 / Batch
```

### 每个 Corner 独立推进

Run 总览不能使用一条全局 Stage 进度条暗示全部 Corner 同步前进。界面按 Stage 展示 Corner 分布，同时明确说明每个 Corner 在结果就绪后立即独立调用算法并进入下一步。

### 自动流程与人工恢复分开

正常监控和人工恢复是两条不同路径：

- 正常监控：查看 Run、Corner、Stage、算法决定和结果；
- 人工恢复：筛选 Testcase，检查执行证据，明确选择忽略或重新仿真。

失败、状态未知和 Postprocess 失败都不自动重试。仿真已完成但结果未复制回来时显示“结果复制失败”及 Backend 返回的具体错误，并在 Case Recovery 中提供“重新复制”和“放弃本次 Submit 并重新运行”。项目不展示 Scratch 清理倒计时，也不替用户判断文件是否仍然存在。

## 2. 一级导航

### 初始化数据

用于管理可复用的基础 Corner 清单：

- 添加基础目录；
- 查看扫描时间、输入版本、可用、异常和缺失数量；
- 查看异常 Corner 及具体原因；
- 执行追加扫描或覆盖扫描；
- 明确提示扫描不会改变已有 Run。

追加扫描作为推荐操作。覆盖扫描需要解释会重新解析全部 Corner，并为变化的内容建立新输入版本。

### 运行流程

用于创建和查看 Run：

- 创建 Run；
- 固定初始化数据版本；
- 配置一组 VDD / VCM；
- 选择算法方案；
- 使用编号列表或反向排除选择 Corner；
- 查看 Run 总览、Corner 监控和活动记录；
- 暂停或继续 Run；
- 向已有 Run 补充 Corner。

### Case 恢复

只在当前 Run 内筛选 Testcase。支持按 Corner、Stage、算法调用序号、执行状态、LSF Job 和文件证据筛选。

三项主要操作：

- 忽略：Testcase 不再阻塞当前算法生成组，历史记录和文件保留；
- 重新仿真：新增执行尝试，按 Corner 组成新 Batch；逻辑上仍属于固定 Case，但使用独立 Scratch 和 Attempt 回传目录。
- 重新复制结果：仅用于 HSPICE 已成功但 copy-back 失败的 Attempt；根据 LSF 子任务创建的 hostname 标记通过普通 SSH 恢复复制，不创建新的仿真。复制失败状态下同时允许用户放弃旧 Submit 并重新运行。

执行前必须提供预览，显示选中数量、预计 Batch 数量、当前旧任务证据和目录覆盖风险。

### 设置

管理 LSF 和本地 Postprocess 配置。设置变化只影响之后创建的 Batch，每个 Batch 保存实际使用配置的副本。

## 3. Run 总览

Run 标题区域固定显示：

- Run 名称和运行状态；
- 初始化数据；
- VDD / VCM；
- 算法方案；
- Corner 数量；
- 创建时间和总体完成度。

总览使用四组摘要：

- 已完成；
- 正在推进；
- 等待人工处理；
- 尚未开始。

Stage 区域使用连续分布进度条显示各 Stage 当前 Corner 数量，而不是全局当前 Stage。Stage 名称和数量由算法方案返回，GUI 不固定假设一定有四个 Stage。点击进度条中的 Stage，可以直接过滤下方 Corner 状态图。

上千个 Corner 的同步 Review 以状态矩阵作为主要界面：

- Corner 按固定编号顺序从左到右、从上到下排列，筛选后不重新排序；
- 色块同时表达当前业务状态，图例提供状态文字和精确数量；
- 支持按 Stage、状态联动筛选，并将未命中项目淡化以保留编号空间上下文；
- 支持紧凑和放大密度；
- 悬停显示 Corner、Stage、算法调用轮次和 Testcase 进度；
- 点击色块打开 Corner 详情；
- 状态矩阵用于快速扫描异常簇，表格作为精确搜索、排序和无障碍替代视图。

“需要处理”区域只提供摘要和进入 Case 恢复页面的入口，不在总览中塞入复杂批量操作。

## 4. Corner 监控与详情

Corner 状态矩阵是同步 Review 的默认入口；Corner 列表放在独立页签中，一行表示一个 Run Corner，而不是一个 Batch 或一个 Array Element。至少显示：

- Corner；
- 当前业务状态；
- 当前有效 Stage 和算法调用轮次；
- 当前算法生成组的 Testcase 完成数量；
- 固定输入版本以及基础数据是否已有新版；
- 最终 Loss；
- 更新时间。

Corner 详情使用大尺寸模态弹窗，不使用侧边抽屉。弹窗默认打开 Workflow，并包含四个页签：

1. **Workflow**：第一屏展示动态 Stage 轨道、当前 Stage 执行、算法调用轮次，以及当前已经到达的具体步骤。Stage 内步骤为算法生成 Testcase、准备 Case/SP、提交 Batch、整组仿真、异常处理门、批量 Postprocess、算法判断；
2. **Info**：展示 Run、PVT、算法方案、固定输入版本、原始 SP/MT、初始 Code 和当前执行目录；
3. **Results**：按算法方案和 Stage 选择不同的图表或表格渲染器，展示每个 Stage 的结果；
4. **Testcases**：在一张表中展示当前有效路径上的全部 Testcase，按 Stage 明确分组；已有结果直接展示四个 Phase Loss，同时提供复制具体 Testcase Path 的独立列。

> 实现状态（2026-08-23）：第 3 项目前只有前端固定渲染器和模拟数据，尚未实现算法包到 Backend API 再到 UI 的版本化展示合同。生产接入边界与推荐 v1 数据格式见 `backend/docs/INTEGRATION_CONTRACTS.md` 和 `backend/docs/ON_MACHINE_AGENT_GUIDE.md`。
>
> 结果展示必须同时支持“当前 Stage Execution 的全部 Round”和“指定 Round”两种范围，由 Stage descriptor 声明默认值与可用范围。规范见 `backend/docs/ALGORITHM_PRESENTATION_CONTRACT.md`。

Workflow 只展示 Backend 自动推进的进度，不提供 `Run from here` 或 `Rewind` 操作。失败、状态未知、重新仿真、重新复制结果和忽略仍由 Case Recovery 页面处理。

历史上被替代的 Stage 执行可以在详情中查看，但不能与当前有效路径混在一起推动流程。

第一版界面的所有可见文本统一使用英文，避免同一套工程术语在中英文之间出现不一致映射。

## 5. 暂停语义

主按钮使用“暂停新任务”，并明确说明：

- 不再创建或提交新任务；
- 已经提交的 PEND / RUN Job 继续运行；
- 状态检查和结果收集继续；
- 已经满足条件的后处理仍可完成。

取消等待任务和终止运行任务属于另外的高风险操作，不能隐藏在普通暂停中。

## 6. 状态表达

GUI 需要区分业务状态和观察证据。第一版至少展示：

```text
未开始
准备文件
LSF 排队
仿真中
结果待复制
后处理中
等待人工处理
状态待确认
失败
已完成
已忽略
```

“公共快照暂未找到”不能直接显示为失败。“仿真成功”只能由当前执行匹配的 `status_complete` 证据确认。

状态不能只依赖颜色，所有状态必须同时显示文字或图标。

## 7. 创建和补充 Corner

创建 Run 使用三步流程：

1. 选择初始化数据和固定输入版本；
2. 配置 Run 名称、VDD、VCM 和算法方案；
3. 输入 Corner 编号列表或反向排除条件，预览可加入、不可用和跳过数量。

向已有 Run 补充 Corner 使用相同筛选方式，但不允许修改 Run 的 PVT 或算法方案。确认前显示：

- 尚未存在、实际会新增的 Corner；
- 已经存在、不会重复加入的 Corner；
- 当前不可用的 Corner。

## 8. 不进入 GUI 的能力

第一版 GUI 不提供 Rollback 或 Replay。

Rollback 是算法调试用 Backend 能力，需要处理当前有效路径、仍在运行的 Job 和历史分支。它通过后端调试入口执行，GUI 只展示执行后 Backend 返回的新有效路径和保留的历史记录。

## 9. 与 Backend 对接时的边界

当前前端数据是交互原型。接入 Backend 时：

- Stage 清单、状态、允许操作和错误原因均由 API 返回；
- 列表筛选、分页和“选择全部符合条件”由 Backend 执行；
- 重新提交必须先调用预览接口，再用预览令牌确认创建新 Batch；
- 所有修改操作需要幂等键，防止重复点击创建重复 Batch；
- 界面刷新只读取 Backend，不直接读取公共快照或执行 LSF 命令。
