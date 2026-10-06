> Historical source verification. Hashes below belong to earlier artifacts; the final suite requires its own checks and hashes.

# Verification report

This report separates Task checks from Core checks. It records only results from completed test and clean-install runs; the Task GitHub Actions matrix is defined but has not yet been reported as run.

## Task 0.2.0 — 2026-10-06 本地验收

隔离工作树：`<isolated-task-worktree>`；Python 3.12，已安装 Core 0.3.1。仅修改 plugins/task，原 Task 仓库与 Core 未改动，也未发布。

| 检查 | 结果 |
|---|---|
| 全量基线与扩展 | `python -m pytest plugins/task/tests -q`：**96 passed，0 failed/0 skipped，97.73 秒**。包含原 77 项适配 v2 版本/schema 断言，以及新增 15 项单元、4 项真实 Core runner 集成测试。 |
| TDD | 首先观察取消、归档、重开、列表和迁移扩展因缺失实现而失败；备份无法创建与 SQLite 备份中断回归各自先 RED 再 GREEN。最终单独扩展测试：`python -m pytest plugins/task/tests/test_extension.py -q`，**15 passed**。 |
| 迁移与完整性 | 真实 v1 SQL fixture；只读不迁移；首次写前完整备份；原任务/标签/schema_meta.created_at/proof 字段保留，原结果 JSON 不补字段；foreign_key_check 与 integrity_check 通过；备份失败、DDL 失败回滚与不完整备份清理通过。 |
| 新能力真实 runner | cancel/archive 权限 read+write、store scope、preview、版本先于 no-op、默认/显式归档列表、提交后退出进程、Core 重启 resume、原收据保留、原结果/事件意图不变、事件 refs-only 与去重均通过。 |
| 源码与 manifest | `python plugins/task/scripts/render_manifest.py --check`、`python plugins/task/scripts/stage_plugin.py --check`、`git diff --check` 均成功。 |
| 锁定 ZIP | `python plugins/task/scripts/build_plugin.py --core-path <pinned-core-source>` 成功；两次重建 SHA-256 一致：`d92942f13c0f0c4301e627fe0c19aec21c04c7d0189cfa0f956ff884b50d4c5c`。产物：`dist/yushuos-task-0.2.0.zip`。 |
| ZIP 解压安装 smoke | 新临时目录解压 ZIP，Core load_manifest 确认 package_hash_verified；真实 CLI 完成 create/list/update/complete/reopen/cancel/archive/reopen、status 及原 create 请求重放，退出码 0；临时配置/数据已移除。 |

全量命令使用 `PYTHONPATH=plugins/task/src`、`PYTHONUTF8=1` 与 `YUSHUOS_CORE_SOURCE=<pinned-core-source>`。Python 可执行文件：`<venv>/Scripts/python.exe`。示例命令：`python plugins/task/examples/demo.py --plugin-path plugins/task/plugin --yushuos <venv>/Scripts/yushuos.exe`。

本轮为 Windows/Python 3.12 本地验证，未运行 0.2.0 的远端 CI 或其他操作系统；下方 0.1.0/wheel/Core 记录属于历史基线，不能当作本轮扩展的新验证。schema v2 回退和备份保留规则见 [0.2.0 扩展规格](docs/TASK-0.2.0.md)。

## Task 0.1.0

| Check | Result |
|---|---|
| Full Task suite | Command: `python -m pytest -q`. **77 total, 77 passed, 0 failed, 0 skipped** on Python 3.12 with the pinned Core 0.3.1 source; the root integration run independently repeated all 77 passing tests. |
| Real CLI integration | **17 cases passed** across 14 test functions. Coverage includes all seven capabilities, success/no-op/version and request conflicts, query and preview without Task storage, receipt/outbox/event import, recovery/replay, expiry, provider drift, abandoned locks, resource/permission gates, privacy, and precise disable/removal. Crash tests cover three create process-exit windows, one update process-exit window, plus Core ledger-confirmation exception and retry. |
| Setup helper | Passed in a fresh config root: created only Core config and an empty ledger, with no `plugin-data`. Default grants were `task.read` and `task.write`, without `task.delete`. A second invocation with a different store returned exit code 2 and left the original config SHA-256 unchanged. |
| Final locked Task ZIP | SHA-256: `c4123b32af5db1a001221b56650db045199e48a2a9e4e11ff979b8665690fd01`. The lock JSON is normalized to LF for portable builds; the independent seven-capability clean-environment CLI smoke passed again against this final ZIP. |
| Earlier Task wheel used in the clean-install smoke | SHA-256: `286cb685e48692726679080571ff639d0bdafbf82fdb115b8a2314f5d8255427`. This tested artifact predates the final README update and has been replaced by the final wheel below. |
| Final Task wheel | SHA-256: `2006bdaf97ab314d83832240ff2790c9009ef42bcde53c545b25fcd91767ed30`. Rebuilt after the final README updates; clean-environment installation, version/import checks and `pip check` passed. |
| Clean-environment install and CLI smoke | Passed with the Core 0.3.1 wheel and the Task ZIP. The smoke exercised all seven capabilities, Core preview, read/list without creating a Task DB, replay from the original result, metadata-only Core storage, and disabling/removing the exact installed code version while retaining Task data and the shared ledger. |
| ZIP independence | Passed after installing and then uninstalling the Task wheel: the locked ZIP still ran without relying on a globally installed `yushuos_task` package. |
| Example walkthrough | `python examples/demo.py --plugin-path plugin` passed in a clean environment with the Core CLI path explicitly supplied. The real CLI installed Task into a new temporary Core root, completed create/list/update/complete, checked status, and replayed create with its original request/result (version 1); the temporary root was removed afterward. |
| Task cross-platform GitHub Actions | **Not run: the Task remote repository has not yet been provided.** The workflow defines Ubuntu/Python 3.11, 3.12, and 3.13, macOS/Python 3.12, and Windows/Python 3.12. |
| Local 10,000-row reference measurement | Seeding: **0.868 s**; median query: **6.386 ms**. This is one local reference measurement, not a performance guarantee or CI threshold. |

The reported full-suite command is `python -m pytest -q`; the test total is 77, including 17 expanded integration cases. The exact integration cases are maintained in `tests/integration/test_runner_recovery.py`.

## Core 0.3.1 compatibility checks

The Task package pins Core source revision `3d784c23034b8cd590327da36f6fa8d41f0dd362`. Core's independent suite reported **148 passed**, preserving the original 138-test baseline. The five-job Core CI matrix passed for Linux/Python 3.11, 3.12, and 3.13, macOS/Python 3.12, and Windows/Python 3.12. The latest successful Core workflow is [run 37333002308](https://github.com/lan99988/YushuCore-OS/actions/runs/37333002308). These are Core results; they do not constitute a Task GitHub Actions run.

No claim is made here that the Task workflow has run on all three operating systems. The package hashes above identify the artifacts used for the clean-environment Task smoke.
