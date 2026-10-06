# lifeadmin

独立插件 `yushuos.lifeadmin`，0.1.0，Core 0.3.1 / Python 3.11+。

解压所选 ZIP，运行 `yushuos install-plugin --path <staging>/plugin`；然后显式选择版本、绑定资源、授予权限。绑定 `lifeadmin_store`，target 为 `{"store_id":"personal"}`；权限 `lifeadmin.read/write`。

读取 query，变更 command。host_required 由宿主提供判断与证据。unknown 保留 request_id 并先 Core resume。业务与 proof 同事务，Core 收据另存。停用使用 user_disabled；移除包保留私有数据。

根目录运行 `python -m pytest plugins/lifeadmin/tests -q`。包锁不是沙箱或签名。详见 [安装](../../docs/INSTALL.md)。

| Capability | Effect | Mode | Required fields |
|---|---|---|---|
| `lifeadmin.create` | internal_write | standalone | `title`, `due_date` |
| `lifeadmin.get` | read_only | standalone | `id` |
| `lifeadmin.list` | read_only | standalone |  |
| `lifeadmin.update` | internal_write | standalone | `id`, `expected_version`, `changes` |
| `lifeadmin.complete` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.delete` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.archive` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.restore` | internal_write | standalone | `id`, `expected_version` |
| `lifeadmin.renew` | internal_write | standalone | `id`, `expected_version`, `due_date`, `reason` |
