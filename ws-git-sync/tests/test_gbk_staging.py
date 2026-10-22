import pytest

from ws_git_sync.models import Operation


@pytest.mark.parametrize("autocrlf", ["false", "true", "input", "attributes-auto"])
@pytest.mark.parametrize("head_eol", ["\n", "\r\n"])
@pytest.mark.parametrize("source_eol", ["\n", "\r\n"])
def test_gbk_server_stage_matches_snapshot_with_index_eol_history(project, autocrlf, head_eol, source_eol):
    p = project
    relative = p.target + "/MirServer/Mir200/Envir/Market_Def/QFunction-0.lua"
    p.put(p.repo, relative, ("-- 旧内容" + head_eol + "return 1" + head_eol).encode("utf-8"))
    p.commit()
    if autocrlf == "attributes-auto":
        p.put(p.repo, ".gitattributes", "*.lua text=auto\n")
        p.commit()
    else:
        p.command(p.repo, "config", "core.autocrlf", autocrlf)
    source = p.put(
        p.server,
        "Mir200/Envir/Market_Def/QFunction-0.lua",
        ("-- 新内容" + source_eol + "return 2" + source_eol).encode("gbk"),
    )
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert result.commit_id and result.uploaded
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_empty_initialization_then_syncs_40_gbk_files_with_crlf_history(project, monkeypatch):
    from dataclasses import replace

    from ws_git_sync.git_ops import Git
    from ws_git_sync.models import TaskRequest
    from ws_git_sync.tasks import run_task

    p = project
    relative = p.target + "/MirServer/Mir200/Envir/Market_Def/"
    sources = []
    for number in range(40):
        name = f"QFunction-{number}.lua"
        p.put(p.repo, relative + name, "-- 原始内容\r\nreturn 1\r\n".encode("utf-8"))
        sources.append(
            p.put(p.server, "Mir200/Envir/Market_Def/" + name, "-- 真实内容\r\nreturn 2\r\n".encode("gbk"))
        )
    p.commit()
    # 用环境配置模拟Windows全局autocrlf，覆盖clone检出及首次同步的完整链路。
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    root = p.repo.parent / "全新空目录"
    root.mkdir()
    git = Git(root)
    assert git.initialize(str(p.remote), "origin", "main") is False
    p.command(root, "config", "user.name", "Sync Test")
    p.command(root, "config", "user.email", "sync@example.invalid")
    before = [(f.read_bytes(), f.stat().st_mtime_ns) for f in sources]
    settings = replace(p.settings, repo=str(root))
    result = run_task(TaskRequest("first-sync", Operation.SYNC, settings, p.profile), git, lambda event: None)
    assert result.status == "SUCCEEDED", result.message
    assert result.commit_id and result.uploaded and len(result.applied) == 40
    assert dict(result.changes) == {"modify": 40}
    assert git.status() == b""
    for number, source in enumerate(sources):
        assert git.blob(result.commit_id, relative + f"QFunction-{number}.lua") == source.read_bytes()
    assert [(f.read_bytes(), f.stat().st_mtime_ns) for f in sources] == before


def test_tampered_staged_gbk_blob_still_fails_snapshot_validation(project, monkeypatch):
    p = project
    relative = p.target + "/MirServer/Mir200/Envir/Market_Def/QFunction-0.lua"
    p.put(p.repo, relative, "-- 原始内容\r\n".encode("utf-8"))
    p.commit()
    p.command(p.repo, "config", "core.autocrlf", "true")
    source = p.put(p.server, "Mir200/Envir/Market_Def/QFunction-0.lua", "-- 真实内容\r\n".encode("gbk"))
    original = p.git.stage
    head = p.git.head()

    def tamper(paths):
        original(paths)
        blob = p.git.run("hash-object", "-w", "--stdin", data=b"tampered").stdout.decode().strip()
        p.git.run("update-index", "--cacheinfo", "100644," + blob + "," + relative)

    monkeypatch.setattr(p.git, "stage", tamper)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "STAGE_CONTENT" and relative in result.message
    assert p.git.head() == head and not result.commit_id and not result.uploaded
    assert source.read_bytes() == "-- 真实内容\r\n".encode("gbk")
