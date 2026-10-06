from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugin"
_IGNORED_PARTS = {"__pycache__", ".pytest_cache"}
_IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd"}


def _files_to_package() -> list[Path]:
    if PLUGIN_ROOT.is_symlink() or not PLUGIN_ROOT.is_dir():
        raise ValueError("plugin/ 必须是普通目录")
    files: list[Path] = []
    for path in PLUGIN_ROOT.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"插件发行目录不允许符号链接：{path.relative_to(PLUGIN_ROOT)}")
        relative = path.relative_to(PLUGIN_ROOT)
        if set(relative.parts) & _IGNORED_PARTS or path.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        if path.is_file() and path.name != ".DS_Store":
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(PLUGIN_ROOT).as_posix())


def _core_source_path(raw_path: str | None) -> Path | None:
    if raw_path is None:
        return None
    path = Path(raw_path).expanduser().resolve()
    if (path / "yushuos").is_dir():
        return path
    if (path / "src" / "yushuos").is_dir():
        return path / "src"
    raise ValueError("--core-path 必须指向包含 yushuos/ 或 src/yushuos/ 的 Core 源码目录")


def _lock_plugin(core_path: Path | None) -> None:
    if core_path is not None and str(core_path) not in sys.path:
        sys.path.insert(0, str(core_path))
    try:
        from yushuos.deployment import lock_plugin
    except ImportError as exc:
        raise RuntimeError("无法导入 YushuOS Core；传入 --core-path 或在当前 Python 安装 Core 0.3.1") from exc
    result = lock_plugin(PLUGIN_ROOT)
    if result.get("status") != "succeeded" or result.get("verified") is not True:
        raise RuntimeError(f"Core 未能验证插件锁：{result}")
    # Core writes JSON with the host newline convention. Keep the lock itself
    # portable too, so Git checkout and deterministic ZIP builds use LF bytes.
    lock_path = PLUGIN_ROOT / "plugin.lock.json"
    lock_path.write_bytes(lock_path.read_bytes().replace(b"\r\n", b"\n"))


def _write_zip(output: Path, files: list[Path]) -> None:
    output = output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("zip 输出目标必须是普通文件")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-", suffix=".tmp", dir=output.parent, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
        with ZipFile(temp_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            directory = ZipInfo("plugin/")
            directory.date_time = (1980, 1, 1, 0, 0, 0)
            directory.external_attr = 0o40755 << 16
            directory.create_system = 3
            archive.writestr(directory, b"")
            for path in files:
                relative = PurePosixPath("plugin", path.relative_to(PLUGIN_ROOT).as_posix()).as_posix()
                info = ZipInfo(relative)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)

        with ZipFile(temp_path, "r") as archive:
            names = set(archive.namelist())
            expected = {
                "plugin/" + path.relative_to(PLUGIN_ROOT).as_posix()
                for path in files
            } | {"plugin/"}
            if names != expected:
                raise RuntimeError("zip 内容与过滤后的 plugin/ 文件清单不一致")
            if "plugin/plugin.lock.json" not in names:
                raise RuntimeError("zip 缺少 Core 生成的 plugin.lock.json")
        os.replace(temp_path, output)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成 Task 发行包：render manifest、stage source、Core lock、过滤 ZIP"
    )
    parser.add_argument(
        "--core-path",
        default=os.environ.get("YUSHUOS_CORE_PATH"),
        help="Core 0.3.1 源码根目录；也可通过 YUSHUOS_CORE_PATH 设置",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "dist" / "yushuos-task-0.2.0.zip"),
        help="输出 zip 路径，默认 dist/yushuos-task-0.2.0.zip",
    )
    args = parser.parse_args()

    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "render_manifest.py")], check=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "stage_plugin.py")], check=True)
        _lock_plugin(_core_source_path(args.core_path))
        from yushuos.manifest import load_manifest

        spec = load_manifest(PLUGIN_ROOT / "plugin.yaml")
        if not spec.package_hash_verified:
            raise RuntimeError("Core 无法验证 Task plugin.lock.json")
        files = _files_to_package()
        required = {"plugin.yaml", "plugin.lock.json", "run.py"}
        names = {path.relative_to(PLUGIN_ROOT).as_posix() for path in files}
        if not required <= names:
            raise ValueError(f"发行目录缺少必要文件：{sorted(required - names)}")
        _write_zip(Path(args.output), files)
        digest = hashlib.sha256(Path(args.output).expanduser().absolute().read_bytes()).hexdigest()
        print(f"已生成 Task 插件包：{Path(args.output).expanduser().absolute()} sha256={digest}")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Task 插件打包失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
