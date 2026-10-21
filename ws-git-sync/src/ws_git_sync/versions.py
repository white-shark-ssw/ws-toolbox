"""仓库解析器适配；只信任用户选定的 ws 仓库，不复制版本发现规则。"""

import hashlib
import importlib.util
import os
import stat
import sys
from pathlib import Path, PurePosixPath

from .models import Profile, ResolvedContext, SyncError


def checked_path(root: Path, relative: str) -> Path:
    """拒绝路径穿越及任何既有链接/重解析点，包括根目录自身。"""
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or "\\" in relative or ":" in relative:
        raise SyncError("PATH_ESCAPE", "路径必须位于确认的目录内", relative)
    target = root / relative
    for p in (
        root,
        *reversed(root.parents),
        *[root.joinpath(*part.parts[:i]) for i in range(1, len(part.parts) + 1)],
    ):
        try:
            info = p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SyncError("LINK_PATH", "不允许同步链接或重解析点", p)
    if not target.resolve().is_relative_to(root.resolve()):
        raise SyncError("PATH_ESCAPE", "路径越出目标根", target)
    return target


def resolve_context(repo: Path, profile: Profile) -> ResolvedContext:
    repo = repo.absolute()
    checked_path(repo, ".")
    path = checked_path(repo, "tools/版本管理/ws_versions.py")
    try:
        code = path.read_bytes()
        name = "_ws_versions_" + hashlib.sha256(os.fsencode(repo) + code).hexdigest()
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        # exec 已核验路径的原始源码，避免 importlib loader 在工作区写 __pycache__。
        # 这仍是执行用户信任仓库里的代码，不构成沙箱。
        sys.modules[name] = module
        try:
            exec(compile(code, str(path), "exec"), module.__dict__)
            result = module.context(repo, profile.version_id)
        finally:
            sys.modules.pop(name, None)
        paths = result["paths"]
        version = paths["version_root"]
        if result["version_id"] != profile.version_id or result["root"] != version:
            raise ValueError("解析身份不一致")
        if len(PurePosixPath(version).parts) != 2 or not version.startswith("source/"):
            raise ValueError("版本根不符合 source/<名称> 契约")
        server = paths["server_root"]
        client = paths["client_root"] + "/GUILayout"
        if server != version + "/工程源码/MirServer" or client != version + "/工程源码/Client/GUILayout":
            raise ValueError("解析器工程路径契约已变化")
        for p in (version, server, client):
            checked_path(repo, p)
        return ResolvedContext(
            repo,
            profile.version_id,
            result["name"],
            version,
            server,
            client,
            Path(profile.server).absolute(),
            Path(profile.client).absolute(),
        )
    except SyncError:
        raise
    except Exception as exc:
        raise SyncError("VERSION_RESOLVE", f"版本解析失败：{exc}", path) from exc
