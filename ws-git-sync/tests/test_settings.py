import json
from dataclasses import replace

import pytest

from ws_git_sync.models import AppSettings, Profile, SyncError
from ws_git_sync.settings import DEFAULT_IGNORES, SettingsStore


def test_settings_roundtrip_preserves_paths_and_profiles(tmp_path):
    store = SettingsStore(tmp_path / "user/settings.json")
    initial = store.load()
    assert initial.ignore_file_names == DEFAULT_IGNORES and initial.ui.columns == 4
    profile = Profile("stable-key", "中文配置", r"D:\版本\MirServer", r"E:\客户端\GUILayout", "ver_001")
    settings = replace(initial, repo=r"D:\Git 仓库", profiles=(profile,), selected_profile_key=profile.key)
    store.save(settings)
    assert store.load() == settings
    assert list(store.path.parent.iterdir()) == [store.path]


@pytest.mark.parametrize(
    "raw",
    [
        b"broken",
        b'{"schema_version":999}',
        b'{"schema_version":true}',
        b'{"schema_version":1,"ignore_file_names":[3]}',
    ],
)
def test_corrupt_or_future_configuration_is_retained(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_bytes(raw)
    with pytest.raises(SyncError, match="未重置原文件"):
        SettingsStore(path).load()
    assert path.read_bytes() == raw


def test_atomic_write_failure_keeps_previous_config(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(repo="old"))
    old = store.path.read_bytes()
    monkeypatch.setattr(module.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(SyncError):
        store.save(AppSettings(repo="new"))
    assert store.path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [store.path]


def test_layout_limits_do_not_reset_business_configuration(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema_version": 1, "repo": "keep-me", "ui": {"sidebar": 9999, "font": -1}}))
    settings = SettingsStore(path).load()
    assert settings.repo == "keep-me" and settings.ui.sidebar == 360 and settings.ui.font == 12


def test_previous_layout_adopts_compact_editor_without_losing_configuration(tmp_path):
    from dataclasses import asdict

    profile = Profile("existing", "中文版本", "D:/MirServer", "D:/GUILayout", "ver_001")
    store = SettingsStore(tmp_path / "settings.json")
    store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repo": "D:/Git/Framework",
                "profiles": [asdict(profile)],
                "selected_profile_key": profile.key,
                "ui": {"editor": 190, "width": 1200, "height": 800},
            }
        )
    )
    settings = store.load()
    assert settings.ui.editor == 175
    assert (settings.ui.width, settings.ui.height) == (1200, 800)
    assert settings.profiles == (profile,) and settings.repo == "D:/Git/Framework"
    revised = replace(settings, ui=replace(settings.ui, editor=205))
    store.save(revised)
    assert store.load() == revised


def test_frozen_configuration_is_beside_executable_not_extraction_or_working_directory(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    executable = tmp_path / "便携工具" / "wsGitSync.exe"
    monkeypatch.setattr(module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(module.sys, "executable", str(executable))
    monkeypatch.setattr(module, "__file__", str(tmp_path / "_MEI123/ws_git_sync/settings.py"))
    monkeypatch.chdir(tmp_path)
    assert module.config_directory() == executable.parent


def test_source_configuration_is_beside_start_bat_regardless_of_working_directory(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    monkeypatch.setattr(module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "project/src/ws_git_sync/settings.py"))
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert module.config_directory() == tmp_path / "project"


def test_valid_legacy_configuration_is_copied_once_and_kept(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    old = tmp_path / "legacy"
    portable = tmp_path / "portable"
    settings = AppSettings(
        repo="D:/Git/Framework",
        profiles=(Profile("one", "中文版本", "D:/MirServer", "D:/GUILayout", "ver_001"),),
    )
    SettingsStore(old / "settings.json").save(settings)
    original = (old / "settings.json").read_bytes()
    monkeypatch.setattr(module, "legacy_config_directory", lambda: old)
    assert module.migrate_legacy_settings(portable)
    assert (portable / "settings.json").read_bytes() == original
    assert (old / "settings.json").read_bytes() == original
    SettingsStore(portable / "settings.json").save(replace(settings, repo="my portable repository"))
    assert not module.migrate_legacy_settings(portable)
    assert SettingsStore(portable / "settings.json").load().repo == "my portable repository"


def test_invalid_legacy_configuration_is_retained_without_creating_portable_config(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    old = tmp_path / "legacy"
    old.mkdir()
    (old / "settings.json").write_bytes(b"broken")
    monkeypatch.setattr(module, "legacy_config_directory", lambda: old)
    portable = tmp_path / "portable"
    with pytest.raises(SyncError, match="未重置原文件"):
        module.migrate_legacy_settings(portable)
    assert not portable.exists() and (old / "settings.json").read_bytes() == b"broken"


def test_portable_configuration_does_not_read_or_use_legacy_config(tmp_path, monkeypatch):
    import ws_git_sync.settings as module

    portable = tmp_path / "portable"
    SettingsStore(portable / "settings.json").save(AppSettings(repo="keep portable"))
    old = tmp_path / "legacy"
    old.mkdir()
    (old / "settings.json").write_bytes(b"broken")
    monkeypatch.setattr(module, "legacy_config_directory", lambda: old)
    assert not module.migrate_legacy_settings(portable)
    assert SettingsStore(portable / "settings.json").load().repo == "keep portable"
