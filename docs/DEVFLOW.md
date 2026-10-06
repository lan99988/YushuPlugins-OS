# Devflow 持久化开发队列

使用标准库 Python，以 `python -m devflow` 运行。协调器进程退出后数据库、日志、模型报告、工作树和修改仍保留。数据库默认为 Windows `%LOCALAPPDATA%/yushuos-devflow/<repo hash>/state.db`，其他系统为 `~/.local/state/yushuos-devflow/<repo hash>/state.db`。`--state` 必须指向仓库外。

```powershell
python -m devflow --repo C:/path/repo init --manifest C:/path/tasks.json
python -m devflow --repo C:/path/repo run --watch --workers 3 --publish auto
python -m devflow --repo C:/path/repo status
python -m devflow --repo C:/path/repo pause
python -m devflow --repo C:/path/repo resume --task plugin-example
python -m devflow --repo C:/path/repo stop
python -m devflow --repo C:/path/repo report
```

`init` 要求初始仓库干净。默认清单为 `devflow/tasks.json`，支持外部绝对路径。重复初始化相同清单保留状态；契约发生变化会拒绝，应使用新的状态目录。`run` 单次运行可执行所有就绪任务；`--watch` 持续检查队列与发布门禁。暂停停止接收新任务，正在执行的任务可完成；停止终止已登记的进程树并保留修改。恢复仅将中断和认证阻塞任务重新入队；达到普通失败上限的任务需要检查证据后使用新任务清单/状态。

清单版本为 1；任务 ID 唯一，仅字母数字、下划线、连字符。依赖必须存在且不能成环。每项必须有明确路径白名单及独立测试 argv。路径以 `/` 分隔，以 `/` 结尾表示整个目录，否则表示单个文件；不接受 `..`、绝对路径或通配符。

```json
{
  "version": 1,
  "tasks": [{
    "id": "plugin-example",
    "deps": [],
    "allowed_paths": ["plugins/example/", "tests/test_example.py"],
    "tests": [["C:/path/.venv/Scripts/python.exe", "-m", "unittest", "discover", "-s", "tests", "-p", "test_example.py"]],
    "risk": "low",
    "prompt": "实现插件，遵守 AGENTS.md，完成测试。",
    "publish": {"enabled": true, "base": "main", "release_tag": "example-v0.1.0"}
  }]
}
```

执行任务最多占用 3 个槽位。单 SQLite 协调器租约与 fencing 阻止重复协调；共享白名单目录/文件的任务串行。每项有独立 Git 分支与工作树，依赖任务已验证 head 会合并到后继任务基线；冲突保留现场。主检出不受任务修改影响。所有跟踪和未跟踪修改都检查白名单，独立测试由协调器执行；测试完成后的 Git tree、任务契约摘要、AGENTS 规则摘要共同绑定独立只读审查。任务模型声明成功无法令任务完成。

模型阶梯为 L1 `gpt-6-luna/max`、L2 `gpt-6.1-sol/medium`、L3 `gpt-6.1-sol/high`、L4 `gpt-6.1-sol/max`。任务风险决定起点；敏感路径自动升至 L4。普通错误最多 4 次，重复失败或后续失败升级。仅允许 GPT 6.1 Sol 与 Luna/max，禁止 Astra、5.6 或任何隐式模型回退。网络失败指数退避；额度不足等待 30/60 分钟或服务 resetAt；认证失败阻塞。单命令 30 分钟超时，终止进程树。重新启动依据 PID 和进程创建标识清理登记的遗留进程，保留差异；中断任务需 `resume`。

发布默认 `manual`。`auto` 仅对清单 `publish.enabled=true` 项推送并创建/复用 PR。发布再次检查 head、tree、干净状态；PR head 必须等于本地审查 head，必须有当前提交通过的 CI 检查；通过 GraphQL 查询实际分支保护，再查询仓库/组织的活动 branch rulesets。规则要求外部审批时必须 `APPROVED`，明确无外部审批要求时使用本地独立 head 审查；未知规则（包括无法判断权限的 404）保持等待。然后使用 `gh pr merge --match-head-commit`。不使用管理员绕过或关闭分支保护。待 CI/审批期间保留 `awaiting_gates` 并继续轮询。合并后可创建指向 merge commit 的版本标签和 GitHub Release，重复执行验证已有标签目标。

