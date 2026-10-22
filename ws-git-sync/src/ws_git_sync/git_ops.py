"""Git 子进程与机器输出适配；不拼接 shell，不改持久换行配置。"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import SyncError
from .settings import validate_clone_url
from .versions import checked_path


def redact(text: str) -> str:
    text = re.sub(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", r"\1***@", text)
    return re.sub(r"(?i)(token|password|access_token)=([^\s&]+)", r"\1=***", text)


def find_git(explicit: str = "") -> str:
    if explicit:
        if not Path(explicit).is_file():
            raise SyncError("GIT_MISSING", "指定 Git 程序不存在", explicit)
        return explicit
    found = shutil.which("git")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "GitHubDesktop"
    for path in sorted(base.glob("app-*/resources/app/git/cmd/git.exe"), reverse=True):
        return str(path)
    raise SyncError("GIT_MISSING", "没有找到 Git，请安装 Git 或在设置中指定路径")


class Git:
    def __init__(self, root: Path, executable: str = ""):
        self.root = root.absolute()
        self.executable = find_git(executable)

    def run(self, *args: str, data: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
            GIT_OPTIONAL_LOCKS="0",
            GIT_EDITOR="true",
            GIT_SEQUENCE_EDITOR="true",
        )
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            result = subprocess.run(
                [self.executable, "--literal-pathspecs", "-c", "core.quotePath=false", *args],
                cwd=self.root,
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                shell=False,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except OSError as exc:
            raise SyncError("GIT_START", f"无法启动 Git：{exc}", self.root) from exc
        if check and result.returncode:
            diagnostic = redact((result.stderr or result.stdout).decode("utf-8", errors="replace").strip())
            raise SyncError(
                "GIT_COMMAND", f"Git {args[0]} 失败（{result.returncode}）：{diagnostic}", self.root
            )
        return result

    def text(self, *args: str) -> str:
        return self.run(*args).stdout.decode("utf-8").strip()

    def head(self) -> str:
        return self.text("rev-parse", "HEAD")

    def status(self) -> bytes:
        return self.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout

    def check(self, branch: str) -> None:
        if not self.root.exists():
            raise SyncError("REPO_UNINITIALIZED", "本地 Git 目录尚未创建或初始化", self.root)
        if not self.root.is_dir():
            raise SyncError("REPO_ROOT", "请选择 Git 仓库根目录", self.root)
        probe = self.run("rev-parse", "--show-toplevel", check=False)
        if probe.returncode:
            # 仅无元数据的目录可以引导初始化；损坏的 .git 或权限错误不能覆盖。
            if not os.path.lexists(self.root / ".git") and b"not a git repository" in probe.stderr:
                raise SyncError("REPO_UNINITIALIZED", "该目录尚未初始化为 Git 仓库", self.root)
            raise SyncError(
                "REPO_ROOT",
                "Git 仓库不可用，请检查权限或已有 .git 元数据："
                + redact(probe.stderr.decode("utf-8", errors="replace")),
                self.root,
            )
        if Path(probe.stdout.decode("utf-8").strip()).resolve() != self.root.resolve():
            raise SyncError("REPO_ROOT", "请选择 Git 仓库根目录，不能选择仓库中的子目录", self.root)
        self.run("check-ref-format", "--branch", branch)
        actual = self.text("branch", "--show-current")
        if actual != branch:
            raise SyncError("WRONG_BRANCH", f"当前分支为 {actual or '游离 HEAD'}，目标为 {branch}")
        for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            path = Path(self.text("rev-parse", "--git-path", marker))
            if not path.is_absolute():
                path = self.root / path
            if path.exists():
                raise SyncError("GIT_IN_PROGRESS", "请先处理已有 Git 合并或变基状态", path)
        self.require_clean()

    def initialize(self, url: str, remote: str, branch: str) -> bool:
        """仅由用户确认的初始化任务调用。先取得完整远端元数据，不覆盖现有文件。"""
        validate_clone_url(url)
        checked_path(self.root, ".")
        if self.root.exists() and not self.root.is_dir():
            raise SyncError("REPO_ROOT", "初始化目标不是目录", self.root)
        if os.path.lexists(self.root / ".git"):
            raise SyncError("REPO_EXISTS", "该目录已有 Git 元数据，未重新初始化", self.root)
        probe_root = self.root
        while not probe_root.exists():
            probe_root = probe_root.parent
        probe = Git(probe_root, self.executable).run("rev-parse", "--show-toplevel", check=False)
        if not probe.returncode:
            raise SyncError("REPO_ROOT", "目标已位于 Git 仓库内，请选择正确仓库根", self.root)
        if b"not a git repository" not in probe.stderr:
            raise SyncError("REPO_ROOT", "无法确认目录状态，未初始化", self.root)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        # 在独立临时目录完成 clone；失败不会向用户目录留下半成品 .git。
        with tempfile.TemporaryDirectory(prefix=".ws-git-init-", dir=self.root.parent) as temp:
            staged = Path(temp) / "checkout"
            command = Git(Path(temp), self.executable)
            command.run(
                "clone",
                "--no-checkout",
                "--single-branch",
                "--branch",
                branch,
                "--origin",
                remote,
                "--",
                url,
                str(staged),
            )
            clone = Git(staged, self.executable)
            clone.run("read-tree", "HEAD")
            checked_path(self.root, ".")
            self.root.mkdir(parents=True, exist_ok=True)
            if os.path.lexists(self.root / ".git"):
                raise SyncError("REPO_EXISTS", "初始化期间其他程序创建了 Git 元数据，已停止", self.root)
            had_files = any(self.root.iterdir())
            # 只迁入 .git。原文件不删除、不覆盖；已有差异由正常干净工作区门禁处理。
            os.rename(staged / ".git", self.root / ".git")
            if not had_files:
                # 不使用 --force；即使期间外部创建文件也不会覆盖。
                self.run("checkout-index", "--all")
        return had_files

    def require_clean(self) -> None:
        dirty = self.status()
        if dirty:
            raise SyncError(
                "DIRTY_REPO",
                "工作区或暂存区有未提交改动，请先处理：\n"
                + dirty.replace(b"\0", b"\n").decode("utf-8", errors="replace"),
            )

    def check_index_flags(self) -> None:
        # skip-worktree / assume-unchanged 会让 status 隐藏真实改动，首版明确拒绝。
        hidden = [p for p in self.run("ls-files", "-v", "-z").stdout.split(b"\0") if p and p[:1] != b"H"]
        if hidden:
            raise SyncError("HIDDEN_INDEX", "仓库使用稀疏检出或隐藏索引标记，请使用普通完整 Git 副本")

    def tracked(self) -> tuple[str, ...]:
        return tuple(os.fsdecode(x) for x in self.run("ls-files", "-z").stdout.split(b"\0") if x)

    def blob(self, revision: str, path: str) -> bytes:
        return self.run("cat-file", "blob", f"{revision}:{path}").stdout

    def pull(self, remote: str, branch: str) -> None:
        self.run("pull", "--ff-only", remote, branch)

    def stage(self, paths: tuple[str, ...]) -> None:
        self.run(
            "add",
            "-A",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            data=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
        )

    def staged_paths(self) -> set[str]:
        return {
            os.fsdecode(p)
            for p in self.run("diff", "--cached", "--name-only", "--no-renames", "-z").stdout.split(b"\0")
            if p
        }

    def clean_hash(self, path: str, content: bytes) -> str:
        return self.run("hash-object", "--path=" + path, "--stdin", data=content).stdout.decode().strip()

    def raw_hash(self, content: bytes) -> str:
        """按仓库对象格式计算原始快照Blob，不受换行、属性和索引历史影响。"""
        return self.run("hash-object", "--no-filters", "--stdin", data=content).stdout.decode().strip()

    def push_existing(self, remote: str, branch: str) -> bool:
        """仅处理已有提交；分叉时 rebase，失败 abort，绝不强推。"""
        self.require_clean()
        original = self.head()
        self.run("fetch", remote, branch)
        fetched = self.text("rev-parse", "FETCH_HEAD")
        ahead, behind = map(
            int, self.text("rev-list", "--left-right", "--count", f"HEAD...{fetched}").split()
        )
        if behind and ahead:
            result = self.run("rebase", fetched, check=False)
            if result.returncode:
                aborted = self.run("rebase", "--abort", check=False)
                if aborted.returncode or self.head() != original:
                    raise SyncError("REBASE_RECOVERY", "变基失败且自动中止未确认恢复，请检查 Git 状态")
                raise SyncError(
                    "REBASE_CONFLICT",
                    "变基冲突，已中止并保留原提交："
                    + redact(result.stderr.decode("utf-8", errors="replace")),
                )
        elif behind:
            self.run("merge", "--ff-only", fetched)
        if ahead:
            self.run("push", remote, f"HEAD:refs/heads/{branch}")
            return True
        return False
