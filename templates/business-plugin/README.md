# 业务插件母版 / Business plugin template

复用 business_runtime/store.py 的本地事务和 adapter.py 的 Core 接合。
17 个领域使用同一辅助实现，每个包 vendoring 自己的副本；数据归插件所有。
业务、proof、原结果与事件意图同事务提交，恢复只核验 proof 并补交确认。

新增插件先冻结 Schema、状态转换、事件和 fixtures，然后实施业务。
不要只把目录改名就声称交付。

1. 在 contracts.py 声明具体输入/输出与事件。
2. 在 domains.py 实现确定性业务，不增加隐藏 LLM 或跨插件数据库读取。
3. 使用 scripts.build_suite.stage 生成临时 manifest、runner、辅助代码和锁。
4. 参照 tests/test_plugin_join_faults.py 验证故障中断、重复请求、权限和 scope。
5. 添加独立接合测试及安装文档。修改公共辅助触发完整回归。

Task 的完整独立项目 plugins/task 是另一参考母版。
远端 App 使用独立操作协议，不能声称远端写和本地 proof 原子提交。