Codex prompt 使用 stdin，固定 `--json --output-schema --output-last-message -m -c model_reasoning_effort`，实现用 `workspace-write`，审查用 `read-only`。可通过 `DEVFLOW_CODEX` / `DEVFLOW_GH` 指定命令路径。gh 在缺少令牌时从 Git Credential Manager 获取 GitHub 凭据，只注入该子进程环境；日志和错误清除令牌。仅 gh 传输 EOF/代理错误时去除该子进程代理重试一次。

报告严格字段：`task_id/tree_sha/contract_sha/rules_sha/outcome/summary/tests/findings`。`outcome` 只能 `pass` 或 `fail`，未知字段、错误类型、过期绑定、审查发现均拒绝。数据库保存 attempt、model、reasoning、session、token_usage、evidence、工作树、SHA 与 PR 状态；`report` 输出完整任务 JSON。

测试使用临时 Git 仓库和 fake Codex/gh，不进行真实 App 或 GitHub 写入：

```powershell
python -m unittest discover -s tests -p 'test_devflow*.py'
```


## 本机能力核对与显式模型策略

`init` 读取本机原生 Codex 可执行文件，运行 `--help`、`exec --help`，再通过 app-server 的 `initialize/model/list` 协议获取当前动态模型清单及支持的 reasoning effort。这不会发起 agent turn 或业务 App 写入。Windows npm 包自动解析其原生 `codex.exe`，避免后台执行 PowerShell 包装脚本失败。版本、路径、模型清单、effort 与最终选择保存在仓库外数据库；`status/report` 可以查看。

默认计划阶梯保持 6 系列。实际 CLI 未列出所需模型或 effort 时，初始化标记 `blocked_capability`；清单缺失或为空时标记 `blocked_spec`。未知模型不会按桌面工具列表猜测为可用，已观察到的能力证据保留。需要调整已授权模型的四级策略时，显式提供 `init --models-policy C:/absolute/models.json`；文件有四个顺序级别，全部核对动态清单：

```json
{
  "levels": [
    {"model": "gpt-6-luna", "effort": "max"},
    {"model": "gpt-6.1-sol", "effort": "medium"},
    {"model": "gpt-6.1-sol", "effort": "high"},
    {"model": "gpt-6.1-sol", "effort": "max"}
  ]
}
```

以上是默认四级策略的文件形式。策略只接受四级 levels，不允许 l4_fallback。仅 Sol 与 Luna/max 可被授权；模型不存在、CLI 账号拒绝，或指定 effort 不受支持均阻塞，不能改用 5.6。

`run --watch` 在能力阻塞时进入普通程序的等待循环，此时尚未启动开发协调器，也不会派发 Agent 或发起模型 turn。首次重探测在初始化失败或旧状态进入 watch 后 30 分钟进行，之后每 30 分钟重试；截止时间持久化，重启等待进程不会触发快速重试。普通 Python 探测子进程只调用 CLI 帮助、版本及 app-server `initialize/model/list`，然后按数据库中保存的模型策略重新校验全部四级。模型清单查询成功但策略仍不受支持时保存新的观察证据并保持阻塞；查询失败时保留原证据、准确错误及任务状态。等待期间所有 pending 任务、工作树、用量和 attempt 都保留。

只有所需模型与 effort 全部明确可用时，watch 才保存含 `selected` 的新证据、清除能力错误，并启动普通 Coordinator。不会自动降低至 5.6、Astra 或改变策略。`status/report` 的 `capability_watch` 提供 `waiting/probing/last_probe_at/next_probe_at`，时间为 Unix 秒；处于等待状态表示等待模型能力，不能据此宣称自动开发已经运行。单次 `run` 在阻塞时仍返回非零和具体原因。`resume` 不会绕过未通过的能力核对。

等待循环每最多 5 秒检查 `pause/stop`，包括探测正在进行时。暂停期间不开始新探测，也不派发任务；已在途的只读能力探测可以完成并保存证据，但不会解除用户暂停。`resume` 后在到期时探测，或在已有成功证据时继续协调器。`stop` 与 `blocked_spec` 退出等待进程；停止会取消在途探测子树，不等待 CLI 协议超时。此轮重探测不包含业务 App 调用，也不记为模型 token 用量。

## Windows 后台登录启动

