# 开发、验收与发布 / Development and release

Core 固定来源见 <code>sources.lock.json</code>。领域插件源在 <code>plugins</code>，能力契约在 <code>contracts</code>，共享本地提交辅助在 <code>business_runtime</code>；开发调度器 <code>devflow</code> 与业务运行时分开。

## 当前交付状态

MVP 包含 20 个独立插件包。每个版本的真实状态见 GitHub Actions 和 Release 所附验收报告：五项必需 CI 是 Linux/Python 3.11、3.12、3.13，Windows/Python 3.12 和 macOS/Python 3.12。只有全部通过且独立审查闭环后才能发布。Feishu/IMA 的真实 App live-read/live-write 仍为 <code>not_verified</code>，模拟接合不能替代真实账号验收。

Suite 不发布到 PyPI。构建 ZIP、锁和独立安装验证不自动构成正式发布。每个发行包应绑定经过审查的源码 SHA、manifest、SHA256SUMS 与实际验证证据。

## 本地质量检查

~~~bash
python -m pip install -e ".[dev]"
python -m scripts.build_suite --output dist
python -m ruff check business_runtime integration scripts devflow tests plugins --select F,E9 --exclude "plugins/*/plugin/*"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m pytest plugins --ignore=plugins/task --ignore=plugins/feishu --ignore=plugins/ima -q
python -m scripts.verify_release --artifacts dist --core-source ../yushuos-core --clean-install
~~~

质量范围包括具体能力正反例、版本冲突、状态转换、金额/时间/分页边界、提交恢复、重复请求、资源 scope 与权限、Core 日志隐私、独立安装、锁与哈希。App 接合使用 fake transport；它不证明真实 App 写入。公共辅助代码变更需要相应回归。独立审查应绑定最终提交、契约与规则；未解决的 P0/P1/P2 阻断发布。

CI 矩阵固定 Core Git SHA，执行 Ruff、根测试、Task 测试、Suite 构建和安装包锁/清单检查。发布前还需核验干净安装与新提交的全部必需 CI；本地通过不能代替跨平台结果。

## 发布顺序与包边界

候选提交 → 独立审查 → PR → 当前 head 所有必需 CI → 普通合并 → 按合并 SHA 干净构建 → 校验锁与哈希 → 标签和 Release。不得强推、关闭保护或使用 admin bypass。已有 PR、标签或 Release 应按原身份查询并核验，避免重复创建；发行资产需与合并 SHA 绑定。

<code>build_suite</code> 只生成已声明的 contracts 与独立 plugin bundle。每个 ZIP 附 <code>plugin.lock.json</code>；套件输出 <code>suite-manifest.json</code> 和 <code>SHA256SUMS</code>。若缺少真实验收证据，manifest 中 mock 默认是 pending；不得为通过发布门槛而手改为 passed。App live-read/live-write 只有关联真实验收证据后才可改为 passed。

里程碑 A：Task、Capture、Idea、Bug、Body、Planner、DeepWork、Feishu。B：再加入 Finance、Relationship、Habit、LifeAdmin、Decision、IMA、Knowledge、Competition、Creation。C：再加入 Review、PersonalModel、Cognition。Task 为 0.2.0，其余为 0.1.0；套件计划从 suite-v0.1.0 分批发布，不上传 PyPI，不自动安装真实 Core，也不自动授予权限。

## 本机调度与模型可用性

后台 ChatGPT CLI 当前不支持所请求的 CLI 6 模型时，调度记录 <code>blocked_capability</code> 并保留工作进度，不自动降级或换成未经批准的模型。已实现的普通 watch 每 30 分钟查询 CLI 模型能力，策略核对通过后再继续派工；等待期间不发起模型 turn。每 5 秒检查暂停和停止，停止时取消在途探测子树。

Windows autostart 脚本应先以 <code>-Mode Plan</code> 检查，再以 <code>-Mode Install</code> 注册登录启动；实际运行使用隐藏窗口、重启和防重复实例。电脑关机期间不会运行。session 恢复、失败证据、使用量和发布逻辑以 <code>DEVFLOW.md</code> 为准；不得把 fresh retry 描述成恢复旧 session。

## English

The MVP contains 20 independent plugin packages. GitHub Actions and each Release's verification report record the actual version-specific state. The five required jobs are Linux/Python 3.11, 3.12, and 3.13; Windows/Python 3.12; and macOS/Python 3.12. Release only after all pass and independent review findings are resolved. Feishu/IMA live App reads and writes remain <code>not_verified</code>; mock integration does not prove a real account was accepted.

The Suite is not published on PyPI. Building ZIPs, validating locks, and checking independent installation do not by themselves constitute a release. Every release must bind its reviewed source SHA, manifest, SHA256SUMS, and verification evidence.

The quality commands above cover concrete capability cases, lifecycle transitions, boundary conditions, commit recovery, duplicate requests, scopes and grants, Core log privacy, independent installation, package locks, and hashes. Fake App transports do not verify live writes. Independent review must target the final commit, contracts, and rules; unresolved P0/P1/P2 findings block release.

Release sequence: candidate commit → independent review → PR → all required checks on the current head → ordinary merge → clean build from the merge SHA → lock/hash validation → tag and Release. Do not force-push, disable branch protection, or use an admin bypass. Inspect existing PRs, tags, and releases by their existing identity to avoid duplicates, and bind every artifact to the merge SHA.

<code>build_suite</code> creates only declared contracts and independent plugin bundles. Each ZIP includes <code>plugin.lock.json</code>; the suite output includes <code>suite-manifest.json</code> and <code>SHA256SUMS</code>. When there is no live verification evidence, the manifest defaults mock validation to pending. Never change that to passed to clear a release gate. Mark App live-read/live-write passed only with linked real verification evidence.

Milestone A includes Task, Capture, Idea, Bug, Body, Planner, DeepWork, and Feishu. Milestone B adds Finance, Relationship, Habit, LifeAdmin, Decision, IMA, Knowledge, Competition, and Creation. Milestone C adds Review, PersonalModel, and Cognition. Task is 0.2.0 and the other plugins are 0.1.0. The Suite plans staged suite-v0.1.0 releases; it is not uploaded to PyPI, does not install Core automatically, and does not grant permissions.

When the background ChatGPT CLI does not support the requested CLI 6 model, the scheduler records <code>blocked_capability</code> and preserves progress without downgrading or selecting unapproved models. The implemented ordinary watch queries CLI model capabilities every 30 minutes and dispatches only after the full policy passes. Waiting makes no model turn. Pause/stop checks run every five seconds, and stopping cancels an in-flight probe tree.

On Windows, inspect the autostart script with <code>-Mode Plan</code> before using <code>-Mode Install</code> to register login startup. Runtime uses a hidden window, restart behavior, and duplicate-instance protection; it does not run while the computer is off. See <code>DEVFLOW.md</code> for the actual support status of session recovery, failure evidence, usage limits, and release logic. Do not describe a fresh retry as recovery of an old session.
