# 计算节点 Scratch 与大规模状态检查

## 1. 问题与当前实现差异

Case 的固定目录位于网络驱动器。如果 HSPICE 直接在该目录读写，大量并行 Element 会把网盘同时用于输入读取、波形连续写入、日志写入和元数据更新，可能成为比 CPU 更早出现的瓶颈。

当前参考实现并没有为每个 Case 生成并提交独立 `run.sh`。LSF 执行的是统一的 `flow-batch-runner`，它从不可修改的 Batch 清单选择 Array Element，然后以网络 Case 目录作为工作目录执行配置的 HSPICE 命令。从架构上看，它等价于一个受控 `run.sh`，但当前尚未实现 Scratch 搬运。

由于 `/SCRATCH` 是被调度服务器的本机磁盘，Backend 在 `bsub` 返回后不能直接替它复制：此时任务可能尚未分配执行节点。stage-in 必须由 LSF 真正启动的 Runner/站点 `run.sh` 在目标节点上完成。

同样，精确知道数万个 `status.json` 路径并不等于检查成本很低。在网络文件系统上，即使只做 `stat`，数万个分散目录的元数据访问也可能持续很久或偶发卡住。这个风险成立，不能让扫描发生在 API 或主 Workflow worker 中。

## 2. 推荐执行路径

```text
Backend 网络控制区
  Batch manifest + Attempt status
            │
            ▼
LSF 启动统一 Runner
            │
            ▼
创建 /SCRATCH/<user>/flowpilot/<attempt_id>
            │
            ▼
取得执行节点原始 hostname，并在网络 Case 目录创建同名零字节标记
            │
            ▼
只把本次 Attempt 的单个 SP 复制到 Scratch
            │
            ▼
在节点本地磁盘运行 HSPICE
            │
            ▼
校验退出码和预期输出
            │
            ▼
把 Scratch 文件夹中的全部内容复制到 Testcase 所在磁盘的 Attempt 回传目录
            │
            ▼
成功则写 complete；失败则写 copyback_waiting 并保留 hostname 标记
            │
            ▼
Backend 核对复制结果，成功后才允许 Postprocess
            │
            ▼
Scratch 由站点自行执行已知的 24 小时清理
```

Scratch 路径必须包含稳定、不可冲突的 Attempt ID，不能只使用 Testcase 名称。Runner 创建目录时限制权限，拒绝符号链接和越界路径。`/SCRATCH` 不存在或不可写时，不启动 HSPICE，写明失败阶段。站点存在已知的 24 小时清理机制，但本项目不负责管理、倒计时或判断这套清理机制，只把 Scratch 当作临时存储。

## 3. stage-in 只复制单个 SP

现场已经确认单个 SP 自身即可运行，不需要复制 include、模型或 Testcase 目录中的其他文件。因此 stage-in 固定为只复制清单指定的 SP，并以该 SP 为入口运行 HSPICE。

Runner 必须校验源 SP 存在、目标复制成功且大小一致；不能把整个 Case 目录作为隐式输入。Batch manifest 只需要记录网络 SP 路径、Scratch 中的 SP 文件名和 HSPICE 命令。

## 4. stage-out 与结果正确性

HSPICE 结束后，Runner 将本次 Scratch 文件夹中的全部内容复制到 Testcase 所在网络磁盘的 Attempt 专属回传目录，包括 stage-in 的 SP、波形、日志以及仿真生成的其他文件。不能只复制预先猜测的输出扩展名。Runner 检查复制命令退出码；复制成功后 Backend 核对 Attempt，再发布到 Postprocess 使用的 Case 位置。

计算节点不能直接读取 Backend 的本地 SQLite，因此 manifest、状态和 Attempt 回传目录必须携带 Attempt 身份。不同 Attempt 的原始回传内容相互隔离，避免旧 Job 晚完成时直接覆盖新结果。用户决定放弃一个复制失败的 Submit 并重新运行时，Backend 先把旧 Attempt 标记为已放弃/已被替代，再创建新的 Attempt 和 Batch；旧记录仍保留供排错。

状态建议为：

`submit -> staging_in -> running -> staging_out -> complete`

异常分支包括 `simulation_failed`、`copyback_waiting` 和 `status_unknown`。其中 `copyback_waiting` 表示 HSPICE 已经成功完成，但 Testcase 所在网络磁盘可能容量或 inode 不足，结果尚未复制回来。用户可以先重试复制；如果 Backend 明确报告源目录不存在、SSH 失败、目标仍不可写或其他错误，用户也可以放弃旧 Submit 并重新运行。

