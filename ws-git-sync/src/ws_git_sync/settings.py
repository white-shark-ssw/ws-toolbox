"""本机配置；坏文件和未来版本不会被空配置覆盖。"""

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from .models import AppSettings, Layout, Profile, SyncError

# 公开源码不携带个人工作流的过滤清单；已有配置中的清单原样保留。
DEFAULT_IGNORES: tuple[str, ...] = ()


def config_directory() -> Path:
    """冻结版使用真实EXE目录，源码版使用项目根；不依赖工作目录或解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def legacy_config_directory() -> Path:
    """仅用于首次迁入旧配置；后续不再向用户目录写配置或锁文件。"""
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "wsGitSync"


def migrate_legacy_settings(directory: Path) -> bool:
    """持有新位置实例锁后调用；仅新位置没有配置时复制有效旧配置，保留旧文件。"""
    target = directory / "settings.json"
    legacy = legacy_config_directory() / "settings.json"
    if target.exists() or target.resolve() == legacy.resolve() or not legacy.exists():
        return False
    SettingsStore(legacy).load()
    try:
        content = legacy.read_bytes()
        if target.exists():
            return False
        atomic_write(target, content)
    except OSError as exc:
        raise SyncError("CONFIG_MIGRATION", f"无法迁入旧配置，旧文件已保留：{exc}", target) from exc
    return True


def validate_profile(profile: Profile) -> None:
    if not profile.name.strip() or not profile.server.strip() or not profile.client.strip():
        raise SyncError("INVALID_PROFILE", "请填写名称、服务端和客户端路径")
    if not re.fullmatch(r"ver_[0-9]{3,}", profile.version_id):
        raise SyncError("INVALID_VERSION_ID", "版本 ID 应为 ver_001 形式")


def validate_clone_url(value: str) -> None:
    """仓库地址只接受常规传输或绝对本地路径，不持久保存密码/令牌。"""
    if value and Path(value).is_absolute():
        return
    parsed = urlsplit(value)
    if parsed.scheme in ("https", "ssh") and parsed.hostname and parsed.path:
        if (
            not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (not parsed.username or parsed.scheme == "ssh")
        ):
            return
    if re.fullmatch(r"git@[A-Za-z0-9.-]+:[^\s]+", value):
        return
    raise SyncError(
        "INVALID_REMOTE_URL", "请填写 HTTPS、SSH 仓库地址或绝对本地仓库路径；地址中不要包含密码或令牌"
    )


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    pending = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings(ignore_file_names=DEFAULT_IGNORES)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
                raise ValueError("配置版本不受支持；原文件已保留")

            def string(obj, key, default=""):
                value = obj.get(key, default)
                if not isinstance(value, str):
                    raise ValueError(f"{key} 必须是文本")
                return value

            profiles = tuple(
                Profile(**{k: string(p, k) for k in Profile.__dataclass_fields__})
                for p in raw.get("profiles", [])
            )
            keys = [p.key for p in profiles]
            if any(not k for k in keys) or len(keys) != len(set(keys)):
                raise ValueError("配置 key 缺失或重复")
            ignored = raw.get("ignore_file_names", list(DEFAULT_IGNORES))
            if not isinstance(ignored, list) or any(
                not isinstance(n, str) or "/" in n or "\\" in n for n in ignored
            ):
                raise ValueError("过滤清单必须是完整文件名数组")
            ui = raw.get("ui", {})

            def number(key, default, low, high):
                value = ui.get(key, default)
                return max(low, min(high, value)) if type(value) is int else default

            order = ui.get("order", ["editor", "actions", "logs"])
            if not isinstance(order, list) or sorted(order) != ["actions", "editor", "logs"]:
                order = ["editor", "actions", "logs"]
            layout = Layout(
                width=number("width", 1080, 900, 7680),
                height=number("height", 720, 600, 4320),
                x=ui.get("x") if type(ui.get("x")) is int else None,
                y=ui.get("y") if type(ui.get("y")) is int else None,
                sidebar=number("sidebar", 180, 150, 360),
                editor=number("editor", 175, 175, 360) if ui.get("revision") == 2 else 175,
                font=number("font", 14, 12, 18),
                columns=number("columns", 4, 1, 4),
                order=tuple(order),
            )
            return AppSettings(
                repo=string(raw, "repo"),
                remote=string(raw, "remote", "origin"),
                branch=string(raw, "branch", "main"),
                git_path=string(raw, "git_path"),
                clone_url=string(raw, "clone_url", AppSettings().clone_url),
                profiles=profiles,
                selected_profile_key=string(raw, "selected_profile_key"),
                ignore_file_names=tuple(ignored),
                ui=layout,
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise SyncError("CONFIG_READ", f"配置读取失败，未重置原文件：{exc}", self.path) from exc

    def save(self, settings: AppSettings) -> None:
        value = asdict(settings)
        value["schema_version"] = 1
        try:
            atomic_write(self.path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        except OSError as exc:
            raise SyncError("CONFIG_WRITE", f"配置保存失败：{exc}", self.path) from exc
