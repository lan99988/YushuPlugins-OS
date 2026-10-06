# 开发、验收与发布 / Development and release

Core 固定来源见 sources.lock.json。业务源在 plugins，冻结能力在 contracts，公共本地提交辅助在 business_runtime；开发调度器 devflow 与业务运行时分开。

## 本地质量检查

```bash
python -m pip install -e ".[dev]"
python -m scripts.build_suite --output dist
python -m ruff check business_runtime integration scripts devflow tests plugins --select F,E9 --exclude "plugins/*/plugin/*"
python -m pytest tests -q
python -m pytest plugins/task/tests -q
python -m pytest plugins --ignore=plugins/task --ignore=plugins/feishu --ignore=plugins/ima -q
python -m scripts.verify_release --artifacts dist --core-source ../yushuos-core --clean-install
```

质量包括：全部具体能力正向/无效输入、版本冲突、状态转换、金额/时间/分页边界、提交恢复、重复请求、scope与权限、Core日志隐私、独立安装、锁与哈希。每个本地插件都有 Core 接合回归；App 只使用 fake transport，无真实写入。
公共辅助修改运行完整回归。独立审查必须绑定最终提交、契约与规则；审查者不能是同任务作者。P0/P1/P2 未解决不能发布。

CI 矩阵为 Linux Python3.11/3.12/3.13，Windows/macOS Python3.12，全部使用固定 Core 和虚构数据。发布前还核验干净安装、完整系统链路和新提交的五套 CI。不能用本地通过代替跨平台通过。

## 自动发布边界

候选提交 → 独立审查 → PR → 当前 head 全部必需 CI → 普通合并 → 合并 SHA 独立构建 → 校验锁/哈希 → 标签与 Release。
不强推，不关闭保护，不使用 admin bypass。已有 PR/标签/Release 按原身份查询并核验，不重复发布；资产与合并提交绑定。

build_suite 只生成所声明的 contracts 和 plugin bundle。每包 ZIP、suite-manifest、SHA256SUMS 供独立安装。
生成器默认 validation.mock=pending；只有关联真实验收证据的发布流程才能标为 passed。
App live_read/live_write 当前 not_verified，相关账号能力保持 unavailable；mock 通过不等于真实连接通过。

里程碑 A：Task/Capture/Idea/Bug/Body/Planner/DeepWork/Feishu。
B：再加入 Finance/Relationship/Habit/LifeAdmin/Decision/IMA/Knowledge/Competition/Creation。
C：再加入 Review/PersonalModel/Cognition。Task0.2.0，其余0.1.0；套件从 suite-v0.1.0 分批发布，不上传 PyPI，不自动安装真实 Core 或授予权限。

## 本机调度与模型

```powershell
python -m devflow init
python -m devflow run --watch --workers 3 --publish auto
python -m devflow status
python -m devflow pause
python -m devflow resume
python -m devflow stop
python -m devflow report
```

状态、日志、输出和 worktree 保存于仓库外。一个协调器及三个槽位；公共文件和发布只有一个写入者。
L1 为 gpt-6-luna/max，L2 gpt-6.1-sol/medium，L3 Sol/high，L4 Sol/max。没有 Astra，也不会自动换成5.6。
CLI model/list 和非交互选项必须真实支持所选模型；不支持时 blocked_capability，保留进度。
本机当前 ChatGPT CLI 已拒绝 gpt-6.1-sol，这个环境限制不能靠调度器修改消除。桌面智能体完成的交付与后台 CLI 队列分别报告。

Windows autostart 脚本先 PlanOnly 查看，再注册登录启动；它使用隐藏窗口、重启和禁止重复实例。电脑关机期间不运行。
恢复 session、失败证据、使用量和发布逻辑的实际支持以 DEVFLOW.md 为准；不能将 fresh retry 声称为恢复旧会话。

## English

Run complete regression and the five-platform matrix, review the final source independently, and release only from the merged SHA.
Bundles are independently locked and hashed. Installation grants no business permissions.
Fake App tests do not establish live verification. Current App live-read/write are unverified.
Devflow uses Luna/max and Sol only; an unsupported CLI model blocks the queue instead of downgrading.
