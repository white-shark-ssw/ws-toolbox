import pytest

from ws_git_sync.models import Operation


@pytest.mark.parametrize("normalization", ["autocrlf", "attributes"])
def test_git_line_ending_normalization_does_not_fail_staging(project, normalization):
    p = project
    if normalization == "autocrlf":
        p.command(p.repo, "config", "core.autocrlf", "true")
    else:
        p.put(p.repo, ".gitattributes", "*.lua text eol=lf\n")
    relative = p.target + "/Client/GUILayout/form/换行.lua"
    p.put(p.repo, relative, b"return 1\n")
    p.commit()
    source = p.put(p.client, "form/换行.lua", b"return 1\r\n")
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    head = p.git.head()
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert result.commit_id == "" and p.git.head() == head
    assert p.git.status() == b""
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_normalized_file_can_mix_with_real_add_modify_and_delete(project):
    p = project
    p.put(p.repo, ".gitattributes", "*.lua text eol=lf\n")
    base = p.target + "/Client/GUILayout/form/"
    for name in ("eol.lua", "changed.lua", "gone.lua"):
        p.put(p.repo, base + name, b"return 1\n")
    p.commit()
    p.put(p.client, "form/eol.lua", b"return 1\r\n")
    p.put(p.client, "form/changed.lua", b"return 2\r\n")
    p.put(p.client, "form/new.lua", b"return 3\r\n")
    before = {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in (p.client / "form").iterdir()}
    head = p.git.head()
    result, events = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED", result.message
    assert result.commit_id and result.uploaded
    assert dict(result.changes) == {"add": 1, "modify": 1, "delete": 1}
    assert any(e.code == "normalized" for e in events)
    changed = p.git.run("diff", "--name-only", "--no-renames", "-z", head, result.commit_id).stdout
    assert set(changed.decode().strip("\0").split("\0")) == {
        base + n for n in ("changed.lua", "gone.lua", "new.lua")
    }
    assert {f.name: (f.read_bytes(), f.stat().st_mtime_ns) for f in (p.client / "form").iterdir()} == before
    assert p.git.status() == b""


@pytest.mark.parametrize("existing", [False, True])
def test_real_missing_staged_path_still_fails_with_filename(project, monkeypatch, existing):
    p = project
    relative = p.target + "/Client/GUILayout/form/missing.lua"
    if existing:
        p.put(p.repo, relative, b"old")
        p.commit()
    p.put(p.client, "form/missing.lua", b"new")
    head = p.git.head()
    monkeypatch.setattr(p.git, "stage", lambda paths: None)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "STAGE_MISMATCH" and relative in result.message
    assert not result.commit_id and p.git.head() == head


def test_foreign_staged_path_is_rejected_even_with_normalized_plan(project, monkeypatch):
    p = project
    p.put(p.repo, ".gitattributes", "*.lua text eol=lf\n")
    relative = p.target + "/Client/GUILayout/form/eol.lua"
    p.put(p.repo, relative, b"return 1\n")
    p.commit()
    p.put(p.client, "form/eol.lua", b"return 1\r\n")
    head = p.git.head()
    original = p.git.stage

    def stage_with_foreign(paths):
        original(paths)
        p.put(p.repo, "foreign.txt", b"unrelated")
        original(("foreign.txt",))

    monkeypatch.setattr(p.git, "stage", stage_with_foreign)
    result, _ = p.run(Operation.SYNC)
    assert result.error_code == "STAGE_MISMATCH" and "foreign.txt" in result.message
    assert not result.commit_id and p.git.head() == head
