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

模型阶梯为 L1 `gpt-6-luna/medium`、L2 `gpt-6.1-sol/medium`、L3 `gpt-6.1-sol/high`、L4 `gpt-6-astra/high`。任务风险决定起点；敏感路径自动升至 L4。普通错误最多 4 次，重复失败或后续失败升级。仅明确不可用的 Astra 才回退到 Sol/max，记录原因。网络失败指数退避；额度不足等待 30/60 分钟或服务 resetAt；认证失败阻塞。单命令 30 分钟超时，终止进程树。重新启动依据 PID 和进程创建标识清理登记的遗留进程，保留差异；中断任务需 `resume`。

发布默认 `manual`。`auto` 仅对清单 `publish.enabled=true` 项推送并创建/复用 PR。发布再次检查 head、tree、干净状态；PR head 必须等于本地审查 head，必须有当前提交通过的 CI 检查；通过 GraphQL 查询实际分支保护，再查询仓库/组织的活动 branch rulesets。规则要求外部审批时必须 `APPROVED`，明确无外部审批要求时使用本地独立 head 审查；未知规则（包括无法判断权限的 404）保持等待。然后使用 `gh pr merge --match-head-commit`。不使用管理员绕过或关闭分支保护。待 CI/审批期间保留 `awaiting_gates` 并继续轮询。合并后可创建指向 merge commit 的版本标签和 GitHub Release，重复执行验证已有标签目标。

Codex prompt 使用 stdin，固定 `--json --output-schema --output-last-message -m -c model_reasoning_effort`，实现用 `workspace-write`，审查用 `read-only`。可通过 `DEVFLOW_CODEX` / `DEVFLOW_GH` 指定命令路径。gh 在缺少令牌时从 Git Credential Manager 获取 GitHub 凭据，只注入该子进程环境；日志和错误清除令牌。仅 gh 传输 EOF/代理错误时去除该子进程代理重试一次。

报告严格字段：`task_id/tree_sha/contract_sha/rules_sha/outcome/summary/tests/findings`。`outcome` 只能 `pass` 或 `fail`，未知字段、错误类型、过期绑定、审查发现均拒绝。数据库保存 attempt、model、reasoning、session、token_usage、evidence、工作树、SHA 与 PR 状态；`report` 输出完整任务 JSON。

测试使用临时 Git 仓库和 fake Codex/gh，不进行真实 App 或 GitHub 写入：

```powershell
python -m unittest discover -s tests -p 'test_devflow*.py'
```


## 本机能力核对与显式模型策略

`init` 读取本机原生 Codex 可执行文件，运行 `--help`、`exec --help`，再通过 app-server 的 `initialize/model/list` 协议获取当前动态模型清单及支持的 reasoning effort。这不会发起 agent turn 或业务 App 写入。Windows npm 包自动解析其原生 `codex.exe`，避免后台执行 PowerShell 包装脚本失败。版本、路径、模型清单、effort 与最终选择保存在仓库外数据库；`status/report` 可以查看。

默认计划阶梯保持 6 系列。实际 CLI 未列出所需模型或 effort 时，初始化标记 `blocked_capability`；清单缺失或为空时标记 `blocked_spec`。未知模型不会按桌面工具列表猜测为可用，已观察到的能力证据保留。需要备用模型时，显式提供 `init --models-policy C:/absolute/models.json`；文件有四个顺序级别，全部核对动态清单：

```json
{
  "levels": [
    {"model": "gpt-5.6-luna", "effort": "medium"},
    {"model": "gpt-5.6-sol", "effort": "medium"},
    {"model": "gpt-5.6-sol", "effort": "high"},
    {"model": "gpt-5.6-sol", "effort": "max"}
  ]
}
```

这是备用策略格式示例，默认不会自动采用。可选 `l4_fallback` 具有 `model/effort`，仅 L4 模型明确不在动态清单时使用，并记录原因。模型存在但指定 effort 不受支持会阻塞。

## Windows 后台登录启动

`devflow/install_autostart.ps1` 提供 `Plan/Install/Launch/Uninstall`。必须传绝对 Repo、Python、Manifest、Codex、Gh、State 路径；State 位于仓库外。先完成 `init` 能力核对和清单初始化。`Plan` 只输出可审查计划，不注册系统任务：

```powershell
powershell -NoProfile -File C:/path/repo/devflow/install_autostart.ps1 -Mode Plan -Repo C:/path/repo -Python C:/path/.venv/Scripts/python.exe -Manifest C:/path/tasks.json -Codex C:/path/codex.exe -Gh 'C:/Program Files/GitHub CLI/gh.exe' -State C:/path/external-state
```

将 Mode 改为 Install 才注册登录启动；可选 `-StartNow` 立即启动。任务名包含仓库摘要，配置与日志写到外部 State。调度器 `IgnoreNew`、全局命名 mutex 和数据库租约共同避免双实例；所有 native 参数使用 Windows 引号规则。PowerShell 和 Python 子进程隐藏窗口，子进程失败 30 秒后重启，登录任务也配置失败重启。`stop` 或能力/清单阻塞会令启动器结束，不运行业务写入。未在测试中实际注册计划任务。
