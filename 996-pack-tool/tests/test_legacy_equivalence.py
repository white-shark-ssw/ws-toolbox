"""Differential checks execute unchanged v114 and the extracted application."""
import ast
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from conftest import ROOT, legacy, maintained, make_app, populate, custom_filters, canonical


def test_baseline_source_is_immutable():
    assert hashlib.sha256((ROOT / 'ws_pack_tool_v114.py').read_bytes()).hexdigest() == 'df2d282f0232b7e2ef53447fa6dfac057659d6ef5986ed420a4cd6c1e1a3fc32'


def test_original_bodies_except_explicit_upgrade_points():
    # The mechanical-extraction commit verified EVERY method. The subsequent
    # feature commit changes only these integration points; other bodies remain
    # under the same structural comparison, including tree selection/interaction.
    changed = {('DataStore', 'load_group'), ('HelpDialog', '_build_ui')} | {('App', name) for name in (
        '__init__', '_save_ui_config', '_build_ui', '_clear_file_area', 'on_group_changed',
        'save_group', 'new_group', 'delete_group', 'refresh_files', '_set_pack_busy',
        '_show_pack_progress', '_close_pack_progress', '_start_pack_task', '_process_pack_queue', '_pack_worker')}
    original = ast.parse((ROOT / 'ws_pack_tool_v114.py').read_text('utf-8-sig'))
    copies = {}
    for path in (ROOT / 'wspack').glob('*.py'):
        for node in ast.parse(path.read_text('utf-8')).body:
            if isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, ast.FunctionDef):
                        owner = 'App' if node.name in ('FileScanner', 'FileCollector', 'ZipBuilder') else node.name
                        copies[owner, method.name] = ast.dump(method, include_attributes=False)
    for node in original.body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef):
                    if (node.name, method.name) not in changed:
                        assert copies[node.name, method.name] == ast.dump(method, include_attributes=False), (node.name, method.name)


@pytest.mark.parametrize('rel,is_dir', [
    ('Mir200', True), ('DBServer', True), ('Other', True),
    ('Mir200/Envir/Market_Def/main.lua', False), ('Mir200/Log/a.txt', False),
    ('Mir200/M2Server.map', False), ('Mir200/Maps/game.map', False),
    ('DBServer/DBServer.elf', False), ('Mir200/GlobalVal.ini', False),
])
def test_filter_functions_equal(tmp_path, rel, is_dir):
    a = make_app(legacy, tmp_path / 'old')
    cfg = custom_filters(a)
    from wspack import filters
    for name, arguments in [
        ('path_allowed', (rel, is_dir, cfg)),
        ('user_blacklist_excluded', (rel, is_dir, cfg)),
        ('ui_hidden_excluded', (rel, is_dir, cfg)),
        ('full_package_user_excluded', (rel, is_dir, cfg)),
        ('rule_package_user_excluded', (rel, is_dir, cfg)),
        ('is_full_package_excluded', (rel, is_dir)),
    ]:
        assert getattr(legacy, name)(*arguments) == getattr(filters, name)(*arguments)


