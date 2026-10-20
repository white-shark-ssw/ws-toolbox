import importlib.util
import queue
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('immutable_v114', ROOT / 'ws_pack_tool_v114.py')
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)
from wspack import app as maintained


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture(autouse=True)
def reject_unexpected_dialog(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('Unexpected UI message: ' + repr(args))
    for name in ('showwarning', 'showerror', 'showinfo'):
        monkeypatch.setattr(legacy.messagebox, name, fail)


def make_app(module, base, source_root=None):
    source_root = source_root or base / 'source'
    source_root.mkdir(parents=True, exist_ok=True)
    store = module.DataStore(base / 'settings')
    safe_source = base / 'safe' / '!Setup.txt'
    safe_source.parent.mkdir(parents=True, exist_ok=True)
    safe_source.write_bytes(b'SAFE SETUP\r\n')
    group = module.PackGroup('sample', import_dir=str(source_root), export_dir=str(base / 'output'))
    store.import_safe_setup_template(group, str(safe_source))
    store.save_group(group)
    obj = object.__new__(module.App)
    obj.store = store
    obj.current_group_name = Value('sample')
    obj.current_rule_name = Value('rule')
    obj.import_dir_var = Value(str(source_root))
    obj.export_dir_var = Value(str(base / 'output'))
    obj.full_export_dir_var = Value(str(base / 'mother'))
    obj.hotfix_export_dir_var = Value(str(base / 'hotfix'))
    obj.dual_export_dir_mode = Value(True)
    obj.rename_export_zip = Value(True)
    obj.scan_entries = {}
    obj.tree_meta = {}
    obj.scan_queue = queue.Queue()
    obj.pack_queue = queue.Queue()
    obj.log = lambda *_: None
    return obj


def populate(root):
    data = {
        'Mir200/Envir/Market_Def/main.lua': 'local text = "中文"\r\nreturn text\r\n'.encode('gbk'),
        'Mir200/Envir/Market_Def/hidden.lua': b'return "hidden"\n',
        'Mir200/Envir/Market_Def/full_excluded.lua': b'return 2\n',
        'Mir200/Envir/Market_Def/rule_excluded.lua': b'return 3\n',
        'Mir200/Envir/Market_Def/block.lua': b'return 4\n',
        'Mir200/Envir/Market_Def/tool.exe': b'EXE',
        'Mir200/Envir/Market_Def/asset.txt': b'ASSET',
        'Mir200/!Setup.txt': b'RUNNING UNSAFE',
        'Mir200/GlobalVal.ini': b'RUNTIME',
        'Mir200/Envir/AdminList.txt': b'ADMIN',
        'Mir200/M2Server.map': b'DEBUG',
        'Mir200/Maps/game.map': b'MAP',
        'DBServer/config.txt': b'DB',
        'DBServer/DBServer.map': b'DEBUG',
        'DBServer/DBServer.elf': b'ELF',
        'DBServer/FDB/player.dat': b'PLAYER DATA',
        'Other/not-selected.txt': b'OTHER',
    }
    for rel, raw in data.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (root / 'Mir200/Envir/QuestDiary/通区数据/转区').mkdir(parents=True)
    return data


def custom_filters(obj):
    cfg = obj.store.load_filter_config()
    cfg.update(hidden_dirs=['DBServer'], hidden_files=['Mir200/Envir/Market_Def/hidden.lua'],
               full_package_exclude_files=['Mir200/Envir/Market_Def/full_excluded.lua'],
               rule_package_exclude_files=['Mir200/Envir/Market_Def/rule_excluded.lua'])
    cfg['blacklist_files'].append('Mir200/Envir/Market_Def/block.lua')
    obj.store.save_filter_config(cfg)
    return cfg


def canonical(items):
    result = []
    for item in items:
        value = {k: v for k, v in item.items() if k not in ('abs_path', 'mtime')}
        if item.get('type') == 'file':
            value['content'] = item.get('virtual_content')
            if value['content'] is None:
                value['content'] = Path(item['abs_path']).read_bytes()
        result.append(value)
    return result