只有 Scratch 文件夹全部内容复制成功后，Runner 才能写 `complete` 并允许 Postprocess。复制失败时保留 hostname 标记，记录 `copyback_waiting`、源 Scratch 路径、目标 Testcase 路径和复制错误。Backend 不推测错误原因，必须把 SSH/复制命令给出的明确错误返回前端。

### 4.1 hostname 零字节标记

LSF Job 真正落到节点后，普通 Job 子任务或 Array Element 独自取得该服务器的原始 hostname，并在 Testcase 网络目录创建一个大小为 0、文件名等于原始 hostname 的标记文件。只有这个 LSF 子任务知道自己实际运行在哪台服务器上，因此 hostname 的发现和标记创建不能由 Backend、Parquet 或其他机制代替。

LSF 子任务在 HSPICE 前尝试创建标记。按现场经验，即使网络盘空间不足也应能够创建零字节文件；即使创建动作意外返回失败，也不阻止仿真继续。Runner 应尽力把该异常写入状态或日志，但恢复 hostname 仍只认该子任务实际创建的标记。

复制失败时必须保留标记。文件名在作为 SSH 目标前必须经过 hostname 格式校验，不能接受用户在前端任意输入主机名。成功复制后是否保留标记不影响流程，后续结合现场习惯确认。

### 4.2 人工重启 copy-back

当复制失败时，Recovery 页面同时提供“重新复制”和“放弃本次 Submit 并重新运行”两种操作。“重新复制”只恢复 stage-out，不重新提交 LSF，也不重新执行 HSPICE：

1. 从当前 Attempt 对应的零字节标记文件名取得原始 hostname；Attempt 记录只提供 ID 和路径，不替代 hostname 标记；
2. 使用确定的 hostname 通过 SSH 登录原执行节点；
3. 检查 `/SCRATCH/<user>/flowpilot/<attempt_id>` 仍然存在且身份匹配；
4. 使用普通 SSH 在原节点重新执行复制，把 Scratch 文件夹全部内容复制到 Testcase 所在磁盘的 Attempt 回传目录；
5. 获取 SSH 和复制命令的退出码及错误输出；
6. 验证结果并更新状态，之后才允许 Postprocess。

第一版按普通 `ssh <hostname> <copy-command>` 设计，语义等价于在远端执行 `cp -a <scratch_path>/. <attempt_return_path>/`。SSH known_hosts/host key 的准备方式、认证和远端 shell 环境标记为真实机器核对项；Plan 不预先加入跳过 host-key 校验的参数。SSH 命令必须由 Backend 根据当前 Attempt 和 hostname 标记构造，不允许前端传入任意主机或任意源/目标路径。复制操作要幂等；重复点击不能创建多个并发复制任务。

项目只知道站点存在 24 小时清理机制，不负责显示倒计时、判断 Scratch 是否已被清理或自动改变状态。每次“重新复制”都实际执行 SSH/复制命令；如果源目录或文件已经不存在，Backend 明确显示这个错误。用户据此选择继续排查、再次复制，或放弃旧 Submit 并重新运行。

Backend 至少区分并原样保留诊断信息：`ssh_connection_failed`、`remote_source_missing`、`destination_unwritable`、`copy_command_failed`、`copy_verification_failed`。这些错误只说明本次复制操作为什么失败，不自动推断 24 小时清理是否发生。

## 5. 状态文件位置

默认继续把每个 Attempt 的 `status.json` 放在对应 Case 目录。一个 Attempt 只有实际运行它的 LSF 子任务写自己的状态文件，Backend 只读，因此当前不存在多个 Job 争写同一个 `status.json` 的问题。

把状态文件改放到按 Batch 分片的目录，只改变目录布局：文件总数、状态写入次数和 Backend 最终需要观察的 Attempt 数量都不会减少。它在某些文件系统上可能减少父目录切换，但也可能让一个 Array 的多个 Element 同时修改同一 Batch 目录，产生新的目录锁或元数据热点。因此不能把它当作确定的性能优化。

下面的布局仅保留为真实机器 A/B 性能实验，不作为默认架构：

```text
control/
└── batches/
    └── <batch_id>/
        ├── manifest.json
        └── status/
            ├── 000001.json
            ├── 000002.json
            └── ...
```

每个 Array Element 仍只写自己的文件，不允许多个节点并发改写一个聚合 JSON。只有实测证明 Batch 布局明显更快且没有目录热点时才考虑启用。

如果现场另有与大型波形盘真正独立、容量受控的控制文件系统，把 Status 放过去可能提高“结果盘爆满时仍能写错误状态”的可靠性；这是故障隔离收益，不是因为移动目录就减少了扫描量。

不建议让数万个 Element 高频写 heartbeat。Runner 只在状态边界原子写入；LSF Parquet 继续作为节点失联和终态缺失时的辅助证据。

