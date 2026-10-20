import hashlib
import os
from pathlib import Path
import threading
import zipfile

import pytest

from conftest import make_app, maintained, populate, custom_filters, canonical
from wspack.pipeline import build_archive, export_client, plan_client, BuildError, TaskCancelled
from wspack.protection.processor import ProtectionOptions

MODES = [ProtectionOptions(), ProtectionOptions(True, False), ProtectionOptions(False, True), ProtectionOptions(True, True)]


@pytest.mark.parametrize('options', MODES)
@pytest.mark.parametrize('kind', ['mother', 'directory-rule', 'checked'])
def test_archive_all_modes_preserve_source_and_structure(tmp_path, options, kind):
    obj = make_app(maintained, tmp_path / 'app')
    source = Path(obj.import_dir_var.get())
    original = populate(source)
    custom_filters(obj)
    if kind == 'mother':
        items = obj._collect_full_package_files()
    elif kind == 'directory-rule':
        items = obj._collect_files_from_rule(maintained.PackRule('r', ['Mir200', 'DBServer'], [], []))
    else:
        rel = 'Mir200/Envir/Market_Def/main.lua'
        obj.tree_meta = {rel: dict(rel_path=rel, type='file', check_state=1, name='main.lua',
                                  abs_path=str(source / rel), size=0, mtime=0, md5='stale')}
        items = obj._collect_files_from_current_checked()
    result = build_archive(items, tmp_path / 'out', 'MirServer', options)
    assert result.files > 0
    with zipfile.ZipFile(result.output) as archive:
        for meta in items:
            rel = meta['rel_path']
            if meta['type'] == 'dir':
                assert 'MirServer/' + rel + '/' in archive.namelist()
            else:
                expected = meta.get('virtual_content')
                if expected is None:
                    expected = Path(meta['abs_path']).read_bytes()
                if isinstance(expected, str):
                    expected = expected.encode()
                actual = archive.read('MirServer/' + rel)
                report = next((r for r in result.reports if r['path'] == rel), None)
                if report and report.get('status') == 'protected':
                    assert report['output_sha256'] == hashlib.sha256(actual).hexdigest()
                if Path(rel).suffix.lower() != '.lua' or not options.enabled:
                    assert actual == expected
                elif options.encrypt:
                    assert actual.startswith(b'-- WS-LUA-PROTECTED')
                else:
                    assert b'local text' not in actual and b'--' not in actual
    assert all((source / rel).read_bytes() == raw for rel, raw in original.items())
    for record in result.source_records:
        if record['rel_path'] == 'Mir200/Envir/Market_Def/main.lua':
            assert record['md5'] == hashlib.md5(original[record['rel_path']]).hexdigest()
    assert {r['rel_path'] for r in result.source_records if r['type'] == 'dir'} == {r['rel_path'] for r in items if r['type'] == 'dir'}
    assert not list((tmp_path / 'out').glob('_temp_build_*'))


def test_archive_failure_keeps_existing_zip(tmp_path):
    output = tmp_path / 'out'
    output.mkdir()
    previous = output / 'MirServer.zip'
    previous.write_bytes(b'PREVIOUS VALID OUTPUT')
    source = tmp_path / 'broken.lua'
    source.write_bytes(b'local x=')
    items = [dict(rel_path='Mir200/broken.lua', type='file', abs_path=str(source))]
    with pytest.raises(BuildError, match='broken.lua'):
        build_archive(items, output, 'MirServer', ProtectionOptions(True, True))
    assert previous.read_bytes() == b'PREVIOUS VALID OUTPUT'
    assert not list(output.glob('_temp_build_*'))


@pytest.mark.parametrize('bad', ['../outside.lua', '/absolute.lua', 'C:/drive.lua', 'a/../../b.lua'])
def test_archive_rejects_path_escape(tmp_path, bad):
    with pytest.raises(BuildError):
        build_archive([dict(rel_path=bad, type='dir')], tmp_path / 'out', 'MirServer')


