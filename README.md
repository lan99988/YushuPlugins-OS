# YushuOS Plugins

YushuOS Plugins 是一套独立安装的本地优先个人管理插件。仓库当前包含 17 个领域插件的共享业务运行时、Task 0.2.0 插件，以及独立 Feishu/IMA 连接适配器。插件依照 Core 的清单、能力目录和统一 JSON 入口工作；Core 固定为 [YushuCore-OS `a198be8eb463581b8d18440fb46558c35fe62f5f`](https://github.com/lan99988/YushuCore-OS/tree/a198be8eb463581b8d18440fb46558c35fe62f5f)。

## 当前范围

| 插件 | slug / plugin ID | 当前代码提供的能力摘要 |
|---|---|---|
| Capture | `capture` / `yushuos.capture` | 捕获条目 CRUD、分类、标记处理、归档/恢复 |
| Idea | `idea` / `yushuos.idea` | 想法 CRUD、转换/丢弃 |
| Bug | `bug` / `yushuos.bug` | 问题 CRUD、严重级别、解决/重开/关闭、关联任务 |
| Finance | `finance` / `yushuos.finance` | 收支流水、月度快照、月结、汇总 |
| Relationship | `relationship` / `yushuos.relationship` | 联系人、互动记录、最近互动 |
| Habit | `habit` / `yushuos.habit` | 习惯 CRUD、打卡/撤销、暂停/恢复、历史 |
| Body | `body` / `yushuos.body` | 状态、睡眠、训练记录，恢复摘要 |
| Life admin | `lifeadmin` / `yushuos.lifeadmin` | 生活行政事项 CRUD、完成、续期 |
| Decision | `decision` / `yushuos.decision` | 决策 CRUD、选项选择、结果记录、host review |
| Planner | `planner` / `yushuos.planner` | 计划、规则化生成/预览、应用、重排 |
| Deep work | `deepwork` / `yushuos.deepwork` | 专注会话开始、暂停、继续、结束、取消、中断 |
| Competition | `competition` / `yushuos.competition` | 比赛 CRUD、里程碑/进度、host review |
| Knowledge | `knowledge` / `yushuos.knowledge` | 知识条目、概念/链接/洞见、搜索 |
| Creation | `creation` / `yushuos.creation` | 创作条目、阶段推进、发布记录 |
| Review | `review` / `yushuos.review` | 结构化复盘生成、查询、定稿、比较、host interpretation |
| Personal model | `personal_model` / `yushuos.personal_model` | 假设 CRUD、关联证据、host evaluation |
| Cognition | `cognition` / `yushuos.cognition` | 模式 CRUD、分析/复核（host 提供判断） |
| Task | `task` / `yushuos.task` | `task.create/get/list/update/complete/reopen/delete/cancel/archive` |
| Feishu | `feishu` / `yushuos.feishu` | 日历/任务/消息适配；模拟接合已测试，线上读写未验收 |
| IMA | `ima` / `yushuos.ima` | 日历/任务/消息适配；模拟接合已测试，线上读写未验收 |

17 个本地领域插件共定义 155 项能力。以 `business_runtime/contracts.py`、Task 契约和各 App adapter manifest 为准。personal model 的 slug 是 `personal_model`，plugin ID 是 `yushuos.personal_model`；其能力名使用 `personal-model.*` 前缀，避免 Core 的 capability 前缀解析把下划线当成分隔符。

## 安装

Suite 不发布到 PyPI。先安装固定 Core，再从已生成的套件 ZIP 中选择要安装的插件。构建中的 suite ZIP 是 `dist/` 下的 20 个独立归档，附 `SHA256SUMS` 和 `suite-manifest.json`；命令和逐步说明见[安装指南](docs/INSTALL.md)。发行包的构建与锁验证可本地执行；正式发布仍要求最终代码审查和完整 CI。线上 App 验收状态单独记录。

```powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
python -m pip install -e .\yushuos-core
```

该仓库的完整 Core 固定版本和 suite 构建步骤以[安装指南](docs/INSTALL.md)为准。插件安装只复制并校验版本包；还需由操作者配置 Core 的版本选择、宿主权限和资源绑定。卸载插件包会保留插件私有数据。

## 安全和执行边界

- Core 负责 catalog、路由、Schema 校验、权限门禁和共享 receipt/outbox；宿主负责理解请求和提供 host 判断。`host_required` 能力不会调用 Core 内置 AI。
- `query` 用于读取，`command` 用于变更。预览、授权执行和真实 App 写入是不同状态；安装插件不会授予业务权限，也不会自动执行 App 写入。
- 每个领域插件将业务数据保存在自己的私有 SQLite 数据库。插件之间不得直接读取彼此数据库；跨域读取和协作通过 `integration/flows.py` 调用 Core。
- 一次本地写入在该插件自己的 SQLite 事务中同时提交业务变更与 proof。Core SDK 的 receipt/outbox 在另一数据库中，二者不构成跨库 ACID 事务。
- 结果状态为 `succeeded`、`failed`、`unavailable`、`unknown` 或 `preview`。标准结果顶层字段为 `status`、`request_id`、`message`、`resource`、`data`、`error`。
- 请求固定原 `request_id`；遇到 `unknown` 时先经 Core 查询/恢复，不要换 ID 重复提交。AI 宿主应遵循[调用指南](docs/zh-CN/AI-INSTALL.md)。
- Skill/Rule 是宿主入口。Codex 或 WorkBuddy 的手动接合步骤见 [Core AI 安装说明](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/zh-CN/AI-INSTALL.md)；本仓库不宣称存在自动宿主安装器。

## 开发和 CI

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m scripts.build_suite --output dist
```

完整矩阵还在 Linux、Windows、macOS 上运行，固定 Core Git SHA，执行 Ruff、根测试、Task 测试、suite 构建及安装包锁/清单检查。App live-read/live-write 尚未验证；不得将未运行的 CI 描述为通过。细节见[插件标准](docs/PLUGIN-STANDARD.md)、[能力规格](docs/PLUGIN-SPECS.md)及[发布流程](docs/RELEASE.md)。
