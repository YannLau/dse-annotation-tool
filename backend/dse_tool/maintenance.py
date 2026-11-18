"""Compact legacy project databases and their SQLite recovery points offline."""

import argparse
import sqlite3
from pathlib import Path
from uuid import UUID

from .storage import backup_paths, compact_database, workspace_lock


def workspace_databases(root):
    projects = [root]
    schemes = root / "schemes"
    if schemes.is_dir() and not schemes.is_symlink():
        for directory in sorted(schemes.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                scheme_id = UUID(directory.name)
            except ValueError:
                continue
            if str(scheme_id) == directory.name:
                projects.append(directory)
    for project in projects:
        backups = project / "backups"
        files = [project / "project.sqlite3"]
        if not backups.is_symlink():
            files.extend(backup_paths(backups))
        yield from (path for path in files if path.is_file() and not path.is_symlink())


def main():
    parser = argparse.ArgumentParser(
        description="压缩标注工作库及其备份，保留全部撤销历史"
    )
    parser.add_argument(
        "--work", type=Path, required=True, help="工作区目录（与服务的 --work 一致）"
    )
    args = parser.parse_args()
    if not (args.work.expanduser() / "project.sqlite3").is_file():
        parser.exit(1, "指定目录缺少 project.sqlite3\n")
    try:
        with workspace_lock(args.work.expanduser(), maintenance=True) as root:
            total_before = total_after = 0
            for path in workspace_databases(root):
                print(path.relative_to(root), flush=True)
                before, after = compact_database(path)
                total_before += before
                total_after += after
                print(
                    f"  {before / 2**20:,.2f} MiB → {after / 2**20:,.2f} MiB",
                    flush=True,
                )
            print(
                f"合计 {total_before / 2**20:,.2f} MiB → {total_after / 2**20:,.2f} MiB"
            )
    except (sqlite3.Error, OSError, ValueError) as exc:
        parser.exit(1, f"压缩中止，已发布的副本可以正常使用。{exc}\n")


if __name__ == "__main__":
    main()