## 6. 扫描不能拖住 Backend

状态检查器必须与 API、bsub、Workflow 和 Postprocess 隔离。推荐作为 Backend supervisor 管理的独立 worker 进程，而不只是同一事件循环中的线程，因为网络文件系统调用可能长时间阻塞。

- 数据库通过 lease 保证同一分片只有一个扫描者，不允许上一轮未完成时叠加下一轮；
- 每次领取小分片并在完成后保存游标，建议先以 100–500 个状态文件测试；
- 所有已提交任务目标周期约 10 分钟，分片在窗口内均匀展开；
- 以数据库中的到期活跃 Batch 为完整候选集合；Parquet 只用于安排优先级，终态或状态发生变化的 Job 优先读取状态文件，不能因为 Parquet 缺失或延迟而永久跳过状态文件；
- 单个分片超时或共享盘异常时记录降级、释放或延期 lease，不阻塞 API；
- 状态更新分批写入 SQLite，不包裹一个覆盖数万文件检查的大事务；
- 如果一轮实际耗时超过 10 分钟，不启动重叠扫描，而是显示“后台状态检查延迟”并继续保存进度。

仅比较 mtime/size 不能消除网络元数据成本，因为 `stat` 本身就是远程访问。真正有效的手段是：减少候选、小分片、低频检查、独立进程和现场基准测试；移动状态目录本身不算主要优化手段。

## 7. 前端只展示系统级检查健康度

主界面显示一个紧凑状态：

- `后台状态检查正常`：最近一轮/分片按计划完成；
- `正在检查任务状态`：扫描进行中，可显示大致进度；
- `状态检查延迟`：超过计划窗口仍未完成，但 Backend/API 仍在线；
- `状态检查异常`：共享盘、DuckDB 或 worker 连续失败。

最近成功时间和错误摘要放在展开详情中即可。普通用户不需要看到每个 Corner 的 `writtenAt/lastObservedAt/nextCheckAt`。单个 Corner 的原始证据只在诊断详情中提供。

## 8. 真实机器需要确认和压测

- `/SCRATCH` 是否每台执行节点都存在、容量、inode 和配额；24 小时清理是已知外部条件，不由项目管理；
- `$USER`、用户映射和目录权限规则；
- 原始 hostname 的具体格式、SSH 可达性、认证方式和远端恢复命令；
- 单个 SP stage-in 的目标命名和 HSPICE 命令；
- Scratch 整个文件夹复制到 Testcase 所在磁盘的 Attempt 回传目录时使用的复制工具、覆盖语义和校验方式；
- 网络文件系统对分散 `stat`、批量目录读取、并发复制和原子 rename 的表现；
- 1 万、5 万、10 万活跃 Attempt 下，每个分片耗时和完整覆盖周期；
- 节点宕机、Scratch 满、网络结果盘满、网络中断、stage-out 半完成、SSH 源目录不存在及旧 Attempt 晚完成的恢复行为。

Scratch 执行路径属于推荐架构；Batch 状态目录只是一项可选的实机 A/B 实验，默认仍使用 Case 内的状态文件。

## 9. 当前工程落地状态

当前版本已经搭好可在开发机验证的完整框架：LSF Runner 执行单 SP stage-in、本机 Scratch 仿真、全目录 stage-out、hostname 标记、Attempt 隔离目录、普通 SSH copy-back、放弃旧 Attempt 后重新提交，以及明确的复制错误分类均已实现。

状态检查器使用 SQLite 到期时间和 lease，每次最多领取 250 个活跃 Attempt；领取事务只返回精确的 `status.json` 路径，网络文件系统 `stat/read` 在事务外执行。文件 mtime/size 未变化时不重复解析 JSON。正常和失败后的检查周期均为 600 秒，调度线程每 10 秒醒来一次检查“是否有到期项”，不是持续扫盘，也不会让同一轮重叠执行。DuckDB/Parquet 快照以持久化 identity 去重，仅作为辅助证据。

API、bsub、普通 Workflow、Postprocess、状态检查分别运行在独立执行通道。bsub 提交前持久化 `submitting` 和开始时间，120 秒超时后进入 `status_unknown`，不会自动再次提交。前端通过统一 Snapshot 与 SSE revision 跟随状态，并只展示系统级检查健康度。

仍需在真实机器完成的事项保持为：`/SCRATCH` 权限与 hostname 格式、HSPICE 命令和输出、LSF Array 环境、SSH known_hosts/认证、远端 `cp -a` 行为、网络盘满/源目录消失，以及 1 万至 10 万 Attempt 的扫描基准。站点配置中的 `realMachineVerificationRequired` 明确保留这一交付边界。
