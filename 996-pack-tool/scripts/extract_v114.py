"""Mechanical extraction of the immutable v114 baseline; run once before changes.

The moved class/function/method source is copied verbatim. Resource roots are the
only adapted helper bodies because the entrypoint now lives above the package.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'wspack/app.py').exists():
    raise SystemExit('This one-time extraction must not overwrite the maintained package. Use a fresh baseline checkout.')
SOURCE = (ROOT / 'ws_pack_tool_v114.py').read_text(encoding='utf-8-sig')
LINES = SOURCE.splitlines(keepends=True)
TREE = ast.parse(SOURCE)
NODES = {n.name: n for n in TREE.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
HEADER = ''.join(LINES[1:15])
CONSTANTS = ''.join(LINES[16:40])


def source(node):
    start = min([node.lineno] + [x.lineno for x in getattr(node, 'decorator_list', [])])
    return ''.join(LINES[start - 1:node.end_lineno])


def write(name, content):
    path = ROOT / 'wspack' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='')


def units(names):
    return '\n\n'.join(source(NODES[name]) for name in names) + '\n'


write('__init__.py', '"""ws 996 packaging application."""\n')
write('constants.py', CONSTANTS)
write('models.py', HEADER + '\n' + units(['PackGroup', 'PackRule']))
filter_names = ['build_full_package_builtin_exclude_text', 'norm_rel', 'display_rule_path',
                'is_suffix_rule', 'normalize_rule_line', 'is_prefix_path', 'path_allowed',
                'user_blacklist_excluded', 'filter_list_excluded', 'ui_hidden_excluded',
                'full_package_user_excluded', 'rule_package_user_excluded',
                'append_unique_rule', 'is_force_hidden_path', 'is_setup_file',
                'is_admin_list_file', 'is_full_package_excluded']
write('filters.py', HEADER + '\nfrom .constants import *\n\n' + units(filter_names))
helpers = units(['md5_of_file', 'resource_path', 'config_base_dir'])
helpers = helpers.replace('Path(__file__).resolve().parent', 'Path(__file__).resolve().parent.parent')
write('files.py', HEADER + '\n' + helpers)
write('store.py', HEADER + '\nfrom .constants import *\nfrom .models import PackGroup, PackRule\nfrom .filters import norm_rel\nfrom .files import md5_of_file\n\n' + units(['DataStore']))
write('widgets.py', HEADER + '\n' + units(['PlaceholderEntry', 'center_child_window', 'center_root_window', 'WidgetTooltip']))
common = HEADER + '\nfrom .constants import *\nfrom .models import PackGroup, PackRule\nfrom .store import DataStore\nfrom .filters import *\nfrom .files import md5_of_file, resource_path, config_base_dir\nfrom .widgets import *\n'
write('dialogs.py', common + '\n' + units(['FilterEditor', 'AdvancedSettingsDialog', 'HelpDialog']))

groups = {
    'scanner.py': ('FileScanner', ['_build_shallow_entries', '_quick_init_baseline_worker', '_scan_worker']),
    'collector.py': ('FileCollector', ['_get_safe_setup_path_for_pack', '_build_safe_setup_meta', '_collect_files_from_current_checked', '_build_dir_meta', '_build_file_meta_from_disk', '_collect_files_from_rule', '_collect_full_package_files']),
    'archive.py': ('ZipBuilder', ['_pack_worker']),
}
methods = {n.name: n for n in NODES['App'].body if isinstance(n, ast.FunctionDef)}
moved = set()
for filename, (classname, names) in groups.items():
    write(filename, common + '\n\nclass ' + classname + ':\n' + '\n\n'.join(source(methods[n]) for n in names) + '\n')
    moved.update(names)
app_lines = LINES[NODES['App'].lineno - 1:NODES['App'].end_lineno]
remove = set()
for name in moved:
    node = methods[name]
    remove.update(range(node.lineno, node.end_lineno + 1))
app = ''.join(line for line_number, line in enumerate(app_lines, NODES['App'].lineno) if line_number not in remove)
app = app.replace('class App:', 'class App(FileScanner, FileCollector, ZipBuilder):', 1)
write('app.py', common + '\nfrom .dialogs import *\nfrom .scanner import FileScanner\nfrom .collector import FileCollector\nfrom .archive import ZipBuilder\n\n' + app + '\n\n' + units(['main']))
(ROOT / 'ws_pack_tool.py').write_text('"""Run the maintained application; v114 stays as the comparison baseline."""\nfrom wspack.app import main\n\nif __name__ == "__main__":\n    main()\n', encoding='utf-8')
print('Extracted', len(moved), 'methods without changing their bodies.')
