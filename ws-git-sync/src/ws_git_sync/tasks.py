"""四个操作的编排；只依赖普通 Python 数据，不操作 GUI。"""

import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .git_ops import Git
from .models import Operation, SyncError, TaskEvent, TaskRequest, TaskResult
from .settings import validate_profile
from .source_sync import (
    ContentStore,
    apply_plan,
    build_plan,
    fingerprint,
    payload,
    validate_source_binding,
    verify_sources,
)
from .versions import checked_path, resolve_context


def run_task(request: TaskRequest, git: Git, emit: Callable[[TaskEvent], None]) -> TaskResult:
    """任务唯一执行入口。任何失败都保留已应用路径和已创建提交的事实。"""
    settings = request.settings
    stage = "VALIDATING"
    sequence = 0
    commit = ""
    applied: list[str] = []
    counts: tuple[tuple[str, int], ...] = ()

    def event(code: str, message: str, path: str = "", level: str = "info"):
        nonlocal sequence
        sequence += 1
        emit(
            TaskEvent(
                request.task_id,
                sequence,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                stage,
                code,
                message,
                level,
                path,
            )
        )

    def phase(name: str, message: str):
        nonlocal stage
        stage = name
        event("stage", message)

    try:
        checked_path(git.root, ".")
        if (
            not settings.branch
            or settings.branch.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", settings.remote)
            or settings.remote.startswith("-")
        ):
            raise SyncError("INVALID_GIT_SETTINGS", "目标分支或远端名称无效")
        if request.operation == Operation.INITIALIZE:
            for profile in settings.profiles:
                for source in (profile.server, profile.client):
                    if source.strip():
                        root = Path(source).absolute()
                        validate_source_binding(git.root, root, root)
            # ref 校验不依赖仓库存在；初始化命令本身使用参数数组。
            Git(git.root.parent if git.root.parent.exists() else Path.cwd(), git.executable).run(
                "check-ref-format", "--branch", settings.branch
            )
            phase("INITIALIZING", "连接远端并初始化本地 Git 目录…")
            preserved = git.initialize(settings.clone_url, settings.remote, settings.branch)
            return TaskResult(
                request.task_id,
                "SUCCEEDED",
                stage,
                "Git 元数据已连接，原有文件全部保留；后续操作会检查文件差异"
                if preserved
                else "Git 仓库已初始化并检出，真实工程未读取或写入",
            )
        git.check(settings.branch)
        event("git", f"Git：{git.executable}；分支：{settings.branch}")
        if request.operation == Operation.PULL:
            phase("UPDATING", "快进更新本地 Git 副本…")
            git.pull(settings.remote, settings.branch)
            git.check(settings.branch)
            return TaskResult(request.task_id, "SUCCEEDED", stage, "Git 副本已更新，真实工程未读取或写入")
        if request.operation == Operation.PUSH:
            phase("PUSHING", "核对远端并上传已有提交…")
            uploaded = git.push_existing(settings.remote, settings.branch)
            return TaskResult(
                request.task_id,
                "SUCCEEDED" if uploaded else "NO_CHANGES",
                stage,
                "已有提交上传成功" if uploaded else "没有待上传提交；必要的快进更新已完成",
                uploaded=uploaded,
            )
        if request.profile is None:
            raise SyncError("NO_PROFILE", "请先选择并保存版本配置")
        validate_profile(request.profile)
        validate_source_binding(
            git.root, Path(request.profile.server).absolute(), Path(request.profile.client).absolute()
        )
        git.check_index_flags()
        if request.operation == Operation.SYNC:
            phase("UPDATING", "同步前快进拉取 Git 副本…")
            git.pull(settings.remote, settings.branch)
            git.check(settings.branch)
        phase("RESOLVING", f"按固定 ID {request.profile.version_id} 解析最新版本目录…")
        context = resolve_context(git.root, request.profile)
        head = git.head()
        event("baseline", f"{context.name} · {context.version_id} · 本地 HEAD {head[:12]}")
        with tempfile.TemporaryDirectory(prefix="ws-git-sync-") as temp:
            phase("SCANNING", "扫描真实工程并构建变更计划…")
            plan = build_plan(
                request.task_id,
                context,
                head,
                git.tracked(),
                lambda p: git.blob(head, p),
                settings.ignore_file_names,
                ContentStore(Path(temp)),
                event,
            )
            counts = tuple(sorted(Counter(p.kind for p in plan.changes).items()))
            summary = dict(counts)
            event(
                "summary",
                f"新增 {summary.get('add', 0)} / 修改 {summary.get('modify', 0)} / 删除 {summary.get('delete', 0)} / 跳过 {plan.skipped}",
            )
            if request.operation == Operation.PREVIEW:
                git.check(settings.branch)
                if git.head() != head:
                    raise SyncError("BASELINE_CHANGED", "扫描期间 Git HEAD 已变化")
                return TaskResult(
                    request.task_id, "SUCCEEDED", stage, "预览结束；未拉取、复制、提交或上传", changes=counts
                )
            git.check(settings.branch)
            if git.head() != head:
                raise SyncError("BASELINE_CHANGED", "Git HEAD 已变化，未执行旧计划")
            verify_sources(plan, settings.ignore_file_names)
            if plan.changes:
                phase("APPLYING", "将计划应用到当前版本 Git 工程副本…")
                apply_plan(plan, applied, event)
                if git.head() != head:
                    raise SyncError("BASELINE_CHANGED", "应用期间 Git HEAD 被外部修改，停止提交")
                phase("STAGING", "仅暂存本轮计划文件…")
                paths = tuple(p.relative for p in plan.changes)
                # 暂存前不允许夹带任何外部暂存项。
                if git.staged_paths():
                    raise SyncError("FOREIGN_INDEX", "外部程序修改暂存区，停止提交")
                git.stage(paths)
                staged = git.staged_paths()
                missing = set(paths) - staged
                extra = staged - set(paths)

                def stage_mismatch():
                    detail = []
                    if missing:
                        detail.append("计划内未暂存：\n" + "\n".join(sorted(missing)))
                    if extra:
                        detail.append("计划外暂存：\n" + "\n".join(sorted(extra)))
                    return SyncError(
                        "STAGE_MISMATCH", "实际暂存范围与计划不一致，已保留现场\n" + "\n".join(detail)
                    )

                if extra or any(item.kind != "modify" and item.relative in missing for item in plan.changes):
                    raise stage_mismatch()
                expected_blobs = {}
                normalized = []
                for item in plan.changes:
                    if item.kind == "delete":
                        if (
                            fingerprint(git.root / item.relative) is not None
                            or git.run("cat-file", "-e", ":" + item.relative, check=False).returncode == 0
                        ):
                            raise SyncError(
                                "STAGE_CONTENT", "计划删除的文件被外部恢复，停止提交", item.relative
                            )
                    else:
                        expected = payload(item.snapshot)
                        if fingerprint(git.root / item.relative) != item.snapshot.digest:
                            raise SyncError("TARGET_CHANGED", "应用后的文件被外部修改", item.relative)
                        actual = git.text("rev-parse", ":" + item.relative)
                        # add会参考原索引保留已有CRLF；hash-object不会加载相同索引历史。
                        # 索引若逐字节等于稳定源快照，直接认可原始Blob；否则必须匹配Git处理结果。
                        raw = git.raw_hash(expected)
                        expected_blobs[item.relative] = (
                            raw if actual == raw else git.clean_hash(item.relative, expected)
                        )
                        if item.relative in missing:
                            # Git可能按autocrlf/attributes归一化换行，使字节修改不产生索引差异。
                            # 仅在工作区仍为源快照、索引等于源经Git处理后的Blob且等于原HEAD时放行。
                            if actual != expected_blobs[item.relative] or actual != git.text(
                                "rev-parse", head + ":" + item.relative
                            ):
                                raise stage_mismatch()
                            normalized.append(item.relative)
                        if actual != expected_blobs[item.relative]:
                            raise SyncError("STAGE_CONTENT", "暂存内容与稳定快照不一致", item.relative)
                if normalized:
                    event(
                        "normalized",
                        f"{len(normalized)} 项经Git换行/属性处理后与原提交相同，无需重复提交。",
                    )
                    counts = tuple(
                        sorted(Counter(p.kind for p in plan.changes if p.relative in staged).items())
                    )
                # 未暂存改动包括外部编辑，不能与本任务的提交一起报告成功。
                if (
                    git.run("diff", "--quiet", check=False).returncode
                    or git.run("ls-files", "--others", "--exclude-standard", "-z").stdout
                ):
                    raise SyncError("FOREIGN_WORKTREE", "检测到计划外或暂存后的改动，停止提交")
                if git.head() != head:
                    raise SyncError("BASELINE_CHANGED", "提交前 Git HEAD 已变化")
                if staged:
                    phase("COMMITTING", "提交当前版本工程变更…")
                    git.run("commit", "-m", f"chore(source): 同步 {context.version_id} 真实工程源码")
                    commit = git.head()
                    committed = {
                        os.fsdecode(p)
                        for p in git.run(
                            "diff-tree",
                            "--no-commit-id",
                            "--name-only",
                            "--no-renames",
                            "-r",
                            "-z",
                            head,
                            commit,
                        ).stdout.split(b"\0")
                        if p
                    }
                    if committed != staged:
                        raise SyncError(
                            "COMMIT_SCOPE", "提交内容范围与计划不一致，请检查提交 hook；提交已保留"
                        )
                    for relative, expected_blob in expected_blobs.items():
                        if git.text("rev-parse", commit + ":" + relative) != expected_blob:
                            raise SyncError(
                                "COMMIT_CONTENT",
                                "提交后内容与快照不一致，请检查提交 hook；提交已保留",
                                relative,
                            )
                    for item in plan.changes:
                        if (
                            item.kind == "delete"
                            and git.run(
                                "cat-file", "-e", commit + ":" + item.relative, check=False
                            ).returncode
                            == 0
                        ):
                            raise SyncError(
                                "COMMIT_CONTENT",
                                "计划删除的文件仍在提交内，请检查提交 hook；提交已保留",
                                item.relative,
                            )
                    event("committed", f"提交成功 {commit[:12]}", level="success")
        phase("PUSHING", "上传分支已有提交…")
        git.require_clean()
        git.run("push", settings.remote, f"HEAD:refs/heads/{settings.branch}")
        event("uploaded", "上传成功；云端 UTF-8/TSV 派生任务完成状态未检查", level="success")
        return TaskResult(
            request.task_id, "SUCCEEDED", stage, "同步并上传完成", commit, True, counts, tuple(applied)
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, SyncError) else "UNEXPECTED"
        message = str(exc)
        event(code, message, level="error")
        if applied and not commit:
            event(
                "partial",
                f"Git 副本已应用 {len(applied)} 项；未确认提交。请检查工作区后重试。",
                level="error",
            )
        if commit:
            event(
                "pending_push",
                f"本地提交 {commit[:12]} 已保留，可处理错误后使用“本地 Git → 云端”。",
                level="error",
            )
        return TaskResult(
            request.task_id, "FAILED", stage, message, commit, False, counts, tuple(applied), code
        )
