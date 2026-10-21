"""临时真实 Git 仓库。官方解析器可由外部环境注入，不复制进工具项目。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ws_git_sync.git_ops import Git
from ws_git_sync.models import AppSettings, Operation, Profile, TaskRequest
from ws_git_sync.tasks import run_task

# 仅作接口夹具；产品运行不存在此回退路径，也不包含私有仓库解析器源码。
RESOLVER_FIXTURE = """
import json
def context(root, key):
    hits = [p.parent for p in (root/"source").glob("*/版本信息.json")
            if json.loads(p.read_text(encoding="utf-8"))["version_id"] == key]
    if len(hits) != 1:
        raise ValueError("unknown or duplicate version")
    p = hits[0].relative_to(root).as_posix()
    return {"version_id": key,"name": hits[0].name,"root": p,
            "paths": {"version_root": p,"server_root": p+"/工程源码/MirServer",
                      "client_root": p+"/工程源码/Client"}}
"""


def git_command(root, *args, check=True):
    env = os.environ.copy()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, env=env
    )


def put(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


@pytest.fixture
def project(tmp_path):
    remote = tmp_path / "云端.git"
    git_command(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    repo = tmp_path / "Git 副本"
    git_command(tmp_path, "clone", str(remote), str(repo))
    git_command(repo, "config", "user.name", "Sync Test")
    git_command(repo, "config", "user.email", "sync@example.invalid")
    git_command(repo, "config", "core.autocrlf", "false")
    official = os.environ.get("WS_GIT_RESOLVER")
    parser = Path(official).read_bytes() if official else RESOLVER_FIXTURE.encode()
    put(repo, "tools/版本管理/ws_versions.py", parser)
    for ident, name in (("ver_001", "版本甲"), ("ver_002", "版本乙")):
        put(repo, f"source/{name}/版本信息.json", json.dumps({"schema_version": 1, "version_id": ident}))
    put(
        repo,
        "source/共享框架来源.json",
        json.dumps(
            {
                "schema_version": 1,
                "version_id": "ver_001",
                "server_root": "工程源码/MirServer/Mir200/Envir/ws",
                "client_root": "工程源码/Client/GUILayout/ws",
            }
        ),
    )
    put(repo, "README.md", "test repository\n")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "initial")
    git_command(repo, "push", "origin", "main")
    server = tmp_path / "真实 服务端" / "MirServer"
    client = tmp_path / "真实 客户端" / "dev" / "GUILayout"
    (server / "Mir200/Envir/ws").mkdir(parents=True)
    (client / "form").mkdir(parents=True)
    profile = Profile("profile-a", "本机甲", str(server), str(client), "ver_001")
    settings = AppSettings(repo=str(repo), profiles=(profile,), selected_profile_key=profile.key)
    backend = Git(repo)
    target = "source/版本甲/工程源码"

    def commit_all(message="fixture"):
        git_command(repo, "add", ".")
        git_command(repo, "commit", "-m", message)
        git_command(repo, "push", "origin", "main")

    def run(operation=Operation.PREVIEW, configuration=None):
        cfg = configuration or settings
        events = []
        result = run_task(TaskRequest("test-task", operation, cfg, profile), backend, events.append)
        return result, events

    def clone():
        other = tmp_path / "other"
        git_command(tmp_path, "clone", str(remote), str(other))
        git_command(other, "config", "user.name", "Cloud Bot")
        git_command(other, "config", "user.email", "bot@example.invalid")
        return other

    return SimpleNamespace(
        repo=repo,
        remote=remote,
        server=server,
        client=client,
        profile=profile,
        settings=settings,
        git=backend,
        target=target,
        commit=commit_all,
        run=run,
        clone=clone,
        put=put,
        command=git_command,
        shutil=shutil,
    )
