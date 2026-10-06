"""Run Task through the real YushuOS Core CLI, optionally as an isolated demo."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


CAPABILITIES = (
    "task.create",
    "task.get",
    "task.list",
    "task.update",
    "task.complete",
    "task.reopen",
    "task.delete",
)
READ_CAPABILITIES = {"task.get", "task.list"}
WRITE_CAPABILITIES = set(CAPABILITIES) - READ_CAPABILITIES


def _json_object(raw: str) -> dict:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (json.JSONDecodeError, ValueError):
        raise argparse.ArgumentTypeError("--fields 必须是有效的 JSON 对象") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--fields 顶层必须是 JSON 对象")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 YushuOS Core CLI 调用 Task；--plugin-path 会使用全新的临时 Core 配置。"
    )
    parser.add_argument("--plugin-path", type=Path, help="可选：锁定后的 Task plugin/ 目录，运行完整隔离演示。")
    parser.add_argument("--config-root", type=Path, help="已有 Core 配置根；capability 单次调用模式必填。")
    parser.add_argument("--store-id", help="Task store 标识；单次调用模式必填，完整演示默认 task-demo。")
    parser.add_argument("--capability", choices=CAPABILITIES, help="单次调用的 Task capability。")
    parser.add_argument("--fields", type=_json_object, default={})
    parser.add_argument("--request-id", help="默认自动生成唯一 request_id。")
    parser.add_argument("--save-request", type=Path, help="保存原请求 JSON，可用 Core resume 命令核对。")
    parser.add_argument("--preview", action="store_true", help="单次写调用只请求 Core 预览。")
    parser.add_argument("--yushuos", default="yushuos", help="Core CLI 程序名或可执行文件路径。")
    return parser


def _find_cli(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.name == value:
        return shutil.which(value)
    return str(candidate.absolute()) if candidate.is_file() else None


def _decode_output(stdout: str) -> dict | None:
    if not stdout.strip():
        return None
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _print_json(label: str, value: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))


def _run_core(cli: str, config_root: Path, args: list[str], *, payload: str | None = None) -> tuple[int, dict | None]:
    command = [cli, "--config-root", str(config_root), *args]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    except OSError as exc:
        print(f"无法启动 Core CLI：{type(exc).__name__}。", file=sys.stderr)
        return 127, None
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    response = _decode_output(completed.stdout)
    if response is None and completed.stdout:
        sys.stdout.write(completed.stdout)
    return completed.returncode, response


def _request(capability: str, fields: dict, store_id: str, request_id: str) -> dict:
    target = {"store_id": store_id}
    if "task_id" in fields:
        target["task_id"] = fields["task_id"]
    return {
        "request_id": request_id,
        "capability": capability,
        "intent": "query" if capability in READ_CAPABILITIES else "command",
        "fields": fields,
        "target": target,
    }


def _invoke(cli: str, config_root: Path, request: dict, *, preview: bool = False) -> dict | None:
    capability = request["capability"]
    mode = "preview" if preview else "execute"
    host_mode = "execute" if capability in WRITE_CAPABILITIES and not preview else "readonly"
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    returncode, response = _run_core(
        cli,
        config_root,
        ["invoke", "--file", "-", "--mode", mode, "--host-mode", host_mode],
        payload=payload,
    )
    _print_json(f"Core invoke · {capability}", response if response is not None else {"exit_code": returncode})
    if returncode != 0 or response is None:
        return None
    return response


def _direct_invoke(args: argparse.Namespace, cli: str) -> int:
    if args.config_root is None or not args.store_id or not args.capability:
        print("单次调用需要显式提供 --config-root、--store-id 和 --capability。", file=sys.stderr)
        return 2
    request_id = args.request_id or f"task-demo-{uuid.uuid4().hex}"
    request = _request(args.capability, args.fields, args.store_id, request_id)
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.save_request:
        request_file = args.save_request.expanduser().absolute()
        request_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with request_file.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
        except FileExistsError:
            print("请求文件已存在；为避免覆盖，未调用 Core。", file=sys.stderr)
            return 2
        input_file = str(request_file)
        input_payload = None
    else:
        input_file = "-"
        input_payload = payload

    preview = args.preview
    mode = "preview" if preview else "execute"
    host_mode = "execute" if args.capability in WRITE_CAPABILITIES and not preview else "readonly"
    returncode, response = _run_core(
        cli,
        args.config_root.expanduser().absolute(),
        ["invoke", "--file", input_file, "--mode", mode, "--host-mode", host_mode],
        payload=input_payload,
    )
    if response is not None:
        _print_json(f"Core invoke · {args.capability}", response)
    if args.save_request:
        print(f"原请求已保存：{args.save_request}")
    return returncode


def _full_demo(args: argparse.Namespace, cli: str) -> int:
    plugin_path = args.plugin_path.expanduser().absolute()
    if plugin_path.is_symlink() or not plugin_path.is_dir():
        print("--plugin-path 必须是包含 plugin.yaml 的锁定插件目录。", file=sys.stderr)
        return 2
    if not (plugin_path / "plugin.yaml").is_file() or not (plugin_path / "plugin.lock.json").is_file():
        print("插件目录缺少 plugin.yaml 或 plugin.lock.json；请先构建并锁定发行目录。", file=sys.stderr)
        return 2
    if args.config_root is not None:
        print("--plugin-path 全流程模式始终使用新临时 Core 根，不接受 --config-root。", file=sys.stderr)
        return 2
    store_id = args.store_id or "task-demo"
    setup_script = Path(__file__).with_name("setup_core.py")

    with tempfile.TemporaryDirectory(prefix="yushuos-task-demo-") as temporary_root:
        config_root = Path(temporary_root) / "core"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        setup = subprocess.run(
            [sys.executable, str(setup_script), "--config-root", str(config_root), "--store-id", store_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        if setup.stdout:
            sys.stdout.write(setup.stdout)
        if setup.stderr:
            sys.stderr.write(setup.stderr)
        if setup.returncode:
            return setup.returncode

        code, installed = _run_core(cli, config_root, ["install-plugin", "--path", str(plugin_path)])
        _print_json("Core install-plugin", installed if installed is not None else {"exit_code": code})
        if code != 0 or not installed or installed.get("status") != "succeeded":
            return code or 1

        request_id = f"task-demo-create-{uuid.uuid4().hex}"
        create_request = _request(
            "task.create",
            {
                "title": "Task Plugin CLI demo",
                "notes": "Created only in this temporary Core environment.",
                "priority": "normal",
                "tags": ["demo", "core-cli"],
            },
            store_id,
            request_id,
        )
        created = _invoke(cli, config_root, create_request)
        if not created or created.get("status") != "succeeded" or not isinstance(created.get("data", {}).get("task"), dict):
            return 1
        task = created["data"]["task"]

        listed = _invoke(
            cli,
            config_root,
            _request("task.list", {"limit": 20}, store_id, f"task-demo-list-{uuid.uuid4().hex}"),
        )
        if not listed or listed.get("status") != "succeeded":
            return 1

        updated = _invoke(
            cli,
            config_root,
            _request(
                "task.update",
                {"task_id": task["id"], "expected_version": task["version"], "changes": {"notes": "Updated through Core CLI."}},
                store_id,
                f"task-demo-update-{uuid.uuid4().hex}",
            ),
        )
        if not updated or updated.get("status") != "succeeded" or not isinstance(updated.get("data", {}).get("task"), dict):
            return 1
        updated_task = updated["data"]["task"]

        completed = _invoke(
            cli,
            config_root,
            _request(
                "task.complete",
                {"task_id": task["id"], "expected_version": updated_task["version"]},
                store_id,
                f"task-demo-complete-{uuid.uuid4().hex}",
            ),
        )
        if not completed or completed.get("status") != "succeeded":
            return 1

        code, status = _run_core(cli, config_root, ["status", "--request-id", request_id])
        _print_json("Core status", status if status is not None else {"exit_code": code})
        if code != 0 or status is None:
            return 1

        replayed = _invoke(cli, config_root, create_request)
        if not replayed or replayed.get("status") != "succeeded":
            return 1

        print("\n演示结束。Core 配置、Task 数据和共享台账均位于已删除的临时目录。")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cli = _find_cli(args.yushuos)
    if not cli:
        print("找不到 yushuos CLI；请激活安装了 Core 0.3.1 的环境，或通过 --yushuos 指定路径。", file=sys.stderr)
        return 127
    if args.plugin_path is not None:
        if args.capability is not None or args.save_request is not None or args.preview or args.request_id:
            print("--plugin-path 全流程模式不接受单次 capability/request 选项。", file=sys.stderr)
            return 2
        return _full_demo(args, cli)
    if args.capability is None:
        print("请使用 --plugin-path 运行隔离完整演示，或显式指定 --config-root、--store-id 和 --capability。", file=sys.stderr)
        return 2
    return _direct_invoke(args, cli)


if __name__ == "__main__":
    raise SystemExit(main())
