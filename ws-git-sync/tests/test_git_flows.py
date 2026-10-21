from ws_git_sync.models import Operation


def test_pull_and_push_do_not_read_real_sources(project):
    p = project
    p.shutil.rmtree(p.server)
    p.shutil.rmtree(p.client)
    other = p.clone()
    p.put(other, "cloud.txt", b"cloud")
    p.command(other, "add", ".")
    p.command(other, "commit", "-m", "cloud")
    p.command(other, "push")
    result, _ = p.run(Operation.PULL)
    assert result.status == "SUCCEEDED" and (p.repo / "cloud.txt").read_bytes() == b"cloud"
    result, _ = p.run(Operation.PUSH)
    assert result.status == "NO_CHANGES"


def test_push_failure_retains_commit_then_retry_does_not_duplicate(project):
    p = project
    p.put(p.client, "form/a.lua", b"new")
    p.command(p.repo, "config", "remote.origin.pushurl", str(p.repo.parent / "missing.git"))
    result, _ = p.run(Operation.SYNC)
    assert result.status == "FAILED" and result.stage == "PUSHING" and result.commit_id
    saved = p.git.head()
    assert p.git.status() == b""
    p.command(p.repo, "config", "--unset", "remote.origin.pushurl")
    retry, _ = p.run(Operation.PUSH)
    assert retry.status == "SUCCEEDED" and retry.uploaded and p.git.head() == saved
    again, _ = p.run(Operation.SYNC)
    assert not again.commit_id and p.git.head() == saved


def test_push_diverged_disjoint_changes_rebases(project):
    p = project
    other = p.clone()
    p.put(other, "cloud.txt", b"cloud")
    p.command(other, "add", ".")
    p.command(other, "commit", "-m", "cloud")
    p.command(other, "push")
    p.put(p.repo, "local.txt", b"local")
    p.command(p.repo, "add", ".")
    p.command(p.repo, "commit", "-m", "local")
    result, _ = p.run(Operation.PUSH)
    assert result.status == "SUCCEEDED" and result.uploaded
    assert (p.repo / "local.txt").read_bytes() == b"local"
    assert (p.repo / "cloud.txt").read_bytes() == b"cloud" and p.git.status() == b""


def test_encoding_divergence_conflict_aborts_and_retains_original(project):
    p = project
    path = p.target + "/MirServer/Mir200/Envir/ws/a.lua"
    p.put(p.repo, path, "旧内容\n".encode("gbk"))
    p.commit()
    other = p.clone()
    p.put(other, path, "旧内容\n".encode("utf-8"))
    p.command(other, "add", ".")
    p.command(other, "commit", "-m", "cloud transcode")
    p.command(other, "push")
    expected = "新内容\n".encode("gbk")
    p.put(p.repo, path, expected)
    p.command(p.repo, "add", ".")
    p.command(p.repo, "commit", "-m", "local source edit")
    original = p.git.head()
    result, _ = p.run(Operation.PUSH)
    assert result.error_code == "REBASE_CONFLICT", result.message
    assert p.git.head() == original and p.git.status() == b""
    assert (p.repo / path).read_bytes() == expected


def test_push_behind_fast_forwards_without_source_io(project):
    p = project
    other = p.clone()
    p.put(other, "only_cloud.txt", b"cloud")
    p.command(other, "add", ".")
    p.command(other, "commit", "-m", "cloud only")
    p.command(other, "push")
    result, _ = p.run(Operation.PUSH)
    assert result.status == "NO_CHANGES" and not result.uploaded
    assert (p.repo / "only_cloud.txt").read_bytes() == b"cloud"


def test_sync_no_file_changes_still_uploads_existing_commit(project):
    p = project
    p.put(p.repo, "local-pending.txt", b"already committed")
    p.command(p.repo, "add", ".")
    p.command(p.repo, "commit", "-m", "pending local")
    head = p.git.head()
    result, _ = p.run(Operation.SYNC)
    assert result.status == "SUCCEEDED" and result.uploaded and not result.commit_id
    assert p.git.head() == head
    assert p.command(p.repo, "ls-remote", "origin", "refs/heads/main").stdout.decode().split()[0] == head
