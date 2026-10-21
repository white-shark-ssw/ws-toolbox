from dataclasses import replace

import pytest

from ws_git_sync.models import Operation
from ws_git_sync.source_sync import same_server, source_text


def test_preview_then_sync_is_scoped_repeatable_and_readonly(project):
    p = project
    content = "return '新增中文'\r\n".encode("gbk")
    p.put(p.server, "Mir200/Envir/ws/新增.lua", content)
    p.put(p.client, "form/a.lua", b"return 1\n")
    other = p.put(p.repo, "source/版本乙/工程源码/Client/GUILayout/form/keep.lua", b"other")
    p.commit()
    original = p.git.head()
    preview, events = p.run()
    assert preview.status == "SUCCEEDED", preview.message
    assert dict(preview.changes) == {"add": 2}
    assert p.git.head() == original and p.git.status() == b""
    assert not (p.repo / p.target / "MirServer").exists()
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED" and result.uploaded and result.commit_id
    assert (p.repo / p.target / "MirServer/Mir200/Envir/ws/新增.lua").read_bytes() == content
    assert (p.server / "Mir200/Envir/ws/新增.lua").read_bytes() == content
    assert other.read_bytes() == b"other"
    assert p.git.status() == b""
    again, _ = p.run(Operation.SYNC)
    assert again.status == "SUCCEEDED" and again.commit_id == "" and not again.changes


def test_sync_uses_real_sources_after_pull_without_writing_back_remote_files(project):
    p = project
    client_relative = p.target + "/Client/GUILayout/form/a.lua"
    server_relative = p.target + "/MirServer/Mir200/Envir/ws/a.lua"
    cloud_only = p.target + "/Client/GUILayout/form/cloud-only.lua"
    p.put(p.repo, client_relative, b"initial")
    p.put(p.repo, server_relative, "初始内容\n")
    p.commit()
    p.put(p.client, "form/a.lua", b"local authoritative source")
    p.put(p.server, "Mir200/Envir/ws/a.lua", "本地真实工程\r\n".encode("gbk"))
    p.put(p.server, "Mir200/Envir/Data/local.xls", bytes(range(256)))

    def snapshot():
        return {
            (side, f.relative_to(root).as_posix()): (f.read_bytes(), f.stat().st_mtime_ns)
            for side, root in (("server", p.server), ("client", p.client))
            for f in root.rglob("*")
            if f.is_file()
        }

    before = snapshot()
    other = p.clone()
    p.put(other, client_relative, b"remote edit")
    p.put(other, server_relative, "云端变更\n")
    p.put(other, cloud_only, b"remote only")
    p.put(other, "cloud-document.md", b"keep repository document")
    p.command(other, "add", ".")
    p.command(other, "commit", "-m", "remote edits")
    p.command(other, "push")
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert snapshot() == before
    assert (p.repo / client_relative).read_bytes() == b"local authoritative source"
    assert (p.repo / server_relative).read_bytes() == "本地真实工程\r\n".encode("gbk")
    assert not (p.repo / cloud_only).exists()
    assert (p.repo / "cloud-document.md").read_bytes() == b"keep repository document"


def test_gbk_utf8_comparison_eol_and_case(project):
    p = project
    p.put(p.server, "Mir200/Envir/ws/a.lua", "return '中文A'\r\n".encode("gbk"))
    p.put(p.repo, p.target + "/MirServer/Mir200/Envir/ws/a.lua", "return '中文A'\n")
    p.commit()
    result, _ = p.run()
    assert result.status == "SUCCEEDED" and not result.changes
    p.put(p.server, "Mir200/Envir/ws/a.lua", "return '中文a'\r\n".encode("gbk"))
    result, _ = p.run()
    assert dict(result.changes) == {"modify": 1}


def test_delete_exact_ignores_generated_and_untracked_scope(project):
    p = project
    for relative, data in (
        ("MirServer/Mir200/Envir/ws/gone.lua", b"gone"),
        ("MirServer/Mir200/Envir/ws/KEEP.LUA", b"ignored"),
        ("MirServer/Mir200/Envir/Data/x.tsv", b"derived"),
        ("MirServer/Mir200/Envir/Data/镜像状态.json", b"{}"),
    ):
        p.put(p.repo, p.target + "/" + relative, data)
    p.commit()
    p.put(p.client, "root_not_in_scope.lua", b"excluded")
    p.put(p.server, "Mir200/Envir/ws/keep.lua", b"changed ignored")
    p.put(p.server, "Mir200/Envir/Data/ignored.xls", b"exclude")
    p.put(p.server, "Mir200/Envir/Data/nested/no.xls", b"nested")
    p.put(p.server, "Mir200/Envir/Data/new.xls", b"\xd0\xcf\x11\xe0\x00\x80")
    cfg = replace(p.settings, ignore_file_names=("keep.lua", "ignored.xls"))
    result, _ = p.run(Operation.SYNC, cfg)
    assert result.status == "SUCCEEDED", result.message
    assert dict(result.changes) == {"add": 1, "delete": 1}
    assert (p.repo / p.target / "MirServer/Mir200/Envir/ws/KEEP.LUA").read_bytes() == b"ignored"
    assert (p.repo / p.target / "MirServer/Mir200/Envir/Data/x.tsv").read_bytes() == b"derived"
    assert not (p.repo / p.target / "Client/GUILayout/root_not_in_scope.lua").exists()


