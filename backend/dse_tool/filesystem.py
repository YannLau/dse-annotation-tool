"""Read-only directory browsing and a local desktop folder picker."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading


_picker_lock = threading.Lock()


def starting_directory(path=""):
    candidate = Path(path).expanduser() if path else Path.home()
    candidate = candidate.resolve()
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else Path.home()


def directories(path="", show_hidden=False, initial=False):
    root = (
        starting_directory(path)
        if initial or not path
        else Path(path).expanduser().resolve()
    )
    if not root.is_dir():
        raise ValueError("目录不存在或无法访问，请选择已有文件夹")
    children = []
    skipped = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir():
                        children.append(
                            {"name": entry.name, "path": str(root / entry.name)}
                        )
                except OSError:
                    skipped += 1
    except OSError as exc:
        raise ValueError("无法读取此目录，请检查访问权限") from exc
    return {
        "path": str(root),
        "parent": str(root.parent) if root.parent != root else None,
        "home": str(Path.home()),
        "directories": sorted(
            children, key=lambda row: (row["name"].casefold(), row["name"])
        ),
        "skipped": skipped,
    }


def pick_directory(initial_path="", purpose="import"):
    if not _picker_lock.acquire(blocking=False):
        return {
            "status": "busy",
            "message": "已有文件夹选择器打开，请先完成或取消该选择",
        }
    try:
        if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        ):
            return {
                "status": "unavailable",
                "message": "服务没有桌面环境，请在页面内浏览文件夹",
            }
        start = starting_directory(initial_path)
        title = (
            "选择导入的数据集文件夹"
            if purpose == "import"
            else "选择要检查的导出数据集文件夹"
        )
        zenity = shutil.which("zenity") if sys.platform.startswith("linux") else None
        if zenity:
            command = [
                zenity,
                "--file-selection",
                "--directory",
                "--title=" + title,
                "--filename=" + str(start) + os.sep,
            ]
        else:
            # Tk must run on its own main thread, not a FastAPI worker thread.
            command = [sys.executable, "-m", "dse_tool.filesystem", str(start), title]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "status": "unavailable",
                "message": "系统选择器未能打开或已超时，请在页面内浏览文件夹",
            }
        if (
            zenity
            and result.returncode == 1
            and not any(
                marker in result.stderr.lower()
                for marker in ("cannot open display", "failed to open display")
            )
        ):
            return {"status": "cancelled"}
        if result.returncode != 0:
            return {
                "status": "unavailable",
                "message": "无法使用系统选择器，请在页面内浏览文件夹",
            }
        # Remove the protocol newline only; spaces in folder names are significant.
        selected = result.stdout.removesuffix("\n")
        if not selected:
            return {"status": "cancelled"}
        root = Path(selected).expanduser().resolve()
        if not root.is_dir():
            return {
                "status": "unavailable",
                "message": "选择的文件夹已不可访问，请重新选择",
            }
        return {"status": "selected", "path": str(root)}
    finally:
        _picker_lock.release()


def main():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root, initialdir=sys.argv[1], title=sys.argv[2], mustexist=True
        )
        print(selected)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
