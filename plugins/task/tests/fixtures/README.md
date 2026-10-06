# schema v1 fixture

`schema-v1.sql` 由套件导入基线 `bf52db8` 的 Task 0.1.0 实现生成（原 Task 来源 `f0cee1d`），使用标准库 SQLite `iterdump` 导出；全部内容是测试数据。它包含原始 CHECK/FK/index 定义、有序标签、原版本 provider pins、结果正文与待确认事件意图。

测试通过该文件重建真实 v1 数据库，避免把 v2 库改一个版本号来假装旧库。测试不依赖运行时 Git，也不会打开原 Task 仓库或真实用户数据库。