def test_tracked_outside_new_whitelist_updates(project):
    p = project
    p.put(p.repo, p.target + "/Client/GUILayout/root.lua", b"old")
    p.commit()
    p.put(p.client, "root.lua", b"new")
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED" and dict(result.changes) == {"modify": 1}


def test_saved_exact_filename_filters_skip_add_modify_delete_case_insensitively(project, tmp_path):
    from ws_git_sync.settings import SettingsStore

    p = project
    for filename in ("keep.lua", "gone.lua"):
        p.put(p.repo, p.target + "/Client/GUILayout/form/" + filename, b"old")
    p.commit()
    p.put(p.client, "form/keep.lua", b"changed")
    p.put(p.client, "form/new.lua", b"new ignored")
    p.put(p.client, "form/keep-other.lua", b"must be included")
    cfg = replace(p.settings, ignore_file_names=("KEEP.LUA", "Gone.Lua", "new.lua"))
    store = SettingsStore(tmp_path / "settings.json")
    store.save(cfg)
    result, _ = p.run(Operation.SYNC, store.load())
    assert result.status == "SUCCEEDED", result.message
    for filename in ("keep.lua", "gone.lua"):
        assert (p.repo / p.target / "Client/GUILayout/form" / filename).read_bytes() == b"old"
    assert not (p.repo / p.target / "Client/GUILayout/form/new.lua").exists()
    assert (p.repo / p.target / "Client/GUILayout/form/keep-other.lua").read_bytes() == b"must be included"


def test_literal_pathspec_special_characters(project):
    p = project
    p.put(p.client, "form/[x] 空格.lua", b"literal")
    p.put(p.client, "form/-name.lua", b"dash")
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert dict(result.changes) == {"add": 2}


def test_version_rename_after_pull_does_not_recreate_old_directory(project):
    p = project
    other = p.clone()
    p.command(other, "mv", "source/版本甲", "source/新名称")
    p.command(other, "commit", "-m", "rename version")
    p.command(other, "push")
    p.put(p.client, "form/new.lua", b"new")
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert not (p.repo / "source/版本甲").exists()
    assert (p.repo / "source/新名称/工程源码/Client/GUILayout/form/new.lua").read_bytes() == b"new"


def test_unknown_version_stops_without_creating_identity(project):
    p = project
    (p.repo / "source/版本甲/版本信息.json").unlink()
    p.commit()
    result, _ = p.run()
    assert result.status == "FAILED" and result.error_code == "VERSION_RESOLVE"
    assert p.git.status() == b""


def test_source_missing_does_not_delete_tracked_tree(project):
    p = project
    p.put(p.repo, p.target + "/MirServer/Mir200/Envir/ws/a.lua", b"keep")
    p.commit()
    p.shutil.rmtree(p.server)
    result, _ = p.run(Operation.SYNC)
    assert result.status == "FAILED" and result.error_code == "SOURCE_ROOT"
    assert p.git.status() == b""


def test_unreadable_source_is_not_deletion(project, monkeypatch):
    p = project
    p.put(p.repo, p.target + "/Client/GUILayout/form/a.lua", b"old")
    file = p.put(p.client, "form/a.lua", b"new")
    p.commit()
    import ws_git_sync.source_sync as sync

    original = sync.read_shared
    monkeypatch.setattr(
        sync,
        "read_shared",
        lambda path: (_ for _ in ()).throw(PermissionError("locked")) if path == file else original(path),
    )
    monkeypatch.setattr(sync.time, "sleep", lambda _: None)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "SOURCE_READ" and not result.applied
    assert p.git.status() == b""


def test_dirty_index_or_worktree_stops_sync(project):
    p = project
    p.put(p.repo, "user.txt", b"mine")
    p.command(p.repo, "add", "user.txt")
    before = p.git.head()
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "DIRTY_REPO" and p.git.head() == before
    assert p.git.staged_paths() == {"user.txt"}


def test_symlink_source_or_target_rejected(project):
    p = project
    link = p.client / "form/link.lua"
    try:
        link.symlink_to(p.repo / "README.md")
    except OSError:
        pytest.skip("symlink permission unavailable")
    result, _ = p.run()
    assert result.error_code == "LINK_PATH" and p.git.status() == b""


def test_source_changes_after_scan_stops_before_apply(project, monkeypatch):
    p = project
    file = p.put(p.client, "form/a.lua", b"new")
    import ws_git_sync.tasks as tasks

    original = tasks.verify_sources

    def changed(plan, ignored):
        file.write_bytes(b"saved while scanning")
        original(plan, ignored)

    monkeypatch.setattr(tasks, "verify_sources", changed)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "SOURCE_CHANGED" and not result.applied
    assert p.git.status() == b""