`devflow/install_autostart.ps1` 提供 `Plan/Install/Launch/Uninstall`。必须传绝对 Repo、Python、Manifest、Codex、Gh、State 路径；State 位于仓库外。先完成 `init` 能力核对和清单初始化。`Plan` 只输出可审查计划，不注册系统任务：

```powershell
powershell -NoProfile -File C:/path/repo/devflow/install_autostart.ps1 -Mode Plan -Repo C:/path/repo -Python C:/path/.venv/Scripts/python.exe -Manifest C:/path/tasks.json -Codex C:/path/codex.exe -Gh 'C:/Program Files/GitHub CLI/gh.exe' -State C:/path/external-state
```

将 Mode 改为 Install 才注册登录启动；可选 `-StartNow` 立即启动。任务名包含仓库摘要，配置与日志写到外部 State。调度器 `IgnoreNew`、全局命名 mutex 和数据库租约共同避免双实例；所有 native 参数使用 Windows 引号规则。PowerShell 和 Python 子进程隐藏窗口，子进程失败 30 秒后重启，登录任务也配置失败重启。`blocked_capability` 会启动并保持隐藏的普通程序 watch，等待模型能力；此时不代表自动开发已运行。`stop` 或 `blocked_spec` 会令启动器结束。未在测试中实际注册计划任务。


## 验证、重试与发布资产

发布只由协调器主线程执行，并有独占 publisher lock；worker 只生成 verified 状态。非 gh 的全部子进程剥离 GH_TOKEN/GITHUB_TOKEN，日志同时脱敏原始环境和 gh 私有环境中的两个令牌。后台启动器也清除其子进程环境中的 GitHub 令牌。

独立测试在每条命令前后固定 Git tree，失败命令同样保存前后 tree 和日志；源码发生变化即拒绝。实现阶段与审查阶段报告都严格校验 schema、task、tree、contract、rules；实现声明通过不能代替独立测试。依赖保存 head/tree/contract/rules 审查快照，依赖审查或工作树失效时后继阻塞并保留现场。

重试明确使用新会话（session_strategy=fresh_retry），保留 prior_session、工作树及所有 attempt 的原始用量记录。新 prompt 携带 last_failure、先前证据摘要和失败日志尾部。usage_records 追加保存实施/审查各阶段、成功/失败、原始 usage_events；token_usage 是已报告数字的累计，不把缺失用量当作零。异常记录结构化 failure_kind，能力、依赖、认证、额度和网络分别处理，普通测试中出现 auth 单词不会误判认证。

确认 PR merge SHA 后，在仓库外建立独立 detached checkout 构建。publish 可提供 build_commands，每项是 argv，支持 {python}/{output_dir}；统一套件入口为 [{python}, -m, scripts.build_suite, --output, {output_dir}]。构建器生成 checkout 文件时，必须在 build_write_paths 明确声明生成路径（如 contracts/ 和各 plugins/<slug>/plugin/）；源文件变化则拒绝。assets 可声明额外外部输出文件，统一构建的 ZIP、suite-manifest.json 自动收集，构建器校验和保留为 suite-SHA256SUMS。

每次发布包含合并 SHA 的 source ZIP、release.lock.json（merge/source/build tree、来源锁、契约和逐资产 SHA256）及总 SHA256SUMS。GitHub release 创建时上传资产，重试核对已有资产 digest 或下载后哈希；哈希不符拒绝覆盖。版本标签必须绑定确认的 merge commit。
初始化会先校验并持久化完整任务队列，再核对 CLI 能力；blocked_capability 时任务仍显示 pending，同一 manifest 重新 init 保留既有工作树和状态。Windows 隐藏 watch 可以在能力阻塞期间每 30 分钟重探测，能力真正满足后恢复协调器；保持等待期间不会启动不受支持的模型。

默认队列是 20 项已交付插件的独立验收，不重复开发桌面已完成的代码。`kind=verify` 先由程序运行测试，再只发起一次独立 `read-only` 审查；无 diff 是正常状态，任何源码变更或规则漂移都拒绝。成功证据绑定现有 head/tree/contract/rules，不创建空提交、PR 或 Release，verify 任务不能启用 publish。发现缺陷会保存证据并阻塞，不要求审查者偷偷修复。新增开发任务使用 `kind=implement`（默认值），保留实际 diff、独立测试、独立审查和可选自动发布流程。两个路径都重新核对测试期间的规则，以及发布/依赖现场的实际规则摘要；Git 忽略的 AGENTS.md 也会使旧证据失效。
