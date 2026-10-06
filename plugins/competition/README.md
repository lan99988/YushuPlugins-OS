# competition

独立插件 `yushuos.competition`，0.1.0，Core 0.3.1 / Python 3.11+。

解压所选 ZIP，运行 `yushuos install-plugin --path <staging>/plugin`；然后显式选择版本、绑定资源、授予权限。绑定 `competition_store`，target 为 `{"store_id":"personal"}`；权限 `competition.read/write`。

读取 query，变更 command。host_required 由宿主提供判断与证据。unknown 保留 request_id 并先 Core resume。业务与 proof 同事务，Core 收据另存。停用使用 user_disabled；移除包保留私有数据。

根目录运行 `python -m pytest plugins/competition/tests -q`。包锁不是沙箱或签名。详见 [安装](../../docs/INSTALL.md)。

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `competition.create` | internal_write | standalone | `title` |
| `competition.get` | read_only | standalone | `id` |
| `competition.list` | read_only | standalone |  |
| `competition.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `competition.delete` | internal_write | standalone | `id`, `expected_version` |
| `competition.archive` | internal_write | standalone | `id`, `expected_version` |
| `competition.restore` | internal_write | standalone | `id`, `expected_version` |
| `competition.milestone` | internal_write | standalone | `id`, `expected_version`, `record_id`, `at`, `label`, `value` |
| `competition.progress` | internal_write | standalone | `id`, `expected_version`, `record_id`, `at`, `label`, `value` |
| `competition.review` | internal_write | host_required | `id`, `expected_version`, `reason`, `judgement`, `evidence_refs` |
