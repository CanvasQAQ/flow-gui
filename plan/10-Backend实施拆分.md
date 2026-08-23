# Backend 实施拆分

> 状态：第一版通用框架、运行时隔离、Scratch Runner、有界状态检查和 Electron 协同已实现；真机配置、规模基准和算法 Results 展示合同待完成（2026-08-23）
>
> 目的：把已经稳定、可以立即开发的后端能力，与仍需样例或业务决定的适配能力分开。

## 第一批：现在可以直接实现

这些工作只依赖当前已经确认的业务规则：

1. Python 单进程服务、配置加载、健康检查和版本信息；
2. SQLite migration、事务边界和核心对象表；
3. Dataset / Corner / Input Version 的保存模型；
4. Run / Run Corner / Stage Execution / Algorithm Call 的历史模型；
5. Testcase / Attempt / Batch / Batch Item / Postprocess 的执行模型；
6. 修改请求的幂等键记录，保证重复点击不重复创建任务；
7. 前端只读查询 API，包括 Dataset、Run、Corner 状态和摘要；
8. Corner 选择集合的校验和预览（文本语法确认后再接解析层）；
9. 流程推进器和状态检查器的空实现及单步执行接口；
10. 用 Fake Adapter 覆盖恢复、部分失败、暂停和幂等性的集成测试。

当前已经完成第 1～10 项的第一版通用实现，包括可持久工作队列和
Fake Algorithm / Scheduler / Postprocess 的端到端测试。这些测试可以验证业务
状态机与恢复不变式，但不能代替 LSF/HSPICE/共享盘真机验收。

## P0 运行时实现状态

1. `bsub`、Workflow 和 Postprocess 已按持久化 work type 拆分为独立线程 lane；
2. Runner 已实现计算节点 Scratch stage-in/stage-out、Attempt 身份隔离和 copy-back 恢复；
3. Case 内保留 Attempt-specific 原子 `status.json`，Batch 目录布局仍只作可选 A/B 测试；
4. 状态检查已有小分片、lease、到期时间和独立 worker，但 1 万/5 万/10 万 Attempt 的现场基准仍是验收项；
5. Backend 已提供 worker/状态检查健康度并只接纳当前 Attempt 证据；
6. Electron supervisor 已管理 Backend 启停、健康检查和日志入口，前端已接入 REST/SSE；
7. Dataset 扫描改为 `202 + operationId` 的后台任务仍未完成。

## 第二批：先定义接口，可并行等待真实资料

以下能力可以先编写 Adapter 接口和 Fake 实现，但真实实现需要输入样例：

| 能力 | 现在能做 | 开始真实实现前需要 |
| --- | --- | --- |
| Algorithm | 已有统一输入、决定输出和 Stage graph 接口；Results 展示合同未实现 | 至少一个算法方案、Stage 清单、脱敏图表数据样例及渲染需求 |
| MT Parser | 已实现 HSPICE 传统空格表格参考 Parser | 现场字段名、失败值和多行规则确认 |
| SP Renderer | 已实现 `.PARAM` 精确替换和原子写入 | 现场重复定义、参数命名确认 |
| LSF Submitter | 已实现 JSON Manifest、Runner、唯一名称和回执 | Queue/资源字段、执行节点环境确认 |
| Scratch Staging | 已实现单 SP stage-in、整目录 stage-out 和基于 hostname marker 的 SSH copy-back 恢复 | hostname/普通 SSH、known_hosts、覆盖与校验规则 |
| Snapshot Reader | 已实现 DuckDB Parquet 批量查询 | 现场列名、时间与终态保留规则 |
| Status Scanner | 已有有界分片、due-time、lease、快照 identity 和健康度实现 | 网络盘基准、分片大小；状态目录布局仅作可选 A/B 测试 |
| Postprocess | 已实现命令 Adapter、生成 MT 定位和统一解析 | 现场确认脚本命令、MT 文件名和字段映射 |

## 暂时不要写死的内容

- 固定 Stage 数量或 `Stage N + 1` 推进规则；
- 现场是否继续使用参考 JSON Manifest，或提供等价的站点 Adapter；
- LSF Job 名称的最终长度和字符规则；
- SP、波形、日志、MT 的最终文件名；
- Status wrapper 的最终错误码；
- Postprocess 是本地任务还是 LSF 任务；
- LSF Setting 的固定字段集合。
- 普通 SSH copy-back 的认证、known_hosts/host key 准备方式和远端命令位置。

这些内容全部放在 Adapter 或配置边界之后，避免后续确认时改动 Workflow 核心。

## 建议后续任务顺序

1. 按 `backend/docs/ON_MACHINE_AGENT_GUIDE.md` 建立私有验证样例和上机日志；
2. 填写 Site Config，保持 `dryRun=true`，并逐项处理 Integration Checks；
3. 在真实机器确认 Scratch 和 stage-in/stage-out 约定，并按需对状态目录布局做 A/B 测试；
4. 用私有 SP/MT 小样例执行 Parser/Renderer 合规测试；
5. 安装真实算法包并验证 Stage/Decision 契约；
6. 在 `dryRun=true` 下检查 Batch Manifest 和 `bsub` 参数；
7. 映射公共 Parquet 列并覆盖全部状态证据测试；
8. 接入现场 Postprocess 脚本，确认其生成 MT 的路径、文件名和字段；
9. 使用专用测试 Queue 完成普通 Job、Array、重启和局部重提验收；
10. 完成 1 万/5 万/10 万活动 Attempt 的现场压测，并根据 IOPS/CPU 数据固化分片与周期。

详细要求见 `backend/docs/INTEGRATION_CONTRACTS.md` 和
`backend/docs/REAL_MACHINE_CHECKLIST.md`。
Scratch 与扫描器的详细边界见《计算节点 Scratch 与大规模状态检查》。
