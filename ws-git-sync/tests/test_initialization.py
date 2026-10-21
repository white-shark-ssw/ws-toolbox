from dataclasses import replace

import pytest

from ws_git_sync.git_ops import Git
from ws_git_sync.models import Operation, SyncError, TaskRequest
from ws_git_sync.tasks import run_task


def initialize(project, root, url=None):
    settings = replace(project.settings, repo=str(root), clone_url=str(url or project.remote))
    return run_task(
        TaskRequest("init", Operation.INITIALIZE, settings, project.profile), Git(root), lambda e: None
    )


def test_empty_directory_initializes_from_remote_and_is_clean(project):
    root = project.repo.parent / "新 Git 目录"
    result = initialize(project, root)
    assert result.status == "SUCCEEDED", result.message
    Git(root).check("main")
    assert (root / "README.md").read_bytes() == (project.repo / "README.md").read_bytes()
    assert Git(root).text("remote", "get-url", "origin") == str(project.remote)
    assert Git(root).text("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") == "origin/main"


def test_nonempty_initialization_only_adds_metadata_and_keeps_changes(project):
    root = project.repo.parent / "下载 ZIP 的目录"
    root.mkdir()
    file = root / "README.md"
    file.write_bytes(b"local custom content")
    stamp = file.stat().st_mtime_ns
    (root / "mine.txt").write_bytes(b"mine")
    result = initialize(project, root)
    assert result.status == "SUCCEEDED", result.message
    assert file.read_bytes() == b"local custom content" and file.stat().st_mtime_ns == stamp
    assert (root / "mine.txt").read_bytes() == b"mine"
    assert not (root / "source").exists()
    with pytest.raises(SyncError) as error:
        Git(root).check("main")
    assert error.value.code == "DIRTY_REPO"


def test_download_failure_does_not_touch_existing_files_or_leave_git_metadata(project):
    root = project.repo.parent / "保留目录"
    root.mkdir()
    (root / "keep.txt").write_bytes(b"keep")
    result = initialize(project, root, project.repo.parent / "missing.git")
    assert result.status == "FAILED"
    assert sorted(p.name for p in root.iterdir()) == ["keep.txt"]
    assert (root / "keep.txt").read_bytes() == b"keep"


def test_existing_or_damaged_git_metadata_is_not_reinitialized(project):
    result = initialize(project, project.repo)
    assert result.error_code == "REPO_EXISTS"
    root = project.repo.parent / "损坏仓库"
    root.mkdir()
    (root / ".git").write_text("damaged metadata")
    with pytest.raises(SyncError) as error:
        Git(root).check("main")
    assert error.value.code == "REPO_ROOT"
    result = initialize(project, root)
    assert result.error_code == "REPO_EXISTS" and (root / ".git").read_text() == "damaged metadata"


def test_nested_git_directory_does_not_offer_initialization(project):
    root = project.repo / "subdir"
    root.mkdir()
    with pytest.raises(SyncError) as error:
        Git(root).check("main")
    assert error.value.code == "REPO_ROOT"
    assert initialize(project, root).error_code == "REPO_ROOT"
    assert initialize(project, root / "not-created").error_code == "REPO_ROOT"
    assert not (root / "not-created").exists()


def test_initialization_refuses_all_configured_source_directories(project):
    result = initialize(project, project.server)
    assert result.error_code == "OVERLAPPING_ROOTS"
    assert not (project.server / ".git").exists()


@pytest.mark.parametrize("operation", [Operation.PREVIEW, Operation.SYNC, Operation.PULL, Operation.PUSH])
def test_any_operation_recognizes_uninitialized_directory(project, operation):
    root = project.repo.parent / "未初始化"
    root.mkdir()
    settings = replace(project.settings, repo=str(root))
    result = run_task(TaskRequest("try", operation, settings, project.profile), Git(root), lambda e: None)
    assert result.error_code == "REPO_UNINITIALIZED"
    assert not (root / ".git").exists()
