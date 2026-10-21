"""真实工程只读 → 变更计划 → Git 副本；本模块不知道 Git 命令或 Qt。"""

import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .models import ChangeItem, ChangePlan, FileSnapshot, ResolvedContext, SyncError
from .versions import checked_path
from .win_io import read_shared

SERVER_TEXT = frozenset((".lua", ".txt", ".ini", ".cfg", ".json", ".csv", ".tsv"))
CLIENT_TEXT = frozenset((".lua", ".txt", ".json"))
RULES = (
    ("server", "Mir200/Envir/ws", SERVER_TEXT - {".tsv"}, True),
    ("server", "Mir200/Envir/Market_Def", SERVER_TEXT - {".tsv"}, True),
    ("server", "Mir200/Envir/Data", frozenset((".xls",)), False),
    ("client", "ws", CLIENT_TEXT, True),
    ("client", "system", CLIENT_TEXT, True),
    ("client", "form", CLIENT_TEXT, True),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_read(path: Path, attempts: int = 10, delay: float = 0.5) -> bytes:
    """只在稳定读取后返回。读取失败绝不返回空 bytes 或作为缺失。"""
    cause = None
    for index in range(attempts):
        try:
            checked_path(path.parent, path.name)
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise SyncError("NOT_FILE", "源或目标不是普通文件", path)
            content = read_shared(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) == (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ) and len(content) == after.st_size:
                return content
            cause = "读取期间文件被修改"
        except OSError as exc:
            cause = str(exc)
        if index + 1 < attempts:
            time.sleep(delay)
    raise SyncError("SOURCE_READ", f"文件持续变化或无法读取（已尝试 {attempts} 次）：{cause}", path)


def fingerprint(path: Path) -> str | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return digest(stable_read(path))


def source_text(raw: bytes, path: Path) -> str:
    if raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise SyncError("SOURCE_ENCODING", "真实服务端文本不能携带 UTF BOM，期望 GBK", path)
    try:
        text = raw.decode("gbk", errors="strict")
        if text.encode("gbk") != raw:
            raise ValueError("GBK 回读不一致")
        return text.replace("\r\n", "\n").replace("\r", "\n")
    except (UnicodeError, ValueError) as exc:
        raise SyncError("SOURCE_ENCODING", f"真实服务端文件不是有效 GBK：{exc}", path) from exc


def same_server(raw: bytes, head: bytes, path: Path) -> bool:
    # 源始终严格校验；比较内存中统一换行，写回仍是原始 GBK 字节。
    text = source_text(raw, path)
    if raw == head:
        return True
    valid = False
    for encoding in ("utf-8", "gbk"):
        try:
            other = head.decode(encoding, errors="strict")
            if other.encode(encoding) != head:
                continue
            valid = True
            if text == other.replace("\r\n", "\n").replace("\r", "\n"):
                return True
        except UnicodeError:
            continue
    if not valid:
        raise SyncError("HEAD_ENCODING", "仓库 Blob 既不是有效 UTF-8，也不是有效 GBK", path)
    return False


def validate_sources(context: ResolvedContext) -> None:
    roots = (context.server, context.client)
    for root in roots:
        checked_path(root, ".")
        if not root.is_dir():
            raise SyncError("SOURCE_ROOT", "真实工程根不存在或不是目录", root)
        # 通过真正枚举判断访问权限，不能依赖 exists 的布尔结果。
        with os.scandir(root) as entries:
            list(entries)
        if root.resolve().is_relative_to(context.repo.resolve()) or context.repo.resolve().is_relative_to(
            root.resolve()
        ):
            raise SyncError("OVERLAPPING_ROOTS", "真实工程与 Git 副本不能重叠", root)
    if roots[0].resolve().is_relative_to(roots[1].resolve()) or roots[1].resolve().is_relative_to(
        roots[0].resolve()
    ):
        raise SyncError("OVERLAPPING_ROOTS", "服务端与客户端真实根不能重叠")
    if not checked_path(context.server, "Mir200/Envir").is_dir():
        raise SyncError("SOURCE_SHAPE", "服务端请选到 MirServer 层，缺少 Mir200/Envir", context.server)
    if not any(checked_path(context.client, p).is_dir() for p in ("ws", "system", "form")):
        raise SyncError(
            "SOURCE_SHAPE", "客户端请选到 GUILayout 层，至少需有 ws/system/form 之一", context.client
        )


def validate_source_binding(repo: Path, server: Path, client: Path) -> None:
    """pull 前先确认真实路径不与 Git 副本重叠，避免配置错误使 pull 触碰真源。"""
    for root in (server, client):
        checked_path(root, ".")
        if root.resolve().is_relative_to(repo.resolve()) or repo.resolve().is_relative_to(root.resolve()):
            raise SyncError("OVERLAPPING_ROOTS", "真实工程与 Git 副本不能重叠", root)


def generated(context: ResolvedContext, relative: str) -> bool:
    if not relative.startswith(context.server_target + "/"):
        return False
    p = Path(relative)
    return "data" in (part.casefold() for part in p.parts) and (
        p.suffix.casefold() == ".tsv" or p.name.casefold() == "镜像状态.json"
    )


def inventory(context: ResolvedContext, ignored: tuple[str, ...]) -> tuple[str, ...]:
    excluded = {n.casefold() for n in ignored}
    found = set()

    def walk(root: Path, recurse: bool, extensions: frozenset):
        checked_path(root, ".")
        try:
            entries = os.scandir(root)
        except FileNotFoundError:
            return
        with entries:
            for entry in entries:
                p = checked_path(root, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    if recurse:
                        walk(p, True, extensions)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and p.name.casefold() not in excluded
                    and p.suffix.casefold() in extensions
                ):
                    found.add(str(p))

    for side, relative, extensions, recurse in RULES:
        walk(checked_path(getattr(context, side), relative), recurse, extensions)
    return tuple(sorted(found))


class ContentStore:
    """计划只保留稳定快照；超过内存预算的内容转存本工具临时目录。"""

    def __init__(self, root: Path):
        self.root = root
        self.memory = 0
        self.index = 0

    def snapshot(self, path: Path, content: bytes | None) -> FileSnapshot:
        if content is None:
            return FileSnapshot(path, None, None)
        hashed = digest(content)
        if len(content) > 1024 * 1024 or self.memory + len(content) > 16 * 1024 * 1024:
            self.index += 1
            spool = self.root / str(self.index)
            spool.write_bytes(content)
            return FileSnapshot(path, hashed, spool)
        self.memory += len(content)
        return FileSnapshot(path, hashed, content)


def payload(snapshot: FileSnapshot) -> bytes:
    data = snapshot.content.read_bytes() if isinstance(snapshot.content, Path) else snapshot.content
    if data is None or digest(data) != snapshot.digest:
        raise SyncError("SNAPSHOT_CHANGED", "任务快照已缺失或变化", snapshot.source)
    return data


def build_plan(
    task_id: str,
    context: ResolvedContext,
    head: str,
    tracked: tuple[str, ...],
    blob: Callable[[str], bytes],
    ignored: tuple[str, ...],
    store: ContentStore,
    emit: Callable[[str, str, str], None],
) -> ChangePlan:
    """只读扫描；只有身份、范围与所有源读取成功后，才返回完整计划。"""
    validate_sources(context)
    listed = inventory(context, ignored)
    excluded = {n.casefold() for n in ignored}
    candidates: dict[str, Path] = {}
    skipped = 0
    roots = ((context.server_target, context.server), (context.client_target, context.client))
    tracked_set = set(tracked)
    for relative in tracked:
        for target, source in roots:
            if relative.startswith(target + "/"):
                if Path(relative).name.casefold() in excluded or generated(context, relative):
                    skipped += 1
                    emit("skip", "过滤/派生跳过", relative)
                else:
                    candidates[relative] = checked_path(source, relative[len(target) + 1 :])
                break
    for name in listed:
        source = Path(name)
        for target, root in roots:
            if source.is_relative_to(root):
                relative = target + "/" + source.relative_to(root).as_posix()
                if not generated(context, relative):
                    candidates[relative] = source
                break
    folded = [p.casefold() for p in candidates]
    if len(folded) != len(set(folded)):
        raise SyncError("CASE_COLLISION", "同步路径存在大小写歧义")
    changes = []
    sources = []
    for relative, source in sorted(candidates.items()):
        target = checked_path(context.repo, relative)
        before = fingerprint(target)
        source_hash = fingerprint(source)
        if source_hash is None:
            snapshot = store.snapshot(source, None)
            if relative in tracked_set:
                changes.append(ChangeItem("delete", relative, snapshot, before))
                emit("delete", "删除", relative)
        else:
            content = stable_read(source)
            snapshot = store.snapshot(source, content)
            server_text = (
                relative.startswith(context.server_target + "/") and source.suffix.casefold() in SERVER_TEXT
            )
            if server_text:
                source_text(content, source)
            same = False
            if relative in tracked_set and before is not None:
                same = (
                    same_server(content, blob(relative), source) if server_text else digest(content) == before
                )
            if not same:
                kind = "modify" if relative in tracked_set else "add"
                changes.append(ChangeItem(kind, relative, snapshot, before))
                emit(kind, {"modify": "修改", "add": "新增"}[kind], relative)
        sources.append(snapshot)
    if inventory(context, ignored) != listed:
        raise SyncError("SOURCE_CHANGED", "扫描期间目录清单发生变化，请重新预览或同步")
    rule_hash = digest(repr((RULES, tuple(sorted(excluded)))).encode("utf-8"))
    return ChangePlan(task_id, context, head, rule_hash, tuple(changes), tuple(sources), listed, skipped)


def verify_sources(plan: ChangePlan, ignored: tuple[str, ...]) -> None:
    validate_sources(plan.context)
    if inventory(plan.context, ignored) != plan.inventory:
        raise SyncError("SOURCE_CHANGED", "预备应用时源目录清单已变化，请重新运行")
    for snapshot in plan.sources:
        if fingerprint(snapshot.source) != snapshot.digest:
            raise SyncError("SOURCE_CHANGED", "扫描后真实文件已变化，请重新运行", snapshot.source)


def apply_plan(plan: ChangePlan, applied: list[str], emit: Callable[[str, str, str], None]) -> None:
    """只写计划内的 Git 工程目标。逐文件原子替换，不承诺整批原子性。"""
    context = plan.context
    for item in plan.changes:
        if not any(item.relative.startswith(p + "/") for p in (context.server_target, context.client_target)):
            raise SyncError("PLAN_ESCAPE", "计划目标超出当前版本工程", item.relative)
        target = checked_path(context.repo, item.relative)
        if fingerprint(target) != item.before:
            raise SyncError("TARGET_CHANGED", "目标文件被外部修改，停止应用", target)
        if item.kind == "delete":
            target.unlink()
        else:
            content = payload(item.snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            checked_path(context.repo, item.relative)
            fd, name = tempfile.mkstemp(prefix=".ws-sync-", dir=target.parent)
            pending = Path(name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if fingerprint(target) != item.before:
                    raise SyncError("TARGET_CHANGED", "替换前目标文件被外部修改", target)
                os.replace(pending, target)
            finally:
                pending.unlink(missing_ok=True)
        applied.append(item.relative)
        emit("applied", "已应用到 Git 副本", item.relative)
