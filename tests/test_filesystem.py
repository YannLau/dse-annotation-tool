import subprocess
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from dse_tool.api import create_app
from dse_tool import filesystem


def test_directory_browser_lists_only_folders_and_preserves_paths(tmp_path):
    root = tmp_path / "数据集 根目录"
    root.mkdir()
    for name in ["Beta", "alpha", ".hidden", "文件夹 with spaces "]:
        (root / name).mkdir()
    (root / "video.mp4").write_bytes(b"not a directory")
    (root / "alias").symlink_to(root / "alpha", target_is_directory=True)
    result = filesystem.directories(str(root))
    assert [r["name"] for r in result["directories"]] == [
        "alias",
        "alpha",
        "Beta",
        "文件夹 with spaces ",
    ]
    assert result["path"] == str(root) and result["parent"] == str(tmp_path)
    assert len(filesystem.directories(str(root), show_hidden=True)["directories"]) == 5
    assert filesystem.directories(str(root / "alias"))["path"] == str(root / "alpha")
    assert filesystem.directories(str(root / "new" / "not-created"), initial=True)[
        "path"
    ] == str(root)
    assert not (root / "new").exists()
    with pytest.raises(ValueError, match="不存在"):
        filesystem.directories(str(root / "new"))
    with pytest.raises(ValueError):
        filesystem.directories(str(root / "video.mp4"))


def test_native_picker_selection_cancel_fallback_and_single_dialog(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(filesystem.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(filesystem.shutil, "which", lambda name: "/usr/bin/zenity")
    selected = tmp_path / "中文 folder ; $literal "
    selected.mkdir()
    calls = []

    def run(command, **kw):
        calls.append((command, kw))
        return SimpleNamespace(returncode=0, stdout=str(selected) + "\n", stderr="")

    monkeypatch.setattr(filesystem.subprocess, "run", run)
    assert filesystem.pick_directory(str(tmp_path / "not-created"), "inspect") == {
        "status": "selected",
        "path": str(selected),
    }
    command, options = calls[0]
    assert command[0] == "/usr/bin/zenity"
    assert "--filename=" + str(tmp_path) + "/" in command
    assert "导出" in next(arg for arg in command if arg.startswith("--title="))
    assert not options.get("shell") and options["timeout"] == 300
    assert not (tmp_path / "not-created").exists()
    # The native window follows the UI language without changing selected paths.
    assert filesystem.pick_directory(str(tmp_path), "inspect", "en")["path"] == str(
        selected
    )
    assert "--title=Select an exported dataset folder to inspect" in calls[-1][0]
    filesystem.pick_directory(str(tmp_path), "import", "en")
    assert "--title=Select a dataset folder to import" in calls[-1][0]

    monkeypatch.setattr(
        filesystem.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert filesystem.pick_directory()["status"] == "cancelled"
    monkeypatch.setattr(
        filesystem.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stdout="", stderr="Gtk-WARNING: cannot open display"
        ),
    )
    assert filesystem.pick_directory()["status"] == "unavailable"

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 300)

    monkeypatch.setattr(filesystem.subprocess, "run", timeout)
    assert filesystem.pick_directory()["status"] == "unavailable"
    # Lock is released after failures. It also prevents duplicate OS windows.
    assert filesystem._picker_lock.acquire(blocking=False)
    try:
        assert filesystem.pick_directory()["status"] == "busy"
    finally:
        filesystem._picker_lock.release()
    monkeypatch.setattr(filesystem.shutil, "which", lambda name: None)
    monkeypatch.setattr(filesystem.subprocess, "run", run)
    assert filesystem.pick_directory()["status"] == "selected"
    assert calls[-1][0][:3] == [filesystem.sys.executable, "-m", "dse_tool.filesystem"]
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert filesystem.pick_directory()["status"] == "unavailable"


def test_directory_api_is_read_only_and_validates_access(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "meta").mkdir()
    (source / "unchanged.txt").write_text("original")
    app = create_app(tmp_path / "work")
    snapshot = app.state.store.snapshot()
    revision = app.state.store.get("revision")
    with TestClient(app) as client:
        result = client.get("/api/filesystem/directories", params={"path": str(source)})
        assert result.status_code == 200
        assert result.json()["directories"] == [
            {"name": "meta", "path": str(source / "meta")}
        ]
        assert (
            client.get(
                "/api/filesystem/directories", params={"path": str(source / "missing")}
            ).status_code
            == 400
        )
        monkeypatch.setattr(
            "dse_tool.api.pick_directory",
            lambda path, purpose, language: {
                "status": "selected",
                "path": str(source),
                "language": language,
            },
        )
        selected = client.post(
            "/api/filesystem/pick-directory",
            json={"initial_path": "", "purpose": "inspect"},
        )
        assert selected.json()["path"] == str(source)
        assert selected.json()["language"] == "zh-CN"
        english = client.post(
            "/api/filesystem/pick-directory",
            json={"initial_path": str(source), "purpose": "inspect", "language": "en"},
        )
        assert english.json()["path"] == str(source)
        assert english.json()["language"] == "en"
        assert (
            client.post(
                "/api/filesystem/pick-directory", json={"language": "invalid"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/filesystem/pick-directory", json={"purpose": "invalid"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/filesystem/pick-directory",
                json={},
                headers={"origin": "https://example.com"},
            ).status_code
            == 403
        )
        monkeypatch.setattr(
            filesystem.os,
            "scandir",
            lambda path: (_ for _ in ()).throw(PermissionError()),
        )
        assert (
            client.get(
                "/api/filesystem/directories", params={"path": str(source)}
            ).status_code
            == 400
        )
    assert app.state.store.snapshot() == snapshot
    assert app.state.store.get("revision") == revision
    assert (source / "unchanged.txt").read_text() == "original"