@pytest.mark.parametrize("content", [b"\xef\xbb\xbfreturn 1", b"\xff"])
def test_server_invalid_encoding_is_explicit(project, content):
    p = project
    p.put(p.server, "Mir200/Envir/ws/a.lua", content)
    result, _ = p.run()
    assert result.error_code == "SOURCE_ENCODING"


def test_same_server_ascii_and_both_legacy_encodings(tmp_path):
    path = tmp_path / "source.lua"
    assert same_server(b"A\r\n", b"A\n", path)
    raw = "测试中文\r\n".encode("gbk")
    assert same_server(raw, "测试中文\n".encode(), path)
    assert same_server(raw, "测试中文\n".encode("gbk"), path)
    assert source_text(raw, path) == "测试中文\n"


def test_overlap_rejected_before_pull(project, monkeypatch):
    p = project
    from ws_git_sync.models import TaskRequest
    from ws_git_sync.tasks import run_task

    profile = replace(p.profile, server=str(p.repo))
    monkeypatch.setattr(p.git, "pull", lambda *args: pytest.fail("不能在确认路径前 pull"))
    result = run_task(TaskRequest("overlap", Operation.SYNC, p.settings, profile), p.git, lambda e: None)
    assert result.error_code == "OVERLAPPING_ROOTS"


def test_partial_apply_is_reported_without_committing_or_overwriting_external_file(project):
    p = project
    from ws_git_sync.models import TaskRequest
    from ws_git_sync.tasks import run_task

    p.put(p.client, "form/a.lua", b"source-a")
    p.put(p.client, "form/b.lua", b"source-b")
    original = p.git.head()

    def interfere(event):
        if event.code == "applied" and event.path.endswith("a.lua"):
            p.put(p.repo, p.target + "/Client/GUILayout/form/b.lua", b"external-change")

    result = run_task(TaskRequest("partial", Operation.SYNC, p.settings, p.profile), p.git, interfere)
    assert result.error_code == "TARGET_CHANGED" and len(result.applied) == 1
    assert p.git.head() == original and not result.commit_id
    assert (p.repo / p.target / "Client/GUILayout/form/b.lua").read_bytes() == b"external-change"
    assert (p.client / "form/b.lua").read_bytes() == b"source-b"


def test_large_binary_snapshot_uses_spool_and_remains_exact(project):
    p = project
    data = bytes(range(256)) * 5000
    p.put(p.server, "Mir200/Envir/Data/a.xls", data)
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert (p.repo / p.target / "MirServer/Mir200/Envir/Data/a.xls").read_bytes() == data


def test_hidden_index_flags_are_rejected(project):
    p = project
    p.command(p.repo, "update-index", "--assume-unchanged", "README.md")
    p.put(p.repo, "README.md", b"invisible change")
    result, _ = p.run()
    assert result.error_code == "HIDDEN_INDEX"


def test_commit_hook_cannot_silently_change_content_and_push(project):
    import shlex

    p = project
    p.put(p.client, "form/a.lua", b"source")
    relative = p.target + "/Client/GUILayout/form/a.lua"
    hook = p.repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'tampered' > "
        + shlex.quote(relative)
        + "\ngit --literal-pathspecs add -- "
        + shlex.quote(relative)
        + "\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    remote_head = p.git.head()
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "COMMIT_CONTENT", result.message
    assert result.commit_id and not result.uploaded
    assert (p.client / "form/a.lua").read_bytes() == b"source"
    assert (
        p.command(p.repo, "ls-remote", "origin", "refs/heads/main").stdout.decode().split()[0] == remote_head
    )


def test_source_inventory_changes_mid_scan_are_rejected(project, monkeypatch):
    p = project
    p.put(p.client, "form/a.lua", b"a")
    import ws_git_sync.source_sync as sync

    original = sync.stable_read
    changed = False

    def during_read(path, *args, **kwargs):
        nonlocal changed
        data = original(path, *args, **kwargs)
        if path.is_relative_to(p.client) and not changed:
            changed = True
            p.put(p.client, "form/b.lua", b"b")
        return data

    monkeypatch.setattr(sync, "stable_read", during_read)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "SOURCE_CHANGED" and p.git.status() == b""


def test_deleted_target_recreated_before_staging_is_not_committed(project):
    from ws_git_sync.models import TaskRequest
    from ws_git_sync.tasks import run_task

    p = project
    relative = p.target + "/Client/GUILayout/form/gone.lua"
    p.put(p.repo, relative, b"old")
    p.commit()
    head = p.git.head()

    def interfere(event):
        if event.code == "stage" and event.stage == "STAGING":
            p.put(p.repo, relative, b"external-recreated")

    result = run_task(TaskRequest("delete-race", Operation.SYNC, p.settings, p.profile), p.git, interfere)
    assert result.error_code == "STAGE_CONTENT" and not result.commit_id
    assert p.git.head() == head and (p.repo / relative).read_bytes() == b"external-recreated"
