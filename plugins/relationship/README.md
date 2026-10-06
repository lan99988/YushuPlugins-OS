# relationship

独立插件 `yushuos.relationship`，0.1.0，Core 0.3.1 / Python 3.11+。

解压所选 ZIP，运行 `yushuos install-plugin --path <staging>/plugin`；然后显式选择版本、绑定资源、授予权限。绑定 `relationship_store`，target 为 `{"store_id":"personal"}`；权限 `relationship.read/write`。

读取 query，变更 command。host_required 由宿主提供判断与证据。unknown 保留 request_id 并先 Core resume。业务与 proof 同事务，Core 收据另存。停用使用 user_disabled；移除包保留私有数据。

根目录运行 `python -m pytest plugins/relationship/tests -q`。包锁不是沙箱或签名。详见 [安装](../../docs/INSTALL.md)。

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `relationship.person.create` | internal_write | standalone | `name` |
| `relationship.person.get` | read_only | standalone | `id` |
| `relationship.person.list` | read_only | standalone |  |
| `relationship.person.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `relationship.person.archive` | internal_write | standalone | `id`, `expected_version` |
| `relationship.person.restore` | internal_write | standalone | `id`, `expected_version` |
| `relationship.interaction.record` | internal_write | standalone | `person_id`, `at` |
| `relationship.interaction.list` | read_only | standalone |  |
| `relationship.recent` | read_only | standalone | `person_id` |
