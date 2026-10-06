# YushuOS Plugins

YushuOS Plugins 是一套独立安装的本地优先个人管理插件。仓库当前包含 17 个领域插件、Task 0.2.0，以及独立 Feishu/IMA 连接适配器。插件依照 Core 的清单、能力目录和统一 JSON 入口工作；Core 固定为 [YushuCore-OS commit a198be8eb463581b8d18440fb46558c35fe62f5f](https://github.com/lan99988/YushuCore-OS/tree/a198be8eb463581b8d18440fb46558c35fe62f5f)。

## 当前范围

| 插件 | slug / plugin ID | 当前代码提供的能力摘要 |
|---|---|---|
| Capture | <code>capture</code> / <code>yushuos.capture</code> | 捕获条目 CRUD、分类、标记处理、归档/恢复 |
| Idea | <code>idea</code> / <code>yushuos.idea</code> | 想法 CRUD、转换/丢弃 |
| Bug | <code>bug</code> / <code>yushuos.bug</code> | 问题 CRUD、严重级别、解决/重开/关闭、关联任务 |
| Finance | <code>finance</code> / <code>yushuos.finance</code> | 收支流水、月度快照、月结、汇总 |
| Relationship | <code>relationship</code> / <code>yushuos.relationship</code> | 联系人、互动记录、最近互动 |
| Habit | <code>habit</code> / <code>yushuos.habit</code> | 习惯 CRUD、打卡/撤销、暂停/恢复、历史 |
| Body | <code>body</code> / <code>yushuos.body</code> | 状态、睡眠、训练记录，恢复摘要 |
| Life admin | <code>lifeadmin</code> / <code>yushuos.lifeadmin</code> | 生活行政事项 CRUD、完成、续期 |
| Decision | <code>decision</code> / <code>yushuos.decision</code> | 决策 CRUD、选项选择、结果记录、host review |
| Planner | <code>planner</code> / <code>yushuos.planner</code> | 计划、规则化生成/预览、应用、重排 |
| Deep work | <code>deepwork</code> / <code>yushuos.deepwork</code> | 专注会话开始、暂停、继续、结束、取消、中断 |
| Competition | <code>competition</code> / <code>yushuos.competition</code> | 比赛 CRUD、里程碑/进度、host review |
| Knowledge | <code>knowledge</code> / <code>yushuos.knowledge</code> | 知识条目、概念/链接/洞见、搜索 |
| Creation | <code>creation</code> / <code>yushuos.creation</code> | 创作条目、阶段推进、发布记录 |
| Review | <code>review</code> / <code>yushuos.review</code> | 结构化复盘生成、查询、定稿、比较、host interpretation |
| Personal model | <code>personal_model</code> / <code>yushuos.personal_model</code> | 假设 CRUD、关联证据、host evaluation |
| Cognition | <code>cognition</code> / <code>yushuos.cognition</code> | 模式 CRUD、分析/复核（host 提供判断） |
| Task | <code>task</code> / <code>yushuos.task</code> | <code>task.create/get/list/update/complete/reopen/delete/cancel/archive</code> |
| Feishu | <code>feishu</code> / <code>yushuos.feishu</code> | 日历/任务/消息适配；模拟接合已测，线上读写未验收 |
| IMA | <code>ima</code> / <code>yushuos.ima</code> | 笔记、笔记本、知识库读取/检索及笔记创建/追加；模拟接合已测，线上读写未验收 |

17 个本地领域插件共定义 155 项能力。以 <code>business_runtime/contracts.py</code>、Task 契约和各 App adapter manifest 为准。Personal model 的 slug 是 <code>personal_model</code>，plugin ID 是 <code>yushuos.personal_model</code>；其能力名使用 <code>personal-model.*</code> 前缀，避免 Core 的能力前缀解析问题。

初次体验可从 Capture 的本地捕获与检索、Task 的待办创建和完成、Planner 的计划预览开始。先预览计划与写入内容，再按需要逐次授权执行。

## 安装

Suite 不发布到 PyPI。下面的命令从源码安装固定 Core 与 Suite 开发依赖，二者共用 Suite 的虚拟环境。完整的模板初始化、Core 部署、ZIP 选择/安装、配置与恢复步骤见[安装指南](docs/INSTALL.md)。

~~~powershell
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
Set-Location yushuos-plugins
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "..\yushuos-core" -e ".[dev]"
~~~

~~~bash
git clone https://github.com/lan99988/YushuCore-OS.git yushuos-core
git -C yushuos-core checkout a198be8eb463581b8d18440fb46558c35fe62f5f
git clone https://github.com/lan99988/YushuPlugins-OS.git yushuos-plugins
cd yushuos-plugins
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ../yushuos-core -e '.[dev]'
~~~

本轮 MVP 的 20 个模拟插件包已构建，独立安装路径已验证。五项跨平台 CI 仍在运行，尚不能报告全部通过；Feishu/IMA 的真实 App 读写仍为 <code>not_verified</code>。构建包、模拟安装与真实 App 验收是不同证据。安装只复制并校验不可变插件版本；还要配置 Core 版本选择、资源绑定和权限。Core 没有 <code>init</code>、<code>enable</code> 或 <code>uninstall-plugin</code> 子命令；插件数据保存在独立目录，移除包不会删除数据。

## 安全和执行边界

- Core 负责 catalog、路由、Schema 校验、权限门禁和共享 receipt/outbox；宿主负责理解请求和提供 host 判断。<code>host_required</code> 能力不会调用 Core 内置 AI。
- <code>query</code> 用于读取，<code>command</code> 用于变更。预览、获准执行和真实 App 写入是不同状态；安装插件不会授予业务权限，也不会自动执行 App 写入。
- 每个领域插件把业务数据保存在自己的私有 SQLite 数据库。插件之间不得直接读取彼此数据库；跨域读取和协作通过 <code>integration/flows.py</code> 调用 Core。
- 一次本地写入在该插件自己的 SQLite 事务中同时提交业务变更与 proof。Core SDK 的 receipt/outbox 在另一数据库中，二者不构成跨库 ACID 事务。
- 结果状态包括 <code>succeeded</code>、<code>failed</code>、<code>unavailable</code>、<code>unknown</code> 和 <code>preview</code>。只有 <code>succeeded</code> 表示请求明确成功；预览未提交，<code>unknown</code> 需要核验。
- 请求沿用原 <code>request_id</code>。本地插件的未知结果按 Core 收据和插件 proof 恢复；App 外部写入的 <code>unknown</code> 必须核验 adapter 的私有 receipt，Core 0.3.1 的通用 <code>resume</code> 不足以确认 App 结果。AI 宿主请看[调用指南](docs/AI-INSTALL.md)和 [App connector 说明](docs/APP-CONNECTORS.md)。
- Codex 与 WorkBuddy 可用固定 Core 的 <code>install-host</code> 安装托管 Skill/Rule 入口；这不会安装业务插件、连接 App 或授予权限。约束见 [Core AI 安装说明](https://github.com/lan99988/YushuCore-OS/blob/a198be8eb463581b8d18440fb46558c35fe62f5f/docs/zh-CN/AI-INSTALL.md)。

## 开发与 CI

~~~bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m scripts.build_suite --output dist
~~~

当前五项 CI 矩阵覆盖 Linux Python 3.11/3.12/3.13、Windows Python 3.12 和 macOS Python 3.12，使用固定 Core SHA。矩阵仍在运行，未确认完成前不得写成全绿。App live-read/live-write 未验证。细节见[插件标准](docs/PLUGIN-STANDARD.md)、[能力规格](docs/PLUGIN-SPECS.md)及[发布流程](docs/RELEASE.md)。
