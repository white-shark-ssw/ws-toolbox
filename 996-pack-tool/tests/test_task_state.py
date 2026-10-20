import json

import pytest

from conftest import maintained, make_app
from wspack.pipeline import BuildResult
from wspack.protection.processor import ProtectionOptions
from wspack import task_ui


@pytest.mark.parametrize('kind', ['server', 'client'])
def test_completion_uses_captured_group(tmp_path, monkeypatch, kind):
    obj = make_app(maintained, tmp_path)
    obj.store.save_group(maintained.PackGroup('A'))
    obj.store.save_group(maintained.PackGroup('B', export_dir='B original'))
    obj.current_group_name.set('B')
    obj.pack_context = {'kind': kind, 'group_name': 'A', 'export_dir': str(tmp_path / 'out'),
                        'export_kind': 'hotfix', 'dual_mode': False, 'import_dir': 'A source',
                        'options': ProtectionOptions(True, True), 'update_baseline': False,
                        'client_import': 'A client input', 'client_export': 'A client output', 'open_after': False}
    obj._close_pack_progress = lambda: None
    obj._set_pack_busy = lambda value: None
    monkeypatch.setattr(task_ui.messagebox, 'showinfo', lambda *a, **k: None)
    result = BuildResult(str(tmp_path / 'out'), 1, 1, reports=[{'path': 'a.lua', 'output_sha256': 'public digest'}])
    obj._finish_task(result)
    a, b = obj.store.load_group('A'), obj.store.load_group('B')
    assert b.export_dir == 'B original' and b.client_import == b.client_export == ''
    if kind == 'server':
        assert a.export_dir == str(tmp_path / 'out') and a.import_dir == 'A source'
        assert a.export_presets == [str(tmp_path / 'out')]
    else:
        assert a.client_import == 'A client input' and a.client_export == 'A client output'
    reports = list((obj.store.base_dir / '.build_reports').glob('*.json'))
    assert len(reports) == 1
    record = json.loads(reports[0].read_text('utf-8'))
    assert record['group'] == 'A' and record['encrypt'] and record['obfuscate']
    assert not {'key', 'nonce', 'source', 'private_mapping'}.intersection(record)


def test_group_new_fields_roundtrip(tmp_path):
    obj = make_app(maintained, tmp_path)
    group = obj.store.load_group('sample')
    group.client_import, group.client_export = 'input.lua', 'output folder'
    obj.store.save_group(group)
    loaded = obj.store.load_group('sample')
    assert (loaded.client_import, loaded.client_export) == ('input.lua', 'output folder')
