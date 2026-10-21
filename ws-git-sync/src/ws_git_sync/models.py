"""跨模块数据契约；本模块无 Qt、文件和网络 IO。"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Operation(StrEnum):
    INITIALIZE = "initialize"
    PREVIEW = "preview"
    SYNC = "sync"
    PULL = "pull"
    PUSH = "push"


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    server: str
    client: str
    version_id: str


@dataclass(frozen=True)
class Layout:
    width: int = 1080
    height: int = 720
    x: int | None = None
    y: int | None = None
    sidebar: int = 180
    editor: int = 175
    font: int = 14
    columns: int = 4
    order: tuple[str, ...] = ("editor", "actions", "logs")
    revision: int = 2


@dataclass(frozen=True)
class AppSettings:
    repo: str = ""
    remote: str = "origin"
    branch: str = "main"
    git_path: str = ""
    clone_url: str = ""
    profiles: tuple[Profile, ...] = ()
    selected_profile_key: str = ""
    ignore_file_names: tuple[str, ...] = ()
    ui: Layout = field(default_factory=Layout)


@dataclass(frozen=True)
class TaskRequest:
    task_id: str
    operation: Operation
    settings: AppSettings
    profile: Profile | None


@dataclass(frozen=True)
class ResolvedContext:
    repo: Path
    version_id: str
    name: str
    version_root: str
    server_target: str
    client_target: str
    server: Path
    client: Path


@dataclass(frozen=True)
class FileSnapshot:
    source: Path
    digest: str | None
    content: bytes | Path | None


@dataclass(frozen=True)
class ChangeItem:
    kind: str
    relative: str
    snapshot: FileSnapshot
    before: str | None


@dataclass(frozen=True)
class ChangePlan:
    task_id: str
    context: ResolvedContext
    head: str
    rules_fingerprint: str
    changes: tuple[ChangeItem, ...]
    sources: tuple[FileSnapshot, ...]
    inventory: tuple[str, ...]
    skipped: int = 0


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    sequence: int
    time: str
    stage: str
    code: str
    message: str
    level: str = "info"
    path: str = ""


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    stage: str
    message: str
    commit_id: str = ""
    uploaded: bool = False
    changes: tuple[tuple[str, int], ...] = ()
    applied: tuple[str, ...] = ()
    error_code: str = ""


class SyncError(Exception):
    def __init__(self, code: str, message: str, path: str | Path = ""):
        self.code = code
        self.message = message
        self.path = str(path)
        super().__init__(message + (f"：{path}" if path else ""))
