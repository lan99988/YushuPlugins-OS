"""Create an isolated Core config and empty shared ledger for Task examples."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import tempfile

import yaml
from yushuos_sdk import StateStore


PLUGIN_ID = "yushuos.task"
PLUGIN_VERSION = "0.1.0"
STORE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
PERMISSIONS = ("task.read", "task.write", "task.delete")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="初始化独立 YushuOS Core 配置和空操作台账；不创建 Task 数据库。"
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        help="Core 独立配置根；省略时创建一个新的临时根并保留以供后续命令使用。",
    )
    parser.add_argument(
        "--store-id", "--task-store", dest="store_id", default="task-demo",
        help="Task 稳定 store_id（--task-store 为兼容旧示例的别名）。",
    )
    parser.add_argument(
        "--grant",
        action="append",
        choices=PERMISSIONS,
        default=None,
        help="附加 Task 权限；可重复传入。默认已授 task.read 与 task.write。",
    )
    parser.add_argument(
        "--allow-delete", action="store_true",
        help="显式增加 task.delete 授权；默认不授予删除权限。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not STORE_ID.fullmatch(args.store_id):
        print("--store-id 必须是 1–200 位稳定标识。", file=sys.stderr)
        return 2
    if args.grant and "task.delete" in args.grant and not args.allow_delete:
        print("task.delete 只能通过显式 --allow-delete 授权。", file=sys.stderr)
        return 2

    if args.config_root is None:
        config_root = Path(tempfile.mkdtemp(prefix="yushuos-task-core-")).absolute()
    else:
        config_root = args.config_root.expanduser().absolute()
    if config_root.is_symlink() or (config_root.exists() and not config_root.is_dir()):
        print("config-root 必须是普通目录，且不能是符号链接。", file=sys.stderr)
        return 2
    config_file = config_root / "config.yaml"
    ledger_path = config_root / "operations.sqlite3"
    ledger_sidecars = tuple(Path(f"{ledger_path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
    if config_file.exists() or config_file.is_symlink():
        print("config.yaml 已存在；为避免覆盖，未做任何更改。", file=sys.stderr)
        return 2
    if ledger_path.exists() or ledger_path.is_symlink():
        print("operations.sqlite3 已存在；为避免覆盖，未做任何更改。", file=sys.stderr)
        return 2
    if any(path.exists() or path.is_symlink() for path in ledger_sidecars):
        print("operations.sqlite3 存在 SQLite sidecar；为避免覆盖，未做任何更改。", file=sys.stderr)
        return 2
    config_root.mkdir(parents=True, exist_ok=True)

    grants = ["task.read", "task.write"]
    for permission in args.grant or []:
        if permission not in grants:
            grants.append(permission)
    if args.allow_delete and "task.delete" not in grants:
        grants.append("task.delete")
    config = {
        "schema_version": 1,
        "plugins": {"versions": {PLUGIN_ID: PLUGIN_VERSION}},
        "state": {"ledger_path": "operations.sqlite3"},
        "bindings": {"resources": {"task_store": args.store_id}},
        "permissions": {"grants": grants, "denials": []},
        "user_disabled": [],
    }

    # Reserve an empty ledger path before Core initializes it. This refuses a
    # pre-existing ledger and lets the cleanup below remove only files created
    # by this invocation if initialization or config creation fails.
    ledger_created = False
    config_created = False
    sidecars_before: set[Path] = set()
    try:
        with ledger_path.open("xb"):
            ledger_created = True
        sidecars_before = {path for path in ledger_sidecars if path.exists() or path.is_symlink()}
        with StateStore(ledger_path).connect(write=True):
            pass
        with config_file.open("x", encoding="utf-8", newline="\n") as stream:
            config_created = True
            yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
    except Exception as exc:
        # Never unlink a config created concurrently by another process. Only
        # remove paths this invocation successfully created, and retain the
        # ledger if another process has already published a config that may use it.
        if config_created:
            try:
                config_file.unlink(missing_ok=True)
            except OSError:
                pass
        if ledger_created and not (config_file.exists() or config_file.is_symlink()):
            for path in ledger_sidecars:
                if path not in sidecars_before:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            try:
                ledger_path.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"Core 初始化失败：{type(exc).__name__}。", file=sys.stderr)
        return 1

    print("Core 配置与空共享台账已初始化；Task 私有数据库尚未创建。")
    print(f"Core 配置根：{config_root}")
    print(f"Task 版本：{PLUGIN_VERSION}；store_id：{args.store_id}")
    print("已授权：" + ", ".join(grants))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