@pytest.mark.parametrize('kind', ['mother', 'directory-rule', 'file-rule', 'checked'])
def test_selected_contents_and_zip_equal(tmp_path, kind):
    source = tmp_path / 'source'
    data = populate(source)
    results = []
    zip_results = []
    for label, module in [('old', legacy), ('new', maintained)]:
        obj = make_app(module, tmp_path / label, source)
        custom_filters(obj)
        if kind == 'mother':
            items = obj._collect_full_package_files()
        elif kind == 'directory-rule':
            # File added after opening/scanning must be included without a refresh.
            (source / 'Mir200/Envir/Market_Def/new.lua').write_bytes(b'return 8\n')
            rule = module.PackRule('r', ['Mir200', 'DBServer'], [], ['Mir200/Envir/Market_Def/asset.txt'])
            items = obj._collect_files_from_rule(rule)
        elif kind == 'file-rule':
            items = obj._collect_files_from_rule(module.PackRule('r', [], ['Mir200/Envir/Market_Def/main.lua', 'Mir200/!Setup.txt'], []))
        else:
            obj.tree_meta = {
                rel: dict(rel_path=rel, name=Path(rel).name, type='file', check_state=1,
                          abs_path=str(source / rel), size=len(raw), mtime=0, md5='', status='normal')
                for rel, raw in data.items()
                if rel in ['Mir200/Envir/Market_Def/main.lua', 'Mir200/!Setup.txt', 'Mir200/GlobalVal.ini', 'Mir200/Envir/AdminList.txt']
            }
            directory = 'Mir200/Envir/QuestDiary/通区数据/转区'
            obj.tree_meta[directory] = dict(rel_path=directory, type='dir', check_state=1)
            items = obj._collect_files_from_current_checked()
        results.append(canonical(items))
        output = tmp_path / label / 'zip'
        obj._pack_worker(str(output), 'MirServer', items)
        events = []
        while not obj.pack_queue.empty():
            events.append(obj.pack_queue.get_nowait())
        assert events[-1][0] == 'done', events[-1]
        with zipfile.ZipFile(output / 'MirServer.zip') as archive:
            zip_results.append({info.filename: (archive.read(info), info.create_system, info.external_attr, info.compress_type)
                                for info in archive.infolist()})
        assert not list(output.glob('_temp_build_*'))
    assert results[0] == results[1]
    assert zip_results[0] == zip_results[1]
    if kind == 'mother':
        content = zip_results[1]
        assert content['MirServer/Mir200/!Setup.txt'][0] == b'SAFE SETUP\r\n'
        assert content['MirServer/Mir200/Envir/AdminList.txt'][0] == b''
        assert 'MirServer/Mir200/Envir/QuestDiary/通区数据/转区/' in content
        assert 'MirServer/Mir200/Maps/game.map' in content
        assert 'MirServer/Mir200/M2Server.map' not in content
        assert 'MirServer/Mir200/GlobalVal.ini' not in content
        assert 'MirServer/DBServer/DBServer.elf' not in content


def test_scan_and_incremental_baseline_equal(tmp_path):
    source = tmp_path / 'source'
    populate(source)
    all_results = []
    for label, module in [('old', legacy), ('new', maintained)]:
        obj = make_app(module, tmp_path / label, source)
        cfg = custom_filters(obj)
        obj._quick_init_baseline_worker(1, 'sample', str(source), cfg)
        event = obj.scan_queue.get_nowait()
        assert event[0] == 'init_done'
        manifest = event[3]
        assert all(value['md5'] == '' for value in manifest['files'].values())
        obj._scan_worker(2, 'sample', str(source), cfg, {})
        event = obj.scan_queue.get_nowait()
        assert event[0] == 'done'
        obj.scan_entries = event[2]
        assert not any(rel.startswith('DBServer') for rel in obj.scan_entries)
        assert 'Mir200/Envir/Market_Def/hidden.lua' not in obj.scan_entries
        baseline = {rel: {key: meta[key] for key in ('size', 'mtime', 'md5')}
                    for rel, meta in obj.scan_entries.items() if meta['type'] == 'file'}
        baseline['Mir200/Envir/Market_Def/main.lua']['md5'] = 'changed-before-baseline'
        baseline['Mir200/Envir/Market_Def/main.lua']['mtime'] -= 3
        del baseline['Mir200/Envir/Market_Def/asset.txt']
        obj._scan_worker(3, 'sample', str(source), cfg, baseline)
        scanned = obj.scan_queue.get_nowait()
        all_results.append(canonical(list(scanned[2].values())))
        assert scanned[2]['Mir200/Envir/Market_Def/main.lua']['status'] == 'modified'
        assert scanned[2]['Mir200/Envir/Market_Def/asset.txt']['status'] == 'new'
        obj._update_baseline_for_files('sample', [scanned[2]['Mir200/Envir/Market_Def/main.lua']])
        saved = obj.store.load_manifest('sample')
        assert list(saved['files']) == ['Mir200/Envir/Market_Def/main.lua']
    assert all_results[0] == all_results[1]


def test_old_group_data_roundtrip(tmp_path):
    values = []
    for label, module in [('old', legacy), ('new', maintained)]:
        obj = make_app(module, tmp_path / label)
        raw = {'name': 'legacy', 'import_dir': 'D:/MirServer', 'export_dir': 'D:/Out',
               'export_presets': [{'name': 'old preset', 'path': 'D:/Out'}, {'path': 'D:/Second'}]}
        path = obj.store.group_json('legacy')
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(raw), encoding='utf-8')
        group = obj.store.load_group('legacy')
        obj.store.save_group(group)
        values.append(json.loads(path.read_text('utf-8')))
    assert values[0] == {key: value for key, value in values[1].items() if key not in ('client_import', 'client_export')}
    assert values[1]['client_import'] == values[1]['client_export'] == ''