@pytest.mark.parametrize('options', MODES)
@pytest.mark.parametrize('single', [False, True])
def test_client_layout_and_flags(tmp_path, options, single):
    source = tmp_path / 'Client/dev/GUILayout/form'
    source.mkdir(parents=True)
    (source / 'a.lua').write_bytes(b'local x="Chinese" -- comment\nreturn x\n')
    (source / 'asset.txt').write_bytes(b'ignore')
    (source / 'nested').mkdir()
    (source / 'nested/b.LUA').write_bytes(b'local y=2\r\nreturn y\r\n')
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    output = tmp_path / 'release/client/dev/GUILayout/form'
    plan = plan_client(source / 'a.lua' if single else source, output)
    result = export_client(plan, options)
    generated = sorted(p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file())
    assert generated == (['a.lua'] if single else ['a.lua', 'nested/b.LUA'])
    assert result.lua_files == (1 if single else 2)
    assert not (output / 'form').exists()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert not list(output.parent.glob('.ws-client-build-*'))


def test_client_bad_file_keeps_all_previous_outputs(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'a.lua').write_bytes(b'return 1')
    (source / 'b.lua').write_bytes(b'local x=')
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'a.lua').write_bytes(b'OLD A')
    (output / 'b.lua').write_bytes(b'OLD B')
    with pytest.raises(BuildError, match='b.lua'):
        export_client(plan_client(source, output), ProtectionOptions(True, True))
    assert (output / 'a.lua').read_bytes() == b'OLD A'
    assert (output / 'b.lua').read_bytes() == b'OLD B'


def test_client_promotion_rolls_back(tmp_path, monkeypatch):
    source, output = tmp_path / 'input', tmp_path / 'output'
    source.mkdir()
    output.mkdir()
    for name in ('a.lua', 'b.lua'):
        (source / name).write_bytes(b'return 1')
        (output / name).write_bytes(('OLD ' + name).encode())
    original_replace = os.replace
    failed = False

    def fail_second(src, dst):
        nonlocal failed
        if not failed and Path(src).name == 'b.lua' and 'contents' in Path(src).parts:
            failed = True
            raise OSError('injected promotion failure')
        return original_replace(src, dst)

    monkeypatch.setattr(os, 'replace', fail_second)
    with pytest.raises(OSError, match='injected'):
        export_client(plan_client(source, output))
    assert failed
    for name in ('a.lua', 'b.lua'):
        assert (output / name).read_bytes() == ('OLD ' + name).encode()


def test_client_rejects_source_overwrite_and_non_folder_output(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.lua').write_bytes(b'return 1')
    for input_value, output in [(source, source), (source, source / 'nested'),
                                (source / 'a.lua', source), (source, source / 'a.lua')]:
        with pytest.raises(BuildError):
            plan_client(input_value, output)


def test_cancellation_keeps_previous_outputs(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'a.lua').write_bytes(b'return 1')
    event = threading.Event()
    event.set()
    output = tmp_path / 'output'
    with pytest.raises(TaskCancelled):
        export_client(plan_client(source, output), cancelled=event)
    with pytest.raises(TaskCancelled):
        build_archive([dict(type='file', rel_path='a.lua', abs_path=str(source / 'a.lua'))], output, 'MirServer', cancelled=event)


def test_mixed_source_encodings_are_retained(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    text = 'local value="等级突破" -- 注释\r\nreturn value\r\n'
    (source / 'gbk.lua').write_bytes(text.encode('gbk'))
    (source / 'utf8.lua').write_bytes(text.encode('utf-8'))
    result = export_client(plan_client(source, tmp_path / 'output'), ProtectionOptions(False, True))
    assert {r['encoding'] for r in result.reports} == {'gbk', 'utf-8'}
    for filename, encoding in [('gbk.lua', 'gbk'), ('utf8.lua', 'utf-8')]:
        raw = (tmp_path / 'output' / filename).read_bytes()
        assert '等级突破'.encode(encoding) in raw
        assert b'\r\n' in raw


def test_plain_packing_does_not_need_lupa(tmp_path):
    import subprocess
    import sys
    code = '''
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from wspack.pipeline import build_archive
root=Path(sys.argv[2]);src=root/'input.lua';src.write_bytes(b'NOT EVEN LUA')
result=build_archive([dict(type='file',rel_path='input.lua',abs_path=str(src))],root/'out','MirServer')
assert Path(result.output).is_file()
assert not any(k.startswith('lupa') for k in sys.modules)
'''
    repo = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, '-S', '-c', code, repo, str(tmp_path)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
