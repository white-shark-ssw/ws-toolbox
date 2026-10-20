
import json
import os
import shutil
import hashlib
import zipfile
from pathlib import Path
from dataclasses import dataclass, asdict, field
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser
import sys
from typing import Dict, List, Optional
import threading
import queue

APP_TITLE = 'ws 996打包工具 By.白鲨 v0.114'
IMPORT_PLACEHOLDER = '请输入 MirServer 目录地址'
MAX_EXPORT_PRESETS = 20
SAFE_SETUP_FILENAME = '!Setup.txt'
ADMIN_LIST_REL_PATH = 'Mir200/Envir/AdminList.txt'
FORCE_HIDDEN_EXCLUDE_NAMES = {'globalval.ini'}
# !Setup.txt no longer globally hidden; if covered by pack/rule/check, use safe template.
SETUP_PROTECTED_NAMES = {'!setup.txt'}
FULL_PACKAGE_INCLUDE_ROOTS = ('DBServer', 'Mir200')
FULL_PACKAGE_EXCLUDE_DIRS = {
    'DBServer/DBServer.log',
    'DBServer/FDB',
    'DBServer/Log',
    'Mir200/Log',
}
FULL_PACKAGE_EXCLUDE_PATHS = {
    'DBServer/DBServer.map',
    'Mir200/M2Server.map',
}
FULL_PACKAGE_EXCLUDE_NAMES = {'globalval.ini'}
FULL_PACKAGE_EXCLUDE_SUFFIXES = (
    '.exe', '.dll', '.elf', '.json', '.code-snippets',
    '.tmp', '.temp', '.bak', '.backup', '.old', '.orig', '.swp', '.swo', '.log', '.pdb',
)


def build_full_package_builtin_exclude_text() -> str:
    """Return readonly builtin full-package exclusion rules for Advanced Settings."""
    lines = []
    if FULL_PACKAGE_EXCLUDE_DIRS:
        lines.append('[内置目录]')
        for x in sorted(FULL_PACKAGE_EXCLUDE_DIRS, key=lambda s: s.lower()):
            lines.append(display_rule_path(x, is_dir=True))
    if FULL_PACKAGE_EXCLUDE_PATHS:
        if lines:
            lines.append('')
        lines.append('[内置文件]')
        for x in sorted(FULL_PACKAGE_EXCLUDE_PATHS, key=lambda s: s.lower()):
            lines.append(display_rule_path(x, is_dir=False))
    if FULL_PACKAGE_EXCLUDE_NAMES:
        if lines:
            lines.append('')
        lines.append('[内置文件名]')
        for x in sorted(FULL_PACKAGE_EXCLUDE_NAMES):
            lines.append(x)
    if FULL_PACKAGE_EXCLUDE_SUFFIXES:
        if lines:
            lines.append('')
        lines.append('[内置后缀]')
        for x in FULL_PACKAGE_EXCLUDE_SUFFIXES:
            lines.append('*' + x)
    return '\n'.join(lines)


@dataclass
class PackGroup:
    name: str
    import_dir: str = ''
    export_dir: str = ''
    export_presets: List[str] = field(default_factory=list)
    full_export_dir: str = ''
    hotfix_export_dir: str = ''
    # safe_setup_path 仅保留为来源路径/追溯信息，不再参与打包读取。
    safe_setup_path: str = ''
    safe_setup_template: str = ''
    safe_setup_md5: str = ''
    safe_setup_import_time: str = ''


@dataclass
class PackRule:
    name: str
    checked_dirs: List[str]
    checked_files: List[str]
    excluded_files: List[str]


class DataStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.group_dir = base_dir / '.group'
        self.manifest_dir = base_dir / '.manifest'
        self.filter_path = base_dir / '.global_filter.json'
        self.ui_config_path = base_dir / '.ui_config.json'
        self.group_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        if not self.filter_path.exists():
            self.save_filter_config({
                'whitelist_dirs': ['Mir200/', 'DBServer/'],
                'whitelist_files': [],
                'blacklist_dirs': [
                    'Mir200/Envir/.vscode/', 'Mir200/Log/',
                    'DBServer/Log/', 'DBServer/DBServer.log/', 'DBServer/FDB/',
                    '.git/', '.svn/', '.vscode/', '.idea/', '__pycache__/', '.ws_pack/', 'ws_pack_config/',
                ],
                'blacklist_files': ['DBServer/DBServer.map', 'Mir200/M2Server.map'],
                'blacklist_suffixes': ['.exe', '.dll', '.elf', '.json', '.code-snippets', '.tmp', '.temp', '.bak', '.backup', '.old', '.orig', '.swp', '.swo', '.log', '.pdb'],
                'hidden_dirs': [],
                'hidden_files': [],
                'full_package_exclude_dirs': [],
                'full_package_exclude_files': [],
                'rule_package_exclude_dirs': [],
                'rule_package_exclude_files': [],
            })

    def group_root(self, group_name: str) -> Path:
        return self.group_dir / group_name

    def group_json(self, group_name: str) -> Path:
        return self.group_root(group_name) / 'group.json'

    def rules_dir(self, group_name: str) -> Path:
        return self.group_root(group_name) / 'rules'

    def safe_template_dir(self, group_name: str) -> Path:
        return self.group_root(group_name) / 'safe_template'

    def safe_template_file(self, group_name: str) -> Path:
        return self.safe_template_dir(group_name) / SAFE_SETUP_FILENAME

    def safe_template_rel(self, group_name: str) -> str:
        try:
            return self.safe_template_file(group_name).relative_to(self.base_dir).as_posix()
        except ValueError:
            return self.safe_template_file(group_name).as_posix()

    def resolve_safe_template(self, group: Optional[PackGroup]) -> Optional[Path]:
        if group is None:
            return None
        rel_value = str(getattr(group, 'safe_setup_template', '') or '').strip()
        if rel_value:
            p = Path(rel_value)
            if not p.is_absolute():
                p = self.base_dir / p
            return p
        p = self.safe_template_file(group.name)
        return p if p.exists() else None

    def import_safe_setup_template(self, group: PackGroup, source_path: str) -> PackGroup:
        src = Path(source_path)
        if src.name.lower() != SAFE_SETUP_FILENAME.lower():
            raise ValueError('请选择名为 !Setup.txt 的安全模板文件')
        if not src.is_file():
            raise FileNotFoundError(f'安全模板文件不存在：{source_path}')
        dst = self.safe_template_file(group.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        group.safe_setup_path = str(src)
        group.safe_setup_template = self.safe_template_rel(group.name)
        group.safe_setup_md5 = md5_of_file(dst)
        group.safe_setup_import_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return group

    def rule_json(self, group_name: str, rule_name: str) -> Path:
        return self.rules_dir(group_name) / f'{rule_name}.json'

    def manifest_json(self, group_name: str) -> Path:
        return self.manifest_dir / f'{group_name}.json'

    def list_groups(self) -> List[str]:
        result = []
        for p in self.group_dir.iterdir():
            group_json = p / 'group.json'
            if p.is_dir() and group_json.exists():
                try:
                    order_key = group_json.stat().st_mtime
                except OSError:
                    order_key = 0
                result.append((p.name, order_key))
        result.sort(key=lambda item: (-item[1], item[0]))
        return [name for name, _ in result]

    def save_group(self, group: PackGroup) -> None:
        root = self.group_root(group.name)
        root.mkdir(parents=True, exist_ok=True)
        self.rules_dir(group.name).mkdir(parents=True, exist_ok=True)
        with self.group_json(group.name).open('w', encoding='utf-8') as f:
            json.dump(asdict(group), f, ensure_ascii=False, indent=2)

    def load_group(self, group_name: str) -> Optional[PackGroup]:
        path = self.group_json(group_name)
        if not path.exists():
            return None
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return None

        export_dir = str(data.get('export_dir', '') or '').strip()
        raw_presets = data.get('export_presets')
        normalized_presets: List[str] = []
        seen = set()

        if isinstance(raw_presets, list):
            for item in raw_presets:
                # v80: [{'name': '单包', 'path': '...'}]
                if isinstance(item, dict):
                    path_value = str(item.get('path', '') or '').strip()
                    if not path_value:
                        path_value = str(item.get('name', '') or '').strip()
                else:
                    path_value = str(item or '').strip()
                key = norm_rel(path_value).lower()
                if path_value and key not in seen:
                    normalized_presets.append(path_value)
                    seen.add(key)

        if export_dir:
            key = norm_rel(export_dir).lower()
            if key in seen:
                normalized_presets = [export_dir] + [p for p in normalized_presets if norm_rel(p).lower() != key]
            else:
                normalized_presets.insert(0, export_dir)
                seen.add(key)

        normalized_presets = normalized_presets[:MAX_EXPORT_PRESETS]

        return PackGroup(
            name=str(data.get('name', group_name) or group_name),
            import_dir=str(data.get('import_dir', '') or '').strip(),
            export_dir=export_dir,
            export_presets=normalized_presets,
            full_export_dir=str(data.get('full_export_dir', '') or export_dir).strip(),
            hotfix_export_dir=str(data.get('hotfix_export_dir', '') or export_dir).strip(),
            safe_setup_path=str(data.get('safe_setup_path', '') or '').strip(),
            safe_setup_template=str(data.get('safe_setup_template', '') or '').strip(),
            safe_setup_md5=str(data.get('safe_setup_md5', '') or '').strip(),
            safe_setup_import_time=str(data.get('safe_setup_import_time', '') or '').strip(),
        )

    def list_rules(self, group_name: str) -> List[str]:
        rules_root = self.rules_dir(group_name)
        if not rules_root.exists():
            return []
        result = [p.stem for p in rules_root.glob('*.json') if p.is_file()]
        return sorted(result)

    def save_rule(self, group_name: str, rule: PackRule) -> None:
        self.rules_dir(group_name).mkdir(parents=True, exist_ok=True)
        with self.rule_json(group_name, rule.name).open('w', encoding='utf-8') as f:
            json.dump(asdict(rule), f, ensure_ascii=False, indent=2)

    def load_rule(self, group_name: str, rule_name: str) -> Optional[PackRule]:
        path = self.rule_json(group_name, rule_name)
        if not path.exists():
            return None
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return PackRule(**data)

    def load_manifest(self, group_name: str) -> Dict:
        path = self.manifest_json(group_name)
        if not path.exists():
            return {'group_name': group_name, 'updated_at': '', 'files': {}}
        with path.open('r', encoding='utf-8') as f:
            return json.load(f)

    def save_manifest(self, group_name: str, manifest: Dict) -> None:
        with self.manifest_json(group_name).open('w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def load_filter_config(self) -> Dict:
        with self.filter_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}

        # 配置迁移：v99 起 .elf 属于默认禁止上传的程序/调试产物。
        # 已存在旧配置时也自动补入黑名单后缀，避免母包或热更包误带 DBServer/DBServer.elf。
        suffixes = data.setdefault('blacklist_suffixes', [])
        if not isinstance(suffixes, list):
            suffixes = []
            data['blacklist_suffixes'] = suffixes
        normalized = {str(x).lower() for x in suffixes}
        changed = False
        if '.elf' not in normalized:
            suffixes.append('.elf')
            changed = True

        # v100 起新增独立列表：不显示、母包排除、规则包排除。
        # 这些都是全局配置，不跟随打包组。
        for key in (
            'hidden_dirs', 'hidden_files',
            'full_package_exclude_dirs', 'full_package_exclude_files',
            'rule_package_exclude_dirs', 'rule_package_exclude_files',
        ):
            if not isinstance(data.get(key), list):
                data[key] = []
                changed = True

        if changed:
            try:
                self.save_filter_config(data)
            except Exception:
                pass
        return data

    def save_filter_config(self, config: Dict) -> None:
        with self.filter_path.open('w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)


    def load_ui_config(self) -> Dict:
        if not self.ui_config_path.exists():
            return {}
        try:
            with self.ui_config_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_ui_config(self, config: Dict) -> None:
        with self.ui_config_path.open('w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)


def md5_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open('rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def norm_rel(path_str: str) -> str:
    return path_str.replace('\\', '/').strip().strip('/')


def display_rule_path(path_str: str, is_dir: bool = False) -> str:
    value = norm_rel(path_str).replace('/', '\\')
    if is_dir and value and not value.endswith('\\'):
        value += '\\'
    return value


def is_suffix_rule(value: str) -> bool:
    value = value.strip()
    return value.startswith('.') and '/' not in value and '\\' not in value


def normalize_rule_line(value: str, allow_suffix: bool = False):
    value = value.strip()
    if not value:
        return None, None
    if allow_suffix and is_suffix_rule(value):
        return 'suffix', value.lower()
    if value.endswith('/') or value.endswith('\\'):
        return 'dir', norm_rel(value)
    return 'file', norm_rel(value)


def is_prefix_path(prefix: str, rel_path: str) -> bool:
    p = norm_rel(prefix)
    r = norm_rel(rel_path)
    if not p:
        return False
    return r == p or r.startswith(p + '/')


def path_allowed(rel_path: str, is_dir: bool, filter_cfg: Dict) -> bool:
    rel_path = norm_rel(rel_path)
    wl_dirs = [norm_rel(x) for x in filter_cfg.get('whitelist_dirs', []) if x]
    wl_files = [norm_rel(x) for x in filter_cfg.get('whitelist_files', []) if x]
    bl_dirs = [norm_rel(x) for x in filter_cfg.get('blacklist_dirs', []) if x]
    bl_files = [norm_rel(x) for x in filter_cfg.get('blacklist_files', []) if x]
    bl_suffixes = [x.lower() for x in filter_cfg.get('blacklist_suffixes', []) if x]

    if wl_dirs or wl_files:
        allow = False
        if is_dir:
            if rel_path in wl_dirs:
                allow = True
            else:
                # 目录白名单有两层语义：
                # 1) 当前目录是白名单目录的祖先：需要显示，方便用户展开到白名单目录。
                # 2) 当前目录位于白名单目录内部：需要允许，规则打包实时扫描目录时不能误过滤子目录。
                for d in wl_dirs:
                    if is_prefix_path(d, rel_path) or d.startswith(rel_path + '/'):
                        allow = True
                        break
                if not allow:
                    for f in wl_files:
                        if f.startswith(rel_path + '/'):
                            allow = True
                            break
        else:
            if rel_path in wl_files:
                allow = True
            else:
                for d in wl_dirs:
                    if is_prefix_path(d, rel_path):
                        allow = True
                        break
        if not allow:
            return False

    for d in bl_dirs:
        if is_prefix_path(d, rel_path):
            return False
    if rel_path in bl_files:
        return False
    if not is_dir:
        low = rel_path.lower()
        for suf in bl_suffixes:
            if low.endswith(suf):
                return False
    return True



def user_blacklist_excluded(rel_path: str, is_dir: bool, filter_cfg: Dict) -> bool:
    """只应用用户黑名单，不应用用户白名单。

    母包根目录已经被 FULL_PACKAGE_INCLUDE_ROOTS 固定为 DBServer / Mir200，
    因此母包只允许用户黑名单继续收窄范围，不能通过白名单突破母包内置安全边界。
    """
    rel_path = norm_rel(rel_path)
    bl_dirs = [norm_rel(x) for x in filter_cfg.get('blacklist_dirs', []) if x]
    bl_files = [norm_rel(x) for x in filter_cfg.get('blacklist_files', []) if x]
    bl_suffixes = [str(x).lower() for x in filter_cfg.get('blacklist_suffixes', []) if x]

    for d in bl_dirs:
        if is_prefix_path(d, rel_path):
            return True
    if rel_path in bl_files:
        return True
    if not is_dir:
        low = rel_path.lower()
        for suf in bl_suffixes:
            if low.endswith(suf):
                return True
    return False

def filter_list_excluded(rel_path: str, is_dir: bool, filter_cfg: Dict, dir_key: str, file_key: str) -> bool:
    """按指定目录/文件列表判断是否排除。目录规则对子路径生效。"""
    rel_path = norm_rel(rel_path)
    dirs = [norm_rel(x) for x in filter_cfg.get(dir_key, []) if x]
    files = [norm_rel(x) for x in filter_cfg.get(file_key, []) if x]
    for d in dirs:
        if is_prefix_path(d, rel_path):
            return True
    return rel_path in files


def ui_hidden_excluded(rel_path: str, is_dir: bool, filter_cfg: Dict) -> bool:
    """不显示列表：只影响文件浏览区/刷新扫描/变化统计，不影响规则包和母包实时扫描。"""
    return filter_list_excluded(rel_path, is_dir, filter_cfg, 'hidden_dirs', 'hidden_files')


def full_package_user_excluded(rel_path: str, is_dir: bool, filter_cfg: Dict) -> bool:
    """用户母包排除：只影响生成母包。"""
    return filter_list_excluded(rel_path, is_dir, filter_cfg, 'full_package_exclude_dirs', 'full_package_exclude_files')


def rule_package_user_excluded(rel_path: str, is_dir: bool, filter_cfg: Dict) -> bool:
    """用户规则包排除：只影响按规则打包。"""
    return filter_list_excluded(rel_path, is_dir, filter_cfg, 'rule_package_exclude_dirs', 'rule_package_exclude_files')


def append_unique_rule(config: Dict, key: str, value: str) -> bool:
    value = norm_rel(value)
    if not value:
        return False
    items = config.setdefault(key, [])
    if not isinstance(items, list):
        items = []
        config[key] = items
    exists = {norm_rel(x).lower() for x in items}
    if value.lower() in exists:
        return False
    items.append(value)
    return True


def is_force_hidden_path(rel_path: str) -> bool:
    name = Path(norm_rel(rel_path)).name.lower()
    return name in FORCE_HIDDEN_EXCLUDE_NAMES


def is_setup_file(rel_path: str) -> bool:
    # !Setup.txt can appear in tree/rules, but pack source always uses current group's safe template.
    name = Path(norm_rel(rel_path)).name.lower()
    return name in SETUP_PROTECTED_NAMES


def is_admin_list_file(rel_path: str) -> bool:
    # AdminList.txt contains admin accounts; never use local real content in any package.
    return norm_rel(rel_path).lower() == ADMIN_LIST_REL_PATH.lower()


def is_full_package_excluded(rel_path: str, is_dir: bool) -> bool:
    rel = norm_rel(rel_path)
    low = rel.lower()
    name = Path(rel).name.lower()
    if name in FULL_PACKAGE_EXCLUDE_NAMES:
        return True
    if is_dir:
        for d in FULL_PACKAGE_EXCLUDE_DIRS:
            if is_prefix_path(d.lower(), low):
                return True
        return False
    if low in {p.lower() for p in FULL_PACKAGE_EXCLUDE_PATHS}:
        return True
    for d in FULL_PACKAGE_EXCLUDE_DIRS:
        if is_prefix_path(d.lower(), low):
            return True
    return any(low.endswith(suf.lower()) for suf in FULL_PACKAGE_EXCLUDE_SUFFIXES)


class PlaceholderEntry(tk.Entry):
    def __init__(self, master=None, textvariable=None, placeholder='', placeholder_color='#9a9a9a', normal_color='#000000', **kwargs):
        self.var = textvariable or tk.StringVar()
        self.placeholder = placeholder
        self.placeholder_color = placeholder_color
        self.normal_color = normal_color
        self._placeholder_visible = False
        self._syncing = False
        super().__init__(master, **kwargs)
        self.bind('<FocusIn>', self._hide_placeholder, add='+')
        self.bind('<FocusOut>', self._show_placeholder_if_needed, add='+')
        self.bind('<KeyRelease>', self._sync_var_from_entry, add='+')
        self.var.trace_add('write', self._sync_entry_from_var)
        self._sync_entry_from_var()
        self.after_idle(self._show_placeholder_if_needed)

    def _sync_var_from_entry(self, event=None):
        if self._syncing or self._placeholder_visible:
            return
        self._syncing = True
        try:
            self.var.set(super().get())
        finally:
            self._syncing = False

    def _sync_entry_from_var(self, *args):
        if self._syncing:
            return
        value = self.var.get()
        self._syncing = True
        try:
            self._placeholder_visible = False
            super().delete(0, 'end')
            if value:
                self.configure(fg=self.normal_color)
                super().insert(0, value)
            else:
                self._show_placeholder_if_needed()
        finally:
            self._syncing = False

    def _show_placeholder_if_needed(self, event=None):
        if self.focus_get() == self:
            return
        if not self.var.get():
            self._placeholder_visible = True
            self.configure(fg=self.placeholder_color)
            super().delete(0, 'end')
            super().insert(0, self.placeholder)

    def _hide_placeholder(self, event=None):
        if self._placeholder_visible:
            self._placeholder_visible = False
            self.configure(fg=self.normal_color)
            super().delete(0, 'end')

    def get(self):
        return '' if self._placeholder_visible else super().get()


def center_child_window(child: tk.Toplevel, parent: tk.Misc):
    child.update_idletasks()
    try:
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        if pw > 1 and ph > 1:
            x = px + (pw - child.winfo_width()) // 2
            y = py + (ph - child.winfo_height()) // 2
            child.geometry(f'+{max(0, x)}+{max(0, y)}')
            return
    except Exception:
        pass
    sw = child.winfo_screenwidth()
    sh = child.winfo_screenheight()
    x = (sw - child.winfo_width()) // 2
    y = (sh - child.winfo_height()) // 2
    child.geometry(f'+{max(0, x)}+{max(0, y)}')


def center_root_window(root: tk.Tk):
    root.update_idletasks()
    w = root.winfo_width() or root.winfo_reqwidth()
    h = root.winfo_height() or root.winfo_reqheight()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    root.geometry(f'{w}x{h}+{x}+{y}')


class WidgetTooltip:
    def __init__(self, widget: tk.Misc, text: str = '', delay_ms: int = 450):
        self.widget = widget
        self.text = text or ''
        self.delay_ms = delay_ms
        self.tip_window = None
        self._job = None
        self._last_xy = (0, 0)
        widget.bind('<Enter>', self._on_enter, add='+')
        widget.bind('<Motion>', self._on_motion, add='+')
        widget.bind('<Leave>', self._on_leave, add='+')
        widget.bind('<ButtonPress>', self._on_leave, add='+')

    def set_text(self, text: str):
        self.text = text or ''
        if self.tip_window is not None:
            self._show_now()

    def _on_enter(self, event=None):
        self._last_xy = self._event_xy(event)
        self._schedule()

    def _on_motion(self, event=None):
        self._last_xy = self._event_xy(event)
        if self.tip_window is not None:
            self._position_tip()

    def _on_leave(self, event=None):
        self._cancel()
        self._hide()

    def _event_xy(self, event):
        if event is None:
            return (self.widget.winfo_pointerx(), self.widget.winfo_pointery())
        return (event.x_root, event.y_root)

    def _schedule(self):
        self._cancel()
        if not self.text:
            return
        self._job = self.widget.after(self.delay_ms, self._show_now)

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show_now(self):
        self._job = None
        if not self.text:
            self._hide()
            return
        if self.tip_window is None or not self.tip_window.winfo_exists():
            self.tip_window = tk.Toplevel(self.widget)
            self.tip_window.wm_overrideredirect(True)
            self.tip_window.attributes('-topmost', True)
            self.tip_label = tk.Label(
                self.tip_window,
                text=self.text,
                justify='left',
                background='#fff8dc',
                foreground='#222222',
                relief='solid',
                borderwidth=1,
                padx=8,
                pady=5,
                font=('Microsoft YaHei UI', 9),
                wraplength=720,
            )
            self.tip_label.pack()
        else:
            self.tip_label.configure(text=self.text)
        self._position_tip()

    def _position_tip(self):
        if self.tip_window is None:
            return
        x, y = self._last_xy
        self.tip_window.update_idletasks()
        w = self.tip_window.winfo_reqwidth()
        h = self.tip_window.winfo_reqheight()
        sw = self.widget.winfo_screenwidth()
        sh = self.widget.winfo_screenheight()
        x = min(max(0, x + 14), max(0, sw - w - 4))
        y = min(max(0, y + 18), max(0, sh - h - 4))
        self.tip_window.wm_geometry(f'+{x}+{y}')

    def _hide(self):
        if self.tip_window is not None:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None



def resource_path(*parts) -> Path:
    if getattr(sys, '_MEIPASS', None):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def config_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class FilterEditor(tk.Toplevel):
    def __init__(self, master, store: DataStore, on_saved):
        super().__init__(master)
        self.title('过滤配置')
        self.geometry('980x700')
        self.minsize(840, 600)
        self.store = store
        self.on_saved = on_saved
        self.config_data = store.load_filter_config()
        self.current_tab = tk.StringVar(value='whitelist')
        self._build_ui()
        self._load_texts_from_config()
        self._switch_tab('whitelist')
        self.transient(master)
        self.grab_set()
        center_child_window(self, master)

    def _make_pack_button(self, parent, text: str, command, bg: str, active_bg: str, width: int = 10):
        # ttk.Button 在 Windows 主题下不稳定支持背景色，这里用 tk.Button 保证打包动作按钮可明显区分。
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg='#ffffff',
            activebackground=active_bg,
            activeforeground='#ffffff',
            disabledforeground='#d9d9d9',
            relief='raised',
            borderwidth=1,
            cursor='hand2',
            padx=8,
            pady=2,
        )

    def _build_ui(self):
        self.configure(bg='#f0f0f0')
        tab_bar = tk.Frame(self, bg='#f0f0f0')
        tab_bar.pack(fill='x', padx=10, pady=(10, 6))
        self.btn_white = tk.Button(tab_bar, text='白名单配置', width=16, command=lambda: self._switch_tab('whitelist'))
        self.btn_black = tk.Button(tab_bar, text='黑名单配置', width=16, command=lambda: self._switch_tab('blacklist'))
        self.btn_white.pack(side='left', padx=(0, 8))
        self.btn_black.pack(side='left')

        body = tk.Frame(self, bg='#f0f0f0')
        body.pack(fill='both', expand=True, padx=10, pady=6)

        self.white_frame = tk.Frame(body, bg='#f0f0f0')
        self.black_frame = tk.Frame(body, bg='#f0f0f0')

        for holder, title in ((self.white_frame, '白名单配置（每行一条，相对路径；目录建议以 / 结尾）'), (self.black_frame, '黑名单配置（每行一条；后缀写成 .log 这种形式）')):
            tk.Label(holder, text=title, anchor='w', bg='#f0f0f0').pack(fill='x', pady=(0, 4))

        self.white_text = tk.Text(self.white_frame, font=('Consolas', 11), wrap='none')
        self.black_text = tk.Text(self.black_frame, font=('Consolas', 11), wrap='none')
        self._make_text_with_scroll(self.white_frame, self.white_text)
        self._make_text_with_scroll(self.black_frame, self.black_text)

        bottom = ttk.Frame(self)
        bottom.pack(fill='x', padx=10, pady=(6, 10))
        self.left_btns = ttk.Frame(bottom)
        self.left_btns.pack(side='left')
        self.right_btns = ttk.Frame(bottom)
        self.right_btns.pack(side='right')

        self.btn_add_dir = ttk.Button(self.left_btns, text='添加目录', command=self._add_dir)
        self.btn_add_file = ttk.Button(self.left_btns, text='添加文件', command=self._add_file)
        self.btn_add_suffix = ttk.Button(self.left_btns, text='添加后缀', command=self._add_suffix)
        self.btn_del = ttk.Button(self.left_btns, text='删除当前行', command=self._delete_current_line)
        self.btn_clear = ttk.Button(self.left_btns, text='清空当前页', command=self._clear_current)
        for b in (self.btn_add_dir, self.btn_add_file, self.btn_add_suffix, self.btn_del, self.btn_clear):
            b.pack(side='left', padx=(0, 6))
        ttk.Button(self.right_btns, text='保存', command=self._save).pack(side='left', padx=(0, 6))
        ttk.Button(self.right_btns, text='取消', command=self.destroy).pack(side='left')

    def _make_text_with_scroll(self, parent, widget: tk.Text):
        wrap = tk.Frame(parent, bg='#f0f0f0')
        wrap.pack(fill='both', expand=True)
        yscroll = ttk.Scrollbar(wrap, orient='vertical', command=widget.yview)
        xscroll = ttk.Scrollbar(wrap, orient='horizontal', command=widget.xview)
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        widget.pack(side='left', fill='both', expand=True)
        yscroll.pack(side='right', fill='y')
        xscroll.pack(side='bottom', fill='x')

    def _set_tab_visual(self):
        active_bg = '#2f6fed'
        active_fg = '#ffffff'
        inactive_bg = '#d9d9d9'
        inactive_fg = '#555555'
        if self.current_tab.get() == 'whitelist':
            self.btn_white.configure(bg=active_bg, fg=active_fg, activebackground=active_bg, activeforeground=active_fg)
            self.btn_black.configure(bg=inactive_bg, fg=inactive_fg, activebackground=inactive_bg, activeforeground=inactive_fg)
        else:
            self.btn_black.configure(bg=active_bg, fg=active_fg, activebackground=active_bg, activeforeground=active_fg)
            self.btn_white.configure(bg=inactive_bg, fg=inactive_fg, activebackground=inactive_bg, activeforeground=inactive_fg)

    def _switch_tab(self, name: str):
        self.current_tab.set(name)
        self.white_frame.pack_forget()
        self.black_frame.pack_forget()
        if name == 'whitelist':
            self.white_frame.pack(fill='both', expand=True)
            self.btn_add_suffix.state(['disabled'])
        else:
            self.black_frame.pack(fill='both', expand=True)
            self.btn_add_suffix.state(['!disabled'])
        self._set_tab_visual()

    def _get_current_text_widget(self) -> tk.Text:
        return self.white_text if self.current_tab.get() == 'whitelist' else self.black_text

    def _load_texts_from_config(self):
        white_lines = []
        for x in self.config_data.get('whitelist_dirs', []):
            white_lines.append(display_rule_path(x, is_dir=True))
        for x in self.config_data.get('whitelist_files', []):
            white_lines.append(display_rule_path(x, is_dir=False))
        black_lines = []
        for x in self.config_data.get('blacklist_dirs', []):
            black_lines.append(display_rule_path(x, is_dir=True))
        for x in self.config_data.get('blacklist_files', []):
            black_lines.append(display_rule_path(x, is_dir=False))
        for x in self.config_data.get('blacklist_suffixes', []):
            black_lines.append(x.lower())
        self.white_text.delete('1.0', 'end')
        self.white_text.insert('1.0', '\n'.join(white_lines))
        self.black_text.delete('1.0', 'end')
        self.black_text.insert('1.0', '\n'.join(black_lines))

    def _bind_auto_save_texts(self):
        for text in self.text_widgets.values():
            text.bind('<<Modified>>', self._on_filter_text_modified, add='+')
            try:
                text.edit_modified(False)
            except tk.TclError:
                pass

    def _on_filter_text_modified(self, event=None):
        widget = event.widget if event is not None else None
        if widget is not None:
            try:
                if not widget.edit_modified():
                    return
                widget.edit_modified(False)
            except tk.TclError:
                pass
        if self._saving_texts:
            return
        self._schedule_auto_save_filters()

    def _schedule_auto_save_filters(self, delay_ms: int = 650):
        if self._auto_save_job is not None:
            try:
                self.after_cancel(self._auto_save_job)
            except Exception:
                pass
        self._auto_save_job = self.after(delay_ms, self._auto_save_filters)

    def _filter_signature_from_texts(self) -> str:
        parts = []
        for key, _title, _desc, _dir_key, _file_key, _suffix_key in self.TAB_META:
            text = self.text_widgets.get(key)
            parts.append(key + '=' + (text.get('1.0', 'end-1c') if text is not None else ''))
        return '\n--TAB--\n'.join(parts)

    def _auto_save_filters(self):
        self._auto_save_job = None
        current_sig = self._filter_signature_from_texts()
        if current_sig == self._last_saved_filter_signature:
            return
        if self._save_filters_to_store(close_after=False, refresh=True):
            self._last_saved_filter_signature = current_sig

    def _append_line(self, value: str):
        text = self._get_current_text_widget()
        current = text.get('1.0', 'end-1c')
        if current and not current.endswith('\n'):
            text.insert('end', '\n')
        text.insert('end', value)
        text.see('end')

    def _add_dir(self):
        value = simpledialog.askstring('添加目录', '请输入相对目录路径（建议以 / 结尾）：', parent=self)
        if not value:
            return
        value = display_rule_path(value, is_dir=True)
        self._append_line(value)

    def _add_file(self):
        value = simpledialog.askstring('添加文件', '请输入相对文件路径：', parent=self)
        if not value:
            return
        self._append_line(display_rule_path(value, is_dir=False))

    def _add_suffix(self):
        if self.current_tab.get() != 'blacklist':
            return
        value = simpledialog.askstring('添加后缀', '请输入后缀，例如 .log：', parent=self)
        if not value:
            return
        if not value.startswith('.'):
            value = '.' + value
        self._append_line(value.lower())

    def _delete_current_line(self):
        text = self._get_current_text_widget()
        idx = text.index('insert')
        line = idx.split('.')[0]
        text.delete(f'{line}.0', f'{line}.0 lineend+1c')
        self._schedule_auto_save_filters(250)

    def _clear_current(self):
        if not messagebox.askyesno('确认', '确定清空当前页吗？', parent=self):
            return
        self._get_current_text_widget().delete('1.0', 'end')

    def _parse_lines(self, raw: str, allow_suffix: bool = False):
        dirs, files, suffixes = [], [], []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            typ, value = normalize_rule_line(line, allow_suffix=allow_suffix)
            if not value:
                continue
            if typ == 'dir':
                if value not in dirs:
                    dirs.append(value)
            elif typ == 'file':
                if value not in files:
                    files.append(value)
            elif typ == 'suffix':
                if value not in suffixes:
                    suffixes.append(value)
        return dirs, files, suffixes

    def _save(self):
        wl_dirs, wl_files, _ = self._parse_lines(self.white_text.get('1.0', 'end-1c'), allow_suffix=False)
        bl_dirs, bl_files, bl_suffixes = self._parse_lines(self.black_text.get('1.0', 'end-1c'), allow_suffix=True)
        old_config = self.config_data if isinstance(self.config_data, dict) else {}
        self.config_data = {
            'whitelist_dirs': wl_dirs,
            'whitelist_files': wl_files,
            'blacklist_dirs': bl_dirs,
            'blacklist_files': bl_files,
            'blacklist_suffixes': bl_suffixes,
        }
        # 兼容旧入口“编辑过滤规则”：只编辑黑白名单，不覆盖高级设置中的其它列表。
        for key in (
            'hidden_dirs', 'hidden_files',
            'full_package_exclude_dirs', 'full_package_exclude_files',
            'rule_package_exclude_dirs', 'rule_package_exclude_files',
        ):
            self.config_data[key] = old_config.get(key, []) if isinstance(old_config.get(key, []), list) else []
        self.store.save_filter_config(self.config_data)
        if callable(self.on_saved):
            self.on_saved()
        self.destroy()



class AdvancedSettingsDialog(tk.Toplevel):
    TAB_META = [
        ('whitelist', '白名单', '白名单配置（每行一条，相对路径；目录建议以 / 结尾）', 'whitelist_dirs', 'whitelist_files', None),
        ('blacklist', '黑名单', '黑名单配置（每行一条；后缀写成 .log 这种形式）', 'blacklist_dirs', 'blacklist_files', 'blacklist_suffixes'),
        ('hidden', '不显示列表', '不显示列表（只影响文件区域/刷新扫描/变化统计；不影响母包和规则实时扫描）', 'hidden_dirs', 'hidden_files', None),
        ('full_package', '母包排除', '不参与母包（文件正常显示，可规则/勾选打包；只在生成母包时排除）', 'full_package_exclude_dirs', 'full_package_exclude_files', None),
        ('rule_package', '规则包排除', '不参与规则包（文件正常显示，可母包/勾选打包；只在按规则打包时排除）', 'rule_package_exclude_dirs', 'rule_package_exclude_files', None),
    ]

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.store = app.store
        self.config_data = self.store.load_filter_config()
        self.text_widgets: Dict[str, tk.Text] = {}
        self._auto_save_job = None
        self._saving_texts = False
        self._last_saved_filter_signature = ''
        self.title('高级设置')
        self.geometry('980x700')
        self.minsize(840, 600)
        self.transient(master)
        self.grab_set()
        self._build_ui()
        self._load_texts_from_config()
        self._bind_auto_save_texts()
        self._last_saved_filter_signature = self._filter_signature_from_texts()
        center_child_window(self, master)

    def _build_ui(self):
        body = ttk.Frame(self, padding=10)
        body.pack(fill='both', expand=True)

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill='both', expand=True)

        for key, title, desc, _dir_key, _file_key, _suffix_key in self.TAB_META:
            frame = ttk.Frame(self.notebook, padding=10)
            self.notebook.add(frame, text=title)
            ttk.Label(frame, text=desc, anchor='w').pack(fill='x', pady=(0, 6))

            if key == 'full_package':
                self._build_full_package_readonly_area(frame)

            text = tk.Text(frame, font=('Consolas', 11), wrap='none')
            self._make_text_with_scroll(frame, text)
            self.text_widgets[key] = text

        self.appearance_frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.appearance_frame, text='外观')
        self._build_appearance_tab(self.appearance_frame)

        bottom = ttk.Frame(body)
        bottom.pack(fill='x', pady=(10, 0))
        left = ttk.Frame(bottom)
        left.pack(side='left')
        right = ttk.Frame(bottom)
        right.pack(side='right')

        self.btn_add_dir = ttk.Button(left, text='添加目录', command=self._add_dir)
        self.btn_add_file = ttk.Button(left, text='添加文件', command=self._add_file)
        self.btn_add_suffix = ttk.Button(left, text='添加后缀', command=self._add_suffix)
        self.btn_del = ttk.Button(left, text='删除当前行', command=self._delete_current_line)
        self.btn_clear = ttk.Button(left, text='清空当前页', command=self._clear_current)
        for b in (self.btn_add_dir, self.btn_add_file, self.btn_add_suffix, self.btn_del, self.btn_clear):
            b.pack(side='left', padx=(0, 6))

        ttk.Button(right, text='保存', command=self._save).pack(side='left', padx=(0, 6))
        ttk.Button(right, text='取消', command=self.destroy).pack(side='left')
        self.notebook.bind('<<NotebookTabChanged>>', lambda _e=None: self._refresh_button_state())
        self._refresh_button_state()

    def _make_text_with_scroll(self, parent, widget: tk.Text):
        wrap = ttk.Frame(parent)
        wrap.pack(fill='both', expand=True)
        yscroll = ttk.Scrollbar(wrap, orient='vertical', command=widget.yview)
        xscroll = ttk.Scrollbar(wrap, orient='horizontal', command=widget.xview)
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        widget.pack(side='left', fill='both', expand=True)
        yscroll.pack(side='right', fill='y')
        xscroll.pack(side='bottom', fill='x')

    def _build_full_package_readonly_area(self, parent):
        box = ttk.LabelFrame(parent, text='工具内置母包排除规则（只读）')
        box.pack(fill='x', expand=False, pady=(0, 8))

        wrap = ttk.Frame(box)
        wrap.pack(fill='x', expand=False, padx=6, pady=6)

        readonly = tk.Text(
            wrap,
            height=5,
            font=('Consolas', 10),
            wrap='none',
            background='#f7f7f7',
            foreground='#555555',
            relief='solid',
            borderwidth=1,
        )
        yscroll = ttk.Scrollbar(wrap, orient='vertical', command=readonly.yview)
        xscroll = ttk.Scrollbar(wrap, orient='horizontal', command=readonly.xview)
        readonly.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        readonly.pack(side='left', fill='x', expand=True)
        yscroll.pack(side='right', fill='y')
        xscroll.pack(side='bottom', fill='x')

        readonly.insert('1.0', build_full_package_builtin_exclude_text())
        readonly.configure(state='disabled')
        self.full_package_builtin_text = readonly

    def _build_appearance_tab(self, parent):
        frame = ttk.LabelFrame(parent, text='外观')
        frame.pack(fill='x', expand=False)

        row = ttk.Frame(frame)
        row.pack(fill='x', padx=10, pady=(12, 8))
        ttk.Label(row, text='勾选颜色：').pack(side='left')
        self.color_preview = tk.Label(row, width=3, relief='solid', borderwidth=1, background=self.app.check_color)
        self.color_preview.pack(side='left', padx=(8, 6), ipady=5)
        self.color_text = ttk.Label(row, textvariable=self.app.check_color_var, width=10)
        self.color_text.pack(side='left')

        btns = ttk.Frame(frame)
        btns.pack(fill='x', padx=10, pady=(0, 12))
        ttk.Button(btns, text='选择颜色', command=self._choose_color).pack(side='left', padx=(0, 8))
        ttk.Button(btns, text='恢复默认', command=self._reset_color).pack(side='left')

        export_mode = ttk.LabelFrame(parent, text='导出模式')
        export_mode.pack(fill='x', expand=False, pady=(24, 0))

        mode_wrap = ttk.Frame(export_mode)
        mode_wrap.pack(fill='x', padx=12, pady=(12, 12))

        dual_box = ttk.Frame(mode_wrap)
        dual_box.pack(side='left', fill='x', expand=True, padx=(0, 18))
        dual_row = ttk.Frame(dual_box)
        dual_row.pack(fill='x')
        ttk.Checkbutton(
            dual_row,
            text='双导出目录',
            variable=self.app.dual_export_dir_mode,
            command=self.app.on_dual_export_dir_mode_toggle,
        ).pack(side='left')
        ttk.Label(
            dual_row,
            text='开启后：生成母包使用“导出母包”，规则/勾选打包使用“导出热更”。',
            foreground='#666666',
            anchor='w'
        ).pack(side='left', padx=(10, 0))
        ttk.Label(
            dual_box,
            text='双导出目录状态会自动保存到配置。',
            foreground='#888888',
            anchor='w'
        ).pack(fill='x', pady=(8, 0))

        rename_box = ttk.Frame(mode_wrap)
        rename_box.pack(side='left', fill='x', expand=True, padx=(0, 0))
        rename_row = ttk.Frame(rename_box)
        rename_row.pack(fill='x')
        ttk.Checkbutton(
            rename_row,
            text='是否重命名',
            variable=self.app.rename_export_zip,
            command=self.app.on_rename_export_zip_toggle,
        ).pack(side='left')
        ttk.Label(
            rename_box,
            text='勾选：压缩包固定为 MirServer.zip；取消：压缩包名称跟随导入目录名。',
            foreground='#888888',
            anchor='w'
        ).pack(fill='x', pady=(8, 0))

    def _current_key(self):
        current = self.notebook.select()
        for key, text in self.text_widgets.items():
            try:
                if str(text.master.master) == str(current):
                    return key
            except Exception:
                pass
        idx = self.notebook.index(current)
        if 0 <= idx < len(self.TAB_META):
            return self.TAB_META[idx][0]
        return 'appearance'

    def _current_meta(self):
        key = self._current_key()
        for item in self.TAB_META:
            if item[0] == key:
                return item
        return None

    def _refresh_button_state(self):
        meta = self._current_meta()
        if meta is None:
            for b in (self.btn_add_dir, self.btn_add_file, self.btn_add_suffix, self.btn_del, self.btn_clear):
                b.state(['disabled'])
            return
        self.btn_add_dir.state(['!disabled'])
        self.btn_add_file.state(['!disabled'])
        self.btn_del.state(['!disabled'])
        self.btn_clear.state(['!disabled'])
        if meta[5]:
            self.btn_add_suffix.state(['!disabled'])
        else:
            self.btn_add_suffix.state(['disabled'])

    def _get_current_text_widget(self):
        key = self._current_key()
        return self.text_widgets.get(key)

    def _load_texts_from_config(self):
        for key, _title, _desc, dir_key, file_key, suffix_key in self.TAB_META:
            lines = []
            for x in self.config_data.get(dir_key, []):
                lines.append(display_rule_path(x, is_dir=True))
            for x in self.config_data.get(file_key, []):
                lines.append(display_rule_path(x, is_dir=False))
            if suffix_key:
                for x in self.config_data.get(suffix_key, []):
                    lines.append(str(x).lower())
            text = self.text_widgets[key]
            text.delete('1.0', 'end')
            text.insert('1.0', '\n'.join(lines))


    def _bind_auto_save_texts(self):
        for text in self.text_widgets.values():
            text.bind('<<Modified>>', self._on_filter_text_modified, add='+')
            try:
                text.edit_modified(False)
            except tk.TclError:
                pass

    def _on_filter_text_modified(self, event=None):
        widget = event.widget if event is not None else None
        if widget is not None:
            try:
                if not widget.edit_modified():
                    return
                widget.edit_modified(False)
            except tk.TclError:
                pass
        if self._saving_texts:
            return
        self._schedule_auto_save_filters()

    def _schedule_auto_save_filters(self, delay_ms: int = 650):
        if self._auto_save_job is not None:
            try:
                self.after_cancel(self._auto_save_job)
            except Exception:
                pass
        self._auto_save_job = self.after(delay_ms, self._auto_save_filters)

    def _filter_signature_from_texts(self) -> str:
        parts = []
        for key, _title, _desc, _dir_key, _file_key, _suffix_key in self.TAB_META:
            text = self.text_widgets.get(key)
            parts.append(key + '=' + (text.get('1.0', 'end-1c') if text is not None else ''))
        return '\n--TAB--\n'.join(parts)

    def _auto_save_filters(self):
        self._auto_save_job = None
        current_sig = self._filter_signature_from_texts()
        if current_sig == self._last_saved_filter_signature:
            return
        if self._save_filters_to_store(close_after=False, refresh=True):
            self._last_saved_filter_signature = current_sig

    def _append_line(self, value: str):
        text = self._get_current_text_widget()
        if text is None:
            return
        current = text.get('1.0', 'end-1c')
        if current and not current.endswith('\n'):
            text.insert('end', '\n')
        text.insert('end', value)
        text.see('end')

    def _add_dir(self):
        if self._current_meta() is None:
            return
        value = simpledialog.askstring('添加目录', '请输入相对目录路径（建议以 / 结尾）：', parent=self)
        if not value:
            return
        self._append_line(display_rule_path(value, is_dir=True))

    def _add_file(self):
        if self._current_meta() is None:
            return
        value = simpledialog.askstring('添加文件', '请输入相对文件路径：', parent=self)
        if not value:
            return
        self._append_line(display_rule_path(value, is_dir=False))

    def _add_suffix(self):
        meta = self._current_meta()
        if meta is None or not meta[5]:
            return
        value = simpledialog.askstring('添加后缀', '请输入后缀，例如 .log：', parent=self)
        if not value:
            return
        if not value.startswith('.'):
            value = '.' + value
        self._append_line(value.lower())

    def _delete_current_line(self):
        text = self._get_current_text_widget()
        if text is None:
            return
        idx = text.index('insert')
        line = idx.split('.')[0]
        text.delete(f'{line}.0', f'{line}.0 lineend+1c')

    def _clear_current(self):
        text = self._get_current_text_widget()
        if text is None:
            return
        if not messagebox.askyesno('确认', '确定清空当前页吗？', parent=self):
            return
        text.delete('1.0', 'end')
        self._schedule_auto_save_filters(250)

    def _parse_lines(self, raw: str, allow_suffix: bool = False):
        dirs, files, suffixes = [], [], []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            typ, value = normalize_rule_line(line, allow_suffix=allow_suffix)
            if not value:
                continue
            if typ == 'dir':
                if value not in dirs:
                    dirs.append(value)
            elif typ == 'file':
                if value not in files:
                    files.append(value)
            elif typ == 'suffix':
                if value not in suffixes:
                    suffixes.append(value)
        return dirs, files, suffixes

    def _save_filters_to_store(self, close_after: bool = False, refresh: bool = True) -> bool:
        new_cfg = {}
        for key, _title, _desc, dir_key, file_key, suffix_key in self.TAB_META:
            text = self.text_widgets[key]
            dirs, files, suffixes = self._parse_lines(text.get('1.0', 'end-1c'), allow_suffix=bool(suffix_key))
            new_cfg[dir_key] = dirs
            new_cfg[file_key] = files
            if suffix_key:
                new_cfg[suffix_key] = suffixes
        # 保留未知字段，避免未来版本扩展被覆盖。
        for k, v in self.config_data.items():
            if k not in new_cfg:
                new_cfg[k] = v
        if new_cfg == self.config_data:
            if close_after:
                self.destroy()
            return False

        self._saving_texts = True
        try:
            self.config_data = new_cfg
            self.store.save_filter_config(self.config_data)
            if refresh and callable(getattr(self.app, '_on_filters_saved', None)):
                self.app._on_filters_saved()
        finally:
            self._saving_texts = False
        if close_after:
            self.destroy()
        return True

    def _save(self):
        self._save_filters_to_store(close_after=True, refresh=True)

    def _refresh_color_preview(self):
        try:
            self.color_preview.configure(background=self.app.check_color)
        except tk.TclError:
            pass

    def _choose_color(self):
        self.app.choose_check_color(parent=self)
        self._refresh_color_preview()

    def _reset_color(self):
        self.app.reset_check_color()
        self._refresh_color_preview()


class HelpDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title('使用说明')
        self.geometry('560x430')
        self.minsize(520, 380)
        self.transient(master)
        self.grab_set()
        self._build_ui()
        center_child_window(self, master)

    def _build_ui(self):
        body = ttk.Frame(self, padding=12)
        body.pack(fill='both', expand=True)

        text_frame = ttk.Frame(body)
        text_frame.pack(fill='both', expand=True)

        scroll = ttk.Scrollbar(text_frame)
        scroll.pack(side='right', fill='y')

        txt = tk.Text(
            text_frame,
            wrap='word',
            height=16,
            yscrollcommand=scroll.set,
            relief='sunken',
            borderwidth=1,
            font=('Microsoft YaHei UI', 10),
        )
        txt.pack(side='left', fill='both', expand=True)
        scroll.configure(command=txt.yview)

        content = (
            'ws 打包工具使用说明\n\n'
            '1. 基础流程\n'
            '   选择或新建打包组，导入目录填写 MirServer 根目录，导出目录填写压缩包输出位置。\n\n'
            '2. 安全 !Setup.txt\n'
            '   点击“导入模板”选择安全 !Setup.txt。工具会复制到当前打包组内部模板库。\n'
            '   后续母包、规则包、勾选包只要覆盖 !Setup.txt，均使用内部模板，不读取导入目录里的运行文件。\n'
            '   “查看模板”打开的是内部模板，可直接修改，修改后即作为新的有效安全文件。\n\n'
            '3. 母包打包\n'
            '   母包只处理 DBServer 和 Mir200。\n'
            '   Mir200/!Setup.txt 使用安全模板注入。\n'
            '   Mir200/Envir/AdminList.txt 由工具生成空文件，不引用本地管理员账号内容。\n\n'
            '4. 规则打包\n'
            '   规则中的文件按精确文件打包；规则中的目录会在打包时实时扫描，新文件会自动带入。\n\n'
            '5. 勾选打包\n'
            '   按当前文件树勾选结果打包，适合临时补丁或少量文件更新。\n\n'
            '6. 强制保护\n'
            '   GlobalVal.ini 永远隐藏并禁止打包。\n'
            '   AdminList.txt 不进入规则包和勾选包。\n'
            '   .map 不做通配排除，只固定排除 DBServer/DBServer.map 和 Mir200/M2Server.map。\n\n'
            '7. 右键规则\n'
            '   文件区域右键可复制相对路径、打开所在目录，并可加入不显示列表、黑名单、母包排除、规则包排除。\n'
            '   不显示列表只影响文件区域和变化扫描，不影响母包/规则实时扫描。\n'
            '   可勾选“显示隐藏项”临时查看被隐藏的文件，节点会显示 [隐藏]，右键可移出不显示列表。\n'
            '   母包排除只影响生成母包；规则包排除只影响按规则打包。\n'
            '   黑名单优先级高于白名单，同时命中时不显示也不打包。\n\n'
            '8. 高级设置\n'
            '   高级设置中可按页签编辑白名单、黑名单、不显示列表、母包排除、规则包排除和勾选颜色。\n\n'
            '9. 导出目录预设\n'
            '   导出目录成功打包后会自动保存到当前打包组历史。\n'
            '   “×”只移除当前历史记录并清空输入框，不删除真实文件夹。\n'
        )
        txt.insert('1.0', content)
        txt.configure(state='disabled')

        bottom = ttk.Frame(body)
        bottom.pack(fill='x', pady=(10, 0))
        ttk.Label(bottom, text='QQ：853169679', foreground='#d9534f').pack(side='left')
        ttk.Button(bottom, text='关闭', command=self.destroy).pack(side='right')


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('1220x820')
        self.root.minsize(1080, 720)

        self.store = DataStore(config_base_dir())

        self.current_group_name = tk.StringVar()
        self.current_rule_name = tk.StringVar()
        self.current_export_preset_name = tk.StringVar()
        self.import_dir_var = tk.StringVar()
        self.export_dir_var = tk.StringVar()
        self.full_export_dir_var = tk.StringVar()
        self.hotfix_export_dir_var = tk.StringVar()
        self.show_filtered_only = tk.BooleanVar(value=True)
        self.show_hidden_items = tk.BooleanVar(value=False)

        self.ui_config = self.store.load_ui_config()
        self.anchor_bar_enabled = tk.BooleanVar(value=bool(self.ui_config.get('anchor_bar_enabled', True)))
        self.anchor_overlay_hover = False
        self.anchor_overlay_weak_style = {
            # Tkinter 普通控件不支持子控件独立透明度，这里用接近文件树底色的配色模拟“高透明”。
            'background': '#ffffff',
            'foreground': '#c0c0c0',
            'relief': 'flat',
            'borderwidth': 0,
        }
        self.anchor_overlay_hover_style = {
            'background': '#fff3bf',
            'foreground': '#222222',
            'relief': 'solid',
            'borderwidth': 1,
        }
        self.rule_mapping_enabled = tk.BooleanVar(value=bool(self.ui_config.get('rule_mapping_enabled', False)))
        self.preview_before_pack = tk.BooleanVar(value=bool(self.ui_config.get('preview_before_pack', False)))
        self.open_export_dir_after_pack = tk.BooleanVar(value=bool(self.ui_config.get('open_export_dir_after_pack', False)))
        self.dual_export_dir_mode = tk.BooleanVar(value=bool(self.ui_config.get('dual_export_dir_mode', True)))
        self.rename_export_zip = tk.BooleanVar(value=bool(self.ui_config.get('rename_export_zip', True)))
        self.check_color = self._normalize_color(self.ui_config.get('check_color', '#23a559'))
        self.check_color_var = tk.StringVar(value=self.check_color)
        self.rule_dirty_var = tk.StringVar(value='规则状态：未选择')

        self.scan_entries: Dict[str, Dict] = {}
        self.tree_meta: Dict[str, Dict] = {}
        self.current_selected_iid: Optional[str] = None
        self.hover_iid: Optional[str] = None
        self.context_iid: Optional[str] = None
        self.context_menu = None
        self.context_menu_generation = 0
        self.frozen_anchor_iid: Optional[str] = None
        self.freeze_bar_visible = False
        self.freeze_pending_job = None
        self.freeze_pending_target: Optional[str] = None
        self.freeze_pending_text = ''
        self.freeze_bar_anim_job = None
        self.freeze_bar_current_height = 0
        self.freeze_bar_target_height = 0
        self.freeze_bar_packed = False
        self.shift_anchor_iid: Optional[str] = None
        self.loaded_group_name = ''
        self.loaded_rule_name = ''
        self._loaded_rule_signature = None
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self.browse_save_job = None
        self.drag_check_active = False
        self.drag_target_state = False
        self.drag_visited_iids = set()
        self.drag_last_iid: Optional[str] = None
        self.drag_visible_iids: List[str] = []
        self.drag_visible_index: Dict[str, int] = {}
        self.drag_refresh_pending = False
        self.scan_queue: queue.Queue = queue.Queue()
        self.scan_generation = 0
        self.scanning = False
        self.pack_queue: queue.Queue = queue.Queue()
        self.packing = False
        self.pack_dialog = None
        self.pack_progress = None
        self.pack_status_label = None
        self.pack_context = None
        self.icon_cache: Dict[str, tk.PhotoImage] = {}
        self._load_icons()

        self._build_ui()
        self._load_groups()
        if not self.anchor_bar_enabled.get():
            self._hide_frozen_anchor()
        self.root.after(120, self._process_scan_queue)
        self.root.after(120, self._process_pack_queue)
        self.log('工具启动完成')

    def _normalize_color(self, value: Optional[str]) -> str:
        if not isinstance(value, str):
            return '#23a559'
        value = value.strip()
        if len(value) == 7 and value.startswith('#'):
            hex_part = value[1:]
            if all(ch in '0123456789abcdefABCDEF' for ch in hex_part):
                return '#' + hex_part.lower()
        return '#23a559'

    def _save_ui_config(self):
        self.ui_config['check_color'] = self.check_color
        self.ui_config['anchor_bar_enabled'] = bool(self.anchor_bar_enabled.get())
        self.ui_config['rule_mapping_enabled'] = bool(self.rule_mapping_enabled.get())
        self.ui_config['preview_before_pack'] = bool(self.preview_before_pack.get())
        self.ui_config['open_export_dir_after_pack'] = bool(self.open_export_dir_after_pack.get())
        self.ui_config['dual_export_dir_mode'] = bool(self.dual_export_dir_mode.get())
        self.ui_config['rename_export_zip'] = bool(self.rename_export_zip.get())
        current_group = self.current_group_name.get().strip()
        if current_group:
            self.ui_config['current_group_name'] = current_group
        else:
            self.ui_config.pop('current_group_name', None)
        self.store.save_ui_config(self.ui_config)

    def _refresh_color_preview(self):
        if hasattr(self, 'check_color_preview'):
            try:
                self.check_color_preview.configure(background=self.check_color)
            except tk.TclError:
                pass
        if hasattr(self, 'check_color_var'):
            self.check_color_var.set(self.check_color)

    def _apply_check_color(self, color_value: str, log_message: Optional[str] = None):
        color_value = self._normalize_color(color_value)
        if color_value == self.check_color and 'check_checked' in self.icon_cache and 'check_partial' in self.icon_cache:
            self._refresh_color_preview()
            return
        self.check_color = color_value
        self._save_ui_config()
        self._build_check_icons()
        self.icon_cache['_combo'] = {}
        self._refresh_color_preview()
        if self.tree_meta:
            self._refresh_all_texts()
        if log_message:
            self.log(log_message)

    def choose_check_color(self, parent=None):
        result = colorchooser.askcolor(color=self.check_color, title='选择勾选颜色', parent=parent or self.root)
        color_value = result[1] if result else None
        if not color_value:
            return
        self._apply_check_color(color_value, f'已设置勾选颜色：{self._normalize_color(color_value)}')

    def reset_check_color(self):
        default_color = '#23a559'
        if self.check_color == default_color:
            self._refresh_color_preview()
            return
        self._apply_check_color(default_color, '已恢复默认勾选颜色')

    def _load_icons(self):
        self.icon_cache = {}
        icon_dir = resource_path('icons')

        def load_one(key: str, filename: str):
            path = icon_dir / filename
            if path.exists():
                try:
                    self.icon_cache[key] = tk.PhotoImage(file=str(path))
                except Exception:
                    pass

        if icon_dir.exists():
            load_one('folder', 'folder.png')
            load_one('file', 'file.png')
            for p in icon_dir.glob('*.png'):
                stem = p.stem.lower()
                if stem in ('folder', 'file'):
                    continue
                try:
                    self.icon_cache[stem] = tk.PhotoImage(file=str(p))
                except Exception:
                    pass

        self._build_check_icons()
        self.icon_cache['_combo'] = {}

    def _build_check_icons(self):
        def make_icon(state: str):
            img = tk.PhotoImage(width=16, height=16)
            border = '#6a6a6a'
            green = self.check_color
            white = '#ffffff'

            # box border
            img.put(border, to=(2, 2, 14, 3))
            img.put(border, to=(2, 13, 14, 14))
            img.put(border, to=(2, 2, 3, 14))
            img.put(border, to=(13, 2, 14, 14))

            if state == 'checked':
                img.put(green, to=(4, 4, 12, 12))
                # simple white tick
                img.put(white, to=(5, 8, 7, 10))
                img.put(white, to=(7, 9, 9, 11))
                img.put(white, to=(9, 7, 11, 9))
                img.put(white, to=(10, 5, 12, 7))
            elif state == 'partial':
                img.put(green, to=(4, 7, 12, 10))
            return img

        self.icon_cache['check_unchecked'] = make_icon('unchecked')
        self.icon_cache['check_checked'] = make_icon('checked')
        self.icon_cache['check_partial'] = make_icon('partial')

    def _compose_icons(self, left_icon, right_icon, key: str):
        combo_cache = self.icon_cache.setdefault('_combo', {})
        if key in combo_cache:
            return combo_cache[key]
        if left_icon is None and right_icon is None:
            return None
        if left_icon is None:
            combo_cache[key] = right_icon
            return right_icon
        if right_icon is None:
            combo_cache[key] = left_icon
            return left_icon

        try:
            lw = max(1, left_icon.width())
            lh = max(1, left_icon.height())
            rw = max(1, right_icon.width())
            rh = max(1, right_icon.height())
            h = max(lh, rh)
            img = tk.PhotoImage(width=lw + rw + 2, height=h)
            img.tk.call(str(img), 'copy', str(left_icon), '-from', 0, 0, lw, lh, '-to', 0, (h - lh) // 2)
            img.tk.call(str(img), 'copy', str(right_icon), '-from', 0, 0, rw, rh, '-to', lw + 2, (h - rh) // 2)
            combo_cache[key] = img
            return img
        except Exception:
            combo_cache[key] = left_icon
            return left_icon

    def _get_check_icon(self, meta: Dict):
        if meta.get('iid') == 'root' or meta.get('display_disabled'):
            return None
        state = meta.get('check_state', 0)
        if state == 1:
            return self.icon_cache.get('check_checked')
        if state == 2:
            return self.icon_cache.get('check_partial')
        return self.icon_cache.get('check_unchecked')

    def _get_filetype_icon(self, meta: Dict):
        if meta.get('iid') == 'root':
            return self.icon_cache.get('folder')
        if meta.get('type') == 'dir':
            return self.icon_cache.get('folder')
        name = str(meta.get('name', ''))
        if '.' in name:
            ext = name.rsplit('.', 1)[-1].lower()
            if ext in self.icon_cache:
                return self.icon_cache.get(ext)
        return self.icon_cache.get('file')

    def _get_icon_for_meta(self, meta: Dict):
        check_icon = self._get_check_icon(meta)
        file_icon = self._get_filetype_icon(meta)
        state_key = 'root' if meta.get('iid') == 'root' else str(meta.get('check_state', 0))
        kind_key = 'dir' if meta.get('type') == 'dir' else str(meta.get('name', 'file')).rsplit('.', 1)[-1].lower()
        return self._compose_icons(check_icon, file_icon, f'{state_key}|{kind_key}')

    def _get_loaded_group_object(self) -> Optional[PackGroup]:
        name = self.current_group_name.get().strip()
        if not name:
            return None
        return self.store.load_group(name)

    def _get_group_export_presets(self, group: Optional[PackGroup] = None) -> List[str]:
        if group is None:
            group = self._get_loaded_group_object()
        if group is None:
            return []
        presets = []
        seen = set()
        for item in getattr(group, 'export_presets', []) or []:
            if isinstance(item, dict):
                path_value = str(item.get('path', '') or '').strip()
            else:
                path_value = str(item or '').strip()
            key = norm_rel(path_value).lower()
            if path_value and key not in seen:
                presets.append(path_value)
                seen.add(key)
        return presets[:MAX_EXPORT_PRESETS]

    def _remember_export_dir(self, export_dir: str, export_kind: str = 'single'):
        export_dir = str(export_dir or '').strip()
        if not export_dir:
            return
        group = self._get_loaded_group_object()
        if group is None:
            return

        # 打包成功后自动补全当前打包组的导入目录。
        current_import_dir = self.import_dir_var.get().strip()
        if current_import_dir and not str(getattr(group, 'import_dir', '') or '').strip():
            group.import_dir = current_import_dir

        if self.dual_export_dir_mode.get():
            if export_kind == 'full':
                group.full_export_dir = export_dir
                self.full_export_dir_var.set(export_dir)
            elif export_kind == 'hotfix':
                group.hotfix_export_dir = export_dir
                self.hotfix_export_dir_var.set(export_dir)
            else:
                group.export_dir = export_dir
                self.export_dir_var.set(export_dir)
            self.store.save_group(group)
            return

        target_key = norm_rel(export_dir).lower()
        presets = [p for p in self._get_group_export_presets(group) if norm_rel(p).lower() != target_key]
        presets.insert(0, export_dir)
        group.export_presets = presets[:MAX_EXPORT_PRESETS]
        group.export_dir = export_dir
        self.store.save_group(group)
        self._refresh_export_preset_combo(group, keep_current=export_dir)

    def _refresh_export_preset_combo(self, group: Optional[PackGroup] = None, keep_current: Optional[str] = None):
        # v98: 切换打包组时必须以目标组配置为准。
        # 旧逻辑会在 keep_current=None 时优先读取当前界面的 export_dir_var，
        # 导致从 A 组切到空配置 B 组时，把 A 组导出目录残留到 B 组界面。
        if keep_current is not None:
            current = keep_current
        elif group is not None:
            current = getattr(group, 'export_dir', '') or ''
        else:
            current = self.export_dir_var.get().strip()
        self.export_dir_var.set(current)
        if group is not None:
            self.full_export_dir_var.set(getattr(group, 'full_export_dir', '') or current)
            self.hotfix_export_dir_var.set(getattr(group, 'hotfix_export_dir', '') or current)
        self.current_export_preset_name.set('预设▼')

    def show_export_preset_menu(self):
        presets = self._get_group_export_presets()
        menu = tk.Menu(self.root, tearoff=0)
        if not presets:
            menu.add_command(label='暂无导出目录历史', state='disabled')
        else:
            for path_value in presets:
                label = path_value
                menu.add_command(label=label, command=lambda p=path_value: self.export_dir_var.set(p))
        try:
            x = self.export_preset_button.winfo_rootx()
            y = self.export_preset_button.winfo_rooty() + self.export_preset_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def on_export_preset_changed(self, event=None):
        # 兼容旧版 Combobox 事件；v83 起预设改为按钮弹出菜单。
        return

    def clear_current_export_dir(self):
        path_value = self.export_dir_var.get().strip()
        if not path_value:
            return
        group = self._get_loaded_group_object()
        if group is not None:
            target_key = norm_rel(path_value).lower()
            group.export_presets = [p for p in self._get_group_export_presets(group) if norm_rel(p).lower() != target_key]
            if norm_rel(group.export_dir).lower() == target_key:
                group.export_dir = ''
            self.store.save_group(group)
            self._refresh_export_preset_combo(group, keep_current='')
        self.export_dir_var.set('')
        self.current_export_preset_name.set('预设▼')
        self.log(f'已清除导出目录记录：{path_value}')

    # v81: 导出目录历史由“打包成功”自动记录，不再提供手动新建/保存/删除预设。
    def new_export_preset(self):
        self.log('导出目录会在打包成功后自动加入历史')

    def save_export_preset(self):
        self.log('导出目录会在打包成功后自动加入历史')

    def delete_export_preset(self):
        self.clear_current_export_dir()

    def _resolve_export_dir(self, export_kind: str = 'single') -> str:
        if self.dual_export_dir_mode.get():
            if export_kind == 'full':
                return self.full_export_dir_var.get().strip()
            if export_kind == 'hotfix':
                return self.hotfix_export_dir_var.get().strip()
        return self.export_dir_var.get().strip()

    def _rebuild_export_dir_row(self):
        if not hasattr(self, 'export_row'):
            return
        for child in self.export_row.winfo_children():
            child.destroy()

        if self.dual_export_dir_mode.get():
            ttk.Label(self.export_row, text='导出母包:').pack(side='left')
            ttk.Entry(self.export_row, textvariable=self.full_export_dir_var, width=38).pack(side='left', fill='x', expand=True, padx=(6, 4))
            ttk.Button(self.export_row, text='浏览', width=6, command=lambda: self.pick_dir(self.full_export_dir_var)).pack(side='left', padx=(0, 4))
            ttk.Button(self.export_row, text='打开', width=6, command=lambda: self._open_directory(self.full_export_dir_var.get().strip())).pack(side='left', padx=(0, 10))
            ttk.Label(self.export_row, text='导出热更:').pack(side='left')
            ttk.Entry(self.export_row, textvariable=self.hotfix_export_dir_var, width=38).pack(side='left', fill='x', expand=True, padx=(6, 4))
            ttk.Button(self.export_row, text='浏览', width=6, command=lambda: self.pick_dir(self.hotfix_export_dir_var)).pack(side='left', padx=(0, 4))
            ttk.Button(self.export_row, text='打开', width=6, command=lambda: self._open_directory(self.hotfix_export_dir_var.get().strip())).pack(side='left')
        else:
            ttk.Label(self.export_row, text='导出目录:').pack(side='left')
            ttk.Entry(self.export_row, textvariable=self.export_dir_var).pack(side='left', fill='x', expand=True, padx=(6, 6))
            self.export_preset_button = ttk.Button(self.export_row, text='预设▼', width=8, command=self.show_export_preset_menu)
            self.export_preset_button.pack(side='left', padx=(0, 6))
            ttk.Button(self.export_row, text='×', width=3, command=self.clear_current_export_dir).pack(side='left', padx=(0, 6))
            ttk.Button(self.export_row, text='浏览', command=lambda: self.pick_dir(self.export_dir_var)).pack(side='left', padx=(0, 6))
            ttk.Button(self.export_row, text='打开', command=lambda: self._open_directory(self.export_dir_var.get().strip())).pack(side='left')

    def on_dual_export_dir_mode_toggle(self):
        self._save_ui_config()
        group = self._get_loaded_group_object()
        if group is not None:
            current = self.export_dir_var.get().strip()
            changed = False
            if self.dual_export_dir_mode.get():
                if not self.full_export_dir_var.get().strip():
                    self.full_export_dir_var.set(getattr(group, 'full_export_dir', '') or current)
                if not self.hotfix_export_dir_var.get().strip():
                    self.hotfix_export_dir_var.set(getattr(group, 'hotfix_export_dir', '') or current)
                group.full_export_dir = self.full_export_dir_var.get().strip()
                group.hotfix_export_dir = self.hotfix_export_dir_var.get().strip()
                changed = True
            else:
                if not self.export_dir_var.get().strip():
                    self.export_dir_var.set(getattr(group, 'export_dir', '') or self.hotfix_export_dir_var.get().strip() or self.full_export_dir_var.get().strip())
                group.export_dir = self.export_dir_var.get().strip()
                changed = True
            if changed:
                self.store.save_group(group)
        self._rebuild_export_dir_row()
        self.log('已开启双导出目录模式' if self.dual_export_dir_mode.get() else '已关闭双导出目录模式')

    def on_rename_export_zip_toggle(self):
        self._save_ui_config()
        if self.rename_export_zip.get():
            self.log('已开启导出包重命名：压缩包固定输出为 MirServer.zip')
        else:
            self.log('已关闭导出包重命名：压缩包名称跟随导入目录名')

    def _tree_insert_with_icon(self, parent_iid: str, iid: str, text_value: str, open_state: bool, tags=()):
        meta = self.tree_meta.get(iid, {})
        icon = self._get_icon_for_meta(meta) if meta else None
        kwargs = {
            'iid': iid,
            'text': text_value,
            'open': open_state,
        }
        if tags:
            kwargs['tags'] = tuple(tags)
        if icon is not None:
            kwargs['image'] = icon
        self.tree.insert(parent_iid, 'end', **kwargs)

    def _make_pack_button(self, parent, text: str, command, bg: str, active_bg: str, width: int = 10):
        # ttk.Button 在 Windows 主题下不稳定支持背景色，这里用 tk.Button 保证打包动作按钮可明显区分。
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg='#ffffff',
            activebackground=active_bg,
            activeforeground='#ffffff',
            disabledforeground='#d9d9d9',
            relief='raised',
            borderwidth=1,
            cursor='hand2',
            padx=8,
            pady=2,
        )

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill='x')

        row0 = ttk.Frame(top)
        row0.pack(fill='x', pady=2)
        ttk.Label(row0, text='打包组:').pack(side='left')
        self.group_combo = ttk.Combobox(row0, textvariable=self.current_group_name, state='readonly', width=30)
        self.group_combo.pack(side='left', padx=(6, 6))
        self.group_combo.bind('<<ComboboxSelected>>', self.on_group_changed)
        ttk.Button(row0, text='保存组', command=self.save_group).pack(side='left', padx=(0, 6))
        ttk.Button(row0, text='新建组', command=self.new_group).pack(side='left', padx=(0, 6))
        ttk.Button(row0, text='删除组', command=self.delete_group).pack(side='left')
        ttk.Button(row0, text='使用说明', command=self.open_help, width=10).pack(side='right')

        row1 = ttk.Frame(top)
        row1.pack(fill='x', pady=2)
        ttk.Label(row1, text='导入目录:').pack(side='left')
        self.import_entry = PlaceholderEntry(row1, textvariable=self.import_dir_var, placeholder=IMPORT_PLACEHOLDER, relief='sunken', borderwidth=1)
        self.import_entry.pack(side='left', fill='x', expand=True, padx=(6, 6))
        import_actions = ttk.Frame(row1)
        import_actions.pack(side='left')
        ttk.Button(import_actions, text='浏览', command=lambda: self.pick_dir(self.import_dir_var)).pack(fill='x')

        self.export_row = ttk.Frame(top)
        self.export_row.pack(fill='x', pady=2)
        self._rebuild_export_dir_row()

        row3 = ttk.Frame(top)
        row3.pack(fill='x', pady=2)
        ttk.Label(row3, text='母包:').pack(side='left')
        self.safe_setup_status_var = tk.StringVar(value='安全 !Setup.txt：未导入')
        self.safe_setup_status_label = ttk.Label(row3, textvariable=self.safe_setup_status_var, width=30, relief='sunken', anchor='w')
        self.safe_setup_status_label.pack(side='left', padx=(6, 6))
        self.safe_setup_tooltip = WidgetTooltip(self.safe_setup_status_label, '')
        ttk.Button(row3, text='导入模板', command=self.pick_safe_setup_template).pack(side='left', padx=(0, 6))
        ttk.Button(row3, text='查看模板', command=self.open_safe_setup_template).pack(side='left', padx=(0, 6))
        self.btn_pack_full = self._make_pack_button(row3, '生成母包', self.pack_full_package, '#7c3aed', '#6d28d9', width=10)
        self.btn_pack_full.pack(side='left')
        ttk.Button(row3, text='高级设置', command=self.open_other_settings).pack(side='right')

        row4 = ttk.Frame(top)
        row4.pack(fill='x', pady=2)
        ttk.Label(row4, text='规则:').pack(side='left')
        self.rule_combo = ttk.Combobox(row4, textvariable=self.current_rule_name, state='readonly', width=30)
        self.rule_combo.pack(side='left', padx=(6, 6))
        self.rule_combo.bind('<<ComboboxSelected>>', self.on_rule_changed)
        ttk.Button(row4, text='保存规则', command=self.save_rule).pack(side='left', padx=(0, 6))
        ttk.Button(row4, text='新建规则', command=self.new_rule).pack(side='left', padx=(0, 6))
        ttk.Button(row4, text='删除规则', command=self.delete_rule).pack(side='left', padx=(0, 6))
        self.btn_pack_rule = self._make_pack_button(row4, '按规则打包', self.pack_by_rule, '#2563eb', '#1d4ed8', width=11)
        self.btn_pack_rule.pack(side='left')
        self.rule_dirty_label = None

        row5 = ttk.Frame(top)
        row5.pack(fill='x', pady=2)
        ttk.Label(row5, text='勾选:').pack(side='left')
        self.btn_refresh = ttk.Button(row5, text='刷新扫描', command=self.refresh_files)
        self.btn_refresh.pack(side='left', padx=(6, 6))
        ttk.Button(row5, text='勾选变化', command=self.check_all_changed).pack(side='left', padx=(0, 6))
        ttk.Button(row5, text='清空勾选', command=self.clear_all_checks).pack(side='left', padx=(0, 6))
        self.btn_set_baseline = ttk.Button(row5, text='设为基线', command=self.set_current_baseline)
        self.btn_set_baseline.pack(side='left', padx=(0, 6))
        self.btn_pack_checked = self._make_pack_button(row5, '勾选打包', self.pack_by_checked, '#16a34a', '#15803d', width=10)
        self.btn_pack_checked.pack(side='left')

        content = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        content.pack(fill='both', expand=True)

        file_frame = ttk.LabelFrame(content, text='文件区域')
        file_frame.pack(fill='both', expand=True)

        file_ops = ttk.Frame(file_frame)
        file_ops.pack(fill='x', padx=8, pady=(8, 4))
        ttk.Button(file_ops, text='展开全部', command=self.expand_all).pack(side='left', padx=(0, 6))
        ttk.Button(file_ops, text='折叠全部', command=self.collapse_all).pack(side='left')
        ttk.Checkbutton(file_ops, text='开启目录锚点', variable=self.anchor_bar_enabled, command=self.on_anchor_bar_toggle).pack(side='left', padx=(18, 0))
        ttk.Checkbutton(file_ops, text='打包前预览', variable=self.preview_before_pack, command=self.on_preview_before_pack_toggle).pack(side='left', padx=(18, 0))
        ttk.Checkbutton(file_ops, text='规则勾选映射', variable=self.rule_mapping_enabled, command=self.on_rule_mapping_toggle).pack(side='left', padx=(24, 0))
        ttk.Checkbutton(file_ops, text='打包完成后打开导出目录', variable=self.open_export_dir_after_pack, command=self.on_open_export_dir_toggle).pack(side='left', padx=(18, 0))

        tree_wrap = ttk.Frame(file_frame)
        tree_wrap.pack(fill='both', expand=True, padx=8, pady=4)

        self.freeze_bar = tk.Frame(tree_wrap, height=0, bg='#ffffff', bd=0, highlightthickness=0)
        self.freeze_bar.pack(fill='x', side='top')
        self.freeze_bar.pack_propagate(False)
        self.freeze_bar_packed = True
        self.freeze_bar_label = tk.Label(
            self.freeze_bar,
            text='',
            anchor='w',
            cursor='hand2',
            background='#f7f7f7',
            padx=8,
            pady=4,
        )
        self.freeze_bar_label.pack(fill='x')
        self.freeze_bar_label.bind('<Button-1>', self.on_frozen_anchor_click)
        self.freeze_bar.bind('<Button-1>', self.on_frozen_anchor_click)
        self.freeze_bar_dash = tk.Canvas(self.freeze_bar, height=2, highlightthickness=0, bd=0, background='#ffffff')
        self.freeze_bar_dash.pack(fill='x')
        self.freeze_bar_dash.bind('<Configure>', self.on_freeze_dash_configure)
        self.freeze_bar_dash.bind('<Button-1>', self.on_frozen_anchor_click)

        self.tree_body = ttk.Frame(tree_wrap)
        self.tree_body.pack(fill='both', expand=True)
        tree_body = self.tree_body

        self.tree_style_name = 'WsPack.Treeview'
        self.tree_style = ttk.Style(self.root)
        normal_bg = self.tree_style.lookup('Treeview', 'background') or '#ffffff'
        normal_fg = self.tree_style.lookup('Treeview', 'foreground') or '#000000'
        self.tree_style.configure(self.tree_style_name, background=normal_bg, fieldbackground=normal_bg, foreground=normal_fg)
        self.tree_style.map(
            self.tree_style_name,
            background=[('selected', normal_bg)],
            foreground=[('selected', normal_fg)],
        )
        self.tree = ttk.Treeview(tree_body, show='tree', selectmode='browse', style=self.tree_style_name)
        self.tree.column('#0', stretch=True, minwidth=240, width=240)
        self.tree_ybar = ttk.Scrollbar(tree_body, orient='vertical', command=self.on_tree_ybar)
        self.tree.configure(yscrollcommand=self.on_tree_yscroll)
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree_ybar.pack(side='right', fill='y')
        self.tree.bind('<Configure>', self.on_tree_configure)
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.tree.bind('<Button-1>', self.on_tree_single_click)
        self.tree.bind('<Button-3>', self.on_tree_right_click)
        self.tree.bind('<Button-2>', self.on_tree_right_click)
        self.tree.bind('<space>', self.on_tree_toggle_selected)
        self.tree.bind('<Motion>', self.on_tree_motion)
        self.tree.bind('<B1-Motion>', self.on_tree_drag_motion)
        self.tree.bind('<ButtonRelease-1>', self.on_tree_drag_release)
        self.tree.bind('<Leave>', self.on_tree_leave)
        self.tree.tag_configure('new', foreground='#1f5eff')
        self.tree.tag_configure('modified', foreground='#d62d20')
        self.tree.tag_configure('filtered', foreground='#999999')
        self.tree.tag_configure('hidden', foreground='#8b5cf6')
        self.tree.tag_configure('hover', background='#eef4ff')
        self.tree.tag_configure('context', background='#dbeafe')

        # 目录锚点改为覆盖浮层，不占用文件树高度，避免滚动时文件区域被挤压产生抖动。
        self.anchor_overlay = tk.Label(
            self.tree,
            text='',
            anchor='w',
            cursor='hand2',
            padx=8,
            pady=3,
        )
        self._apply_anchor_overlay_style(False)
        self.anchor_overlay.bind('<Button-1>', self.on_frozen_anchor_click)
        self.anchor_overlay.bind('<Enter>', self.on_anchor_overlay_enter)
        self.anchor_overlay.bind('<Leave>', self.on_anchor_overlay_leave)
        self.anchor_overlay.place_forget()

        node_ops = ttk.Frame(file_frame)
        node_ops.pack(fill='x', padx=8, pady=(4, 8))
        self.current_node_label = ttk.Label(node_ops, text='当前节点: (无)')
        self.current_node_label.pack(side='left')
        ttk.Checkbutton(node_ops, text='按照过滤显示', variable=self.show_filtered_only, command=self.refresh_tree_only).pack(side='right')
        ttk.Checkbutton(node_ops, text='显示隐藏项', variable=self.show_hidden_items, command=self.on_show_hidden_items_toggle).pack(side='right', padx=(0, 10))

        log_frame = ttk.LabelFrame(content, text='日志输出区')
        log_frame.pack(fill='both', expand=False, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=11, state='disabled', font=('Consolas', 10))
        self.log_text.pack(fill='both', expand=True, padx=8, pady=8)

    def log(self, text: str):
        now = datetime.now().strftime('%H:%M:%S')
        self.log_text.configure(state='normal')
        self.log_text.insert('end', f'[{now}] {text}\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def pick_dir(self, var: tk.StringVar):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            var.set(path)

    def _load_groups(self):
        groups = self.store.list_groups()
        self.group_combo['values'] = groups
        if not groups:
            return

        current = self.current_group_name.get().strip()
        saved = str(self.ui_config.get('current_group_name', '') or '').strip()
        target = ''
        if current in groups:
            target = current
        elif saved in groups:
            target = saved
        else:
            target = groups[0]

        if target != current:
            self.current_group_name.set(target)
        self.on_group_changed()

    def _clear_file_area(self):
        self.tree.delete(*self.tree.get_children())
        self.scan_entries = {}
        self.tree_meta = {}
        self.current_selected_iid = None
        self.shift_anchor_iid = None
        self.current_node_label.configure(text='当前节点: (无)')
        self._hide_frozen_anchor()
        self._refresh_rule_dirty_state()

    def on_tree_yscroll(self, first, last):
        if hasattr(self, 'tree_ybar'):
            self.tree_ybar.set(first, last)
        self.root.after_idle(self.update_frozen_anchor)
        self._schedule_save_browse_position()

    def on_tree_ybar(self, *args):
        self.tree.yview(*args)
        self.root.after_idle(self.update_frozen_anchor)

    def on_freeze_dash_configure(self, event=None):
        canvas = getattr(self, 'freeze_bar_dash', None)
        if canvas is None:
            return
        canvas.delete('all')
        w = max(1, canvas.winfo_width())
        y = max(1, canvas.winfo_height() // 2)
        canvas.create_line(0, y, w, y, fill='#9a9a9a', dash=(4, 3))

    def _cancel_freeze_pending(self):
        job = getattr(self, 'freeze_pending_job', None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self.freeze_pending_job = None

    def _cancel_freeze_animation(self):
        job = getattr(self, 'freeze_bar_anim_job', None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self.freeze_bar_anim_job = None

    def _measure_freeze_bar_height(self):
        try:
            self.freeze_bar.update_idletasks()
            return max(28, self.freeze_bar_label.winfo_reqheight() + self.freeze_bar_dash.winfo_reqheight())
        except Exception:
            return 32

    def _start_freeze_bar_animation(self, target_height: int):
        if not hasattr(self, 'freeze_bar'):
            return
        self._cancel_freeze_animation()
        self.freeze_bar_target_height = max(0, int(target_height))

        def step():
            current = int(self.freeze_bar_current_height)
            target = int(self.freeze_bar_target_height)
            if current == target:
                self.freeze_bar_anim_job = None
                self.freeze_bar_visible = target > 0
                if target == 0:
                    self.freeze_bar_current_height = 0
                    self.freeze_bar.configure(height=0)
                    if self.freeze_bar_packed:
                        self.freeze_bar.pack_forget()
                        self.freeze_bar_packed = False
                return
            delta = target - current
            move = max(1, abs(delta) // 2)
            if abs(delta) <= 2:
                current = target
            else:
                current += move if delta > 0 else -move
            self.freeze_bar_current_height = current
            self.freeze_bar.configure(height=current)
            self.freeze_bar_visible = current > 0
            self.freeze_bar_anim_job = self.root.after(12, step)

        step()

    def _apply_anchor_overlay_style(self, hover: bool = False):
        if not hasattr(self, 'anchor_overlay'):
            return
        style = self.anchor_overlay_hover_style if hover else self.anchor_overlay_weak_style
        try:
            self.anchor_overlay.configure(**style)
        except Exception:
            pass

    def on_anchor_overlay_enter(self, event=None):
        self.anchor_overlay_hover = True
        self._apply_anchor_overlay_style(True)

    def on_anchor_overlay_leave(self, event=None):
        self.anchor_overlay_hover = False
        self._apply_anchor_overlay_style(False)

    def _show_frozen_anchor(self, iid: str, text: str):
        if not hasattr(self, 'anchor_overlay'):
            return
        self._cancel_freeze_pending()
        self.frozen_anchor_iid = iid
        self.freeze_bar_visible = True
        self.anchor_overlay.configure(text=f'目录锚点：{text}')
        self._apply_anchor_overlay_style(self.anchor_overlay_hover)
        try:
            self.anchor_overlay.place(x=6, y=6)
            self.anchor_overlay.lift()
        except Exception:
            pass

    def _hide_frozen_anchor(self):
        self._cancel_freeze_pending()
        self.freeze_pending_target = None
        self.freeze_pending_text = ''
        self.frozen_anchor_iid = None
        self.freeze_bar_visible = False
        self.anchor_overlay_hover = False
        if hasattr(self, 'anchor_overlay'):
            try:
                self.anchor_overlay.place_forget()
            except Exception:
                pass
        if hasattr(self, 'freeze_bar'):
            try:
                self.freeze_bar.configure(height=0)
            except Exception:
                pass

    def _get_anchor_dir_for_top_iid(self, iid: str):
        if not iid or iid not in self.tree_meta:
            return None
        meta = self.tree_meta[iid]
        if meta.get('iid') == 'root':
            return None
        anchor_iid = iid if meta.get('type') == 'dir' else meta.get('parent_iid')
        while anchor_iid and anchor_iid in self.tree_meta:
            anchor_meta = self.tree_meta[anchor_iid]
            if anchor_meta.get('iid') == 'root':
                return None
            if anchor_meta.get('type') == 'dir':
                return anchor_iid
            anchor_iid = anchor_meta.get('parent_iid')
        return None

    def update_frozen_anchor(self):
        if not self.anchor_bar_enabled.get():
            self._hide_frozen_anchor()
            return
        if not hasattr(self, 'tree') or not self.tree_meta:
            self._hide_frozen_anchor()
            return
        try:
            top_iid = self.tree.identify_row(2)
            if not top_iid:
                top_iid = self.tree.identify_row(18)
        except tk.TclError:
            self._hide_frozen_anchor()
            return
        if not top_iid or top_iid not in self.tree_meta:
            self._hide_frozen_anchor()
            return
        anchor_iid = self._get_anchor_dir_for_top_iid(top_iid)
        if not anchor_iid or anchor_iid == top_iid:
            self._hide_frozen_anchor()
            return
        anchor_meta = self.tree_meta.get(anchor_iid)
        if not anchor_meta:
            self._hide_frozen_anchor()
            return
        label = anchor_meta.get('rel_path') or anchor_meta.get('name') or ''
        self._show_frozen_anchor(anchor_iid, label)

    def on_frozen_anchor_click(self, event=None):
        iid = self.frozen_anchor_iid
        if not iid or iid not in self.tree_meta:
            return 'break'
        parent = self.tree.parent(iid)
        chain = []
        while parent:
            chain.append(parent)
            parent = self.tree.parent(parent)
        for node in reversed(chain):
            try:
                self.tree.item(node, open=True)
            except tk.TclError:
                pass
        try:
            visible = self._get_visible_iids_in_order()
            if iid in visible and len(visible) > 1:
                idx = visible.index(iid)
                self.tree.yview_moveto(max(0.0, idx / max(1, len(visible))))
            self.tree.see(iid)
        except tk.TclError:
            pass
        self.shift_anchor_iid = iid
        self._set_current_node_from_iid(iid)
        self.root.after_idle(self.update_frozen_anchor)
        return 'break'

    def on_tree_configure(self, event=None):
        if not hasattr(self, 'tree'):
            return
        width = self.tree.winfo_width()
        if width <= 1:
            return
        self.tree.column('#0', width=max(240, width - 4))
        self.root.after_idle(self.update_frozen_anchor)

    def _update_rule_combo_for_group(self, group_name: str):
        rules = self.store.list_rules(group_name) if group_name else []
        self.rule_combo['values'] = rules
        self.current_rule_name.set(rules[0] if rules else '')

    def on_group_changed(self, event=None):
        name = self.current_group_name.get().strip()
        if not name:
            return
        previous = self.loaded_group_name or ''
        if previous and name != previous:
            if not self._confirm_rule_dirty_before_leaving(f'切换到打包组“{name}”'):
                self.current_group_name.set(previous)
                return
        group = self.store.load_group(name)
        if not group:
            return
        self.import_dir_var.set(group.import_dir)
        self._refresh_export_preset_combo(group)
        self._refresh_safe_setup_status(group)
        self._update_rule_combo_for_group(name)
        self.loaded_group_name = name
        self.loaded_rule_name = self.current_rule_name.get().strip()
        self._loaded_rule_signature = None
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self._save_ui_config()
        self._clear_file_area()
        self._refresh_rule_dirty_state()
        self.log(f'已切换打包组：{name}')
        if self.current_rule_name.get().strip():
            self.log(f'当前组已载入规则：{self.current_rule_name.get().strip()}')
        if self.import_dir_var.get().strip() and Path(self.import_dir_var.get().strip()).exists():
            self.root.after(80, self.refresh_files)

    def on_rule_changed(self, event=None):
        new_rule_name = self.current_rule_name.get().strip()
        previous = self.loaded_rule_name or ''
        if previous and new_rule_name != previous:
            if not self._confirm_rule_dirty_before_leaving(f'切换到规则“{new_rule_name}”'):
                self.current_rule_name.set(previous)
                return
        self.loaded_group_name = self.current_group_name.get().strip()
        self.loaded_rule_name = new_rule_name
        self._loaded_rule_signature = self._rule_signature_from_rule(self.store.load_rule(self.loaded_group_name, new_rule_name))
        if self.rule_mapping_enabled.get():
            self.load_rule_to_ui()
        else:
            self._refresh_rule_dirty_state()

    def on_anchor_bar_toggle(self):
        self._save_ui_config()
        if self.anchor_bar_enabled.get():
            self.update_frozen_anchor()
            self.log('已开启目录锚点')
        else:
            self._hide_frozen_anchor()
            self.log('已关闭目录锚点')

    def on_rule_mapping_toggle(self):
        self._save_ui_config()
        if self.rule_mapping_enabled.get():
            self.load_rule_to_ui()
        else:
            self.log('已关闭规则勾选映射')

    def on_preview_before_pack_toggle(self):
        self._save_ui_config()
        if self.preview_before_pack.get():
            self.log('已开启打包前预览')
        else:
            self.log('已关闭打包前预览')

    def on_open_export_dir_toggle(self):
        self._save_ui_config()
        if self.open_export_dir_after_pack.get():
            self.log('已开启打包完成后打开导出目录')
        else:
            self.log('已关闭打包完成后打开导出目录')

    def _rule_signature_from_rule(self, rule: Optional[PackRule]):
        if not rule:
            return None
        return (
            tuple(sorted({norm_rel(x) for x in rule.checked_dirs})),
            tuple(sorted({norm_rel(x) for x in rule.checked_files})),
            tuple(sorted({norm_rel(x) for x in rule.excluded_files})),
        )

    def _current_ui_rule_signature(self):
        if not self.tree_meta:
            return None
        rule = self._rule_from_current_ui('__current__')
        return self._rule_signature_from_rule(rule)

    def _update_rule_dirty_prompt(self, is_dirty: bool, reason: str = ''):
        self.rule_dirty_var.set('')
        if getattr(self, 'rule_dirty_label', None) is not None:
            try:
                self.rule_dirty_label.pack_forget()
            except Exception:
                pass

    def _mark_current_rule_dirty(self, reason: str = '已修改勾选'):
        if not self.loaded_group_name or not self.loaded_rule_name:
            return
        self.rule_dirty_by_user = True
        self.rule_dirty_reason = reason
        self._update_rule_dirty_prompt(True, reason)

    def _refresh_rule_dirty_state(self):
        if not self.loaded_group_name or not self.loaded_rule_name:
            self._update_rule_dirty_prompt(False)
            return False
        if not self.tree_meta:
            self._update_rule_dirty_prompt(False)
            return False
        if not self.rule_dirty_by_user:
            self._update_rule_dirty_prompt(False)
            return False
        current_sig = self._current_ui_rule_signature()
        is_dirty = current_sig != self._loaded_rule_signature
        if not is_dirty:
            self.rule_dirty_by_user = False
            self.rule_dirty_reason = ''
        self._update_rule_dirty_prompt(is_dirty, self.rule_dirty_reason if is_dirty else '')
        return is_dirty

    def _mark_current_rule_clean(self):
        self.loaded_group_name = self.current_group_name.get().strip()
        self.loaded_rule_name = self.current_rule_name.get().strip()
        if self.loaded_group_name and self.loaded_rule_name:
            rule = self.store.load_rule(self.loaded_group_name, self.loaded_rule_name)
            self._loaded_rule_signature = self._rule_signature_from_rule(rule)
        else:
            self._loaded_rule_signature = None
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self._refresh_rule_dirty_state()

    def _confirm_rule_dirty_before_leaving(self, action_desc: str) -> bool:
        return True

    def _schedule_save_browse_position(self):
        group_name = self.current_group_name.get().strip()
        if not group_name or not hasattr(self, 'tree'):
            return
        if self.browse_save_job is not None:
            try:
                self.root.after_cancel(self.browse_save_job)
            except Exception:
                pass
        self.browse_save_job = self.root.after(180, self._save_current_browse_position)

    def _save_current_browse_position(self):
        self.browse_save_job = None
        group_name = self.current_group_name.get().strip()
        if not group_name or not hasattr(self, 'tree'):
            return
        browse_positions = self.ui_config.setdefault('browse_positions', {})
        selected_rel = ''
        iid = self.current_selected_iid
        if iid and iid in self.tree_meta:
            selected_rel = self.tree_meta[iid].get('rel_path', '') or ''
        try:
            first = float(self.tree.yview()[0])
        except Exception:
            first = 0.0
        browse_positions[group_name] = {
            'selected_rel_path': selected_rel,
            'yview_first': first,
        }
        self._save_ui_config()

    def _scroll_to_anchor_iid(self, iid: str):
        if not iid or iid not in self.tree_meta:
            return
        chain = []
        parent = self.tree.parent(iid)
        while parent:
            chain.append(parent)
            parent = self.tree.parent(parent)
        for node in reversed(chain):
            try:
                self.tree.item(node, open=True)
            except tk.TclError:
                pass
        try:
            self.tree.see(iid)
        except tk.TclError:
            pass

    def _restore_browse_position_after_refresh(self):
        group_name = self.current_group_name.get().strip()
        if not group_name or not self.tree_meta:
            return
        browse_positions = self.ui_config.get('browse_positions', {}) or {}
        state = browse_positions.get(group_name)
        if not isinstance(state, dict):
            return
        selected_rel = norm_rel(str(state.get('selected_rel_path', '') or ''))
        yview_first = state.get('yview_first', 0.0)
        target_iid = None
        if selected_rel:
            target_iid = f'n::{selected_rel}'
            if target_iid not in self.tree_meta:
                target_iid = None
        if target_iid:
            self._scroll_to_anchor_iid(target_iid)
            self.current_selected_iid = target_iid
            self.shift_anchor_iid = target_iid
            self._set_current_node_from_iid(target_iid)
            for node_iid in self.tree_meta.keys():
                self._update_item_tags(node_iid)
        else:
            try:
                yview_first = max(0.0, min(1.0, float(yview_first)))
                self.tree.yview_moveto(yview_first)
            except Exception:
                pass
        self.root.after_idle(self.update_frozen_anchor)

    def _refresh_safe_setup_status(self, group: Optional[PackGroup] = None):
        if group is None:
            group = self._get_loaded_group_object()
        status = '安全 !Setup.txt：未导入'
        tooltip = '当前打包组尚未导入安全 !Setup.txt 模板。\n打母包或更新 !Setup.txt 前，请先导入模板。'
        if group is not None:
            template_path = self.store.resolve_safe_template(group)
            source_path = str(getattr(group, 'safe_setup_path', '') or '').strip()
            import_time = str(getattr(group, 'safe_setup_import_time', '') or '').strip()
            md5_value = str(getattr(group, 'safe_setup_md5', '') or '').strip()
            if template_path and template_path.is_file():
                status = '安全 !Setup.txt：已导入'
                if import_time:
                    status = f'安全 !Setup.txt：已导入 {import_time[:10]}'
                lines = ['安全 !Setup.txt：已导入']
                if import_time:
                    lines.append(f'导入时间：{import_time}')
                lines.append(f'内部模板：{template_path}')
                if source_path:
                    lines.append(f'来源路径：{source_path}')
                if md5_value:
                    lines.append(f'导入记录 MD5：{md5_value}')
                lines.append('说明：打包只读取工具内部模板；允许直接修改该内部模板，修改后即为当前有效安全文件。')
                tooltip = '\n'.join(lines)
            elif template_path:
                status = '安全 !Setup.txt：模板丢失'
                tooltip = f'工具内部安全模板不存在，请重新导入。\n内部模板：{template_path}'
            elif source_path:
                status = '安全 !Setup.txt：旧配置需重新导入'
                tooltip = f'当前打包组仍是旧版外部路径配置，需要重新导入安全模板。\n旧来源路径：{source_path}'
        if hasattr(self, 'safe_setup_status_var'):
            self.safe_setup_status_var.set(status)
        if hasattr(self, 'safe_setup_tooltip'):
            self.safe_setup_tooltip.set_text(tooltip)

    def pick_safe_setup_template(self):
        group = self._get_loaded_group_object()
        if group is None:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        path = filedialog.askopenfilename(parent=self.root, title='导入安全 !Setup.txt 模板', filetypes=[('!Setup.txt', '!Setup.txt'), ('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            self.store.import_safe_setup_template(group, path)
        except Exception as exc:
            messagebox.showwarning('提示', str(exc))
            return
        group.import_dir = self.import_dir_var.get().strip()
        group.export_dir = self.export_dir_var.get().strip()
        group.full_export_dir = self.full_export_dir_var.get().strip()
        group.hotfix_export_dir = self.hotfix_export_dir_var.get().strip()
        self.store.save_group(group)
        self._refresh_safe_setup_status(group)
        template_path = self.store.resolve_safe_template(group)
        self.log(f'已导入当前打包组安全 !Setup.txt 模板：{template_path}')
        self.log(f'安全模板来源仅用于追溯，不参与打包读取：{path}')

    def open_safe_setup_template(self):
        group = self._get_loaded_group_object()
        template_path = self.store.resolve_safe_template(group)
        if not template_path:
            messagebox.showwarning('提示', '当前打包组未导入安全 !Setup.txt 模板')
            return
        if not template_path.exists():
            messagebox.showwarning('提示', '工具内部安全 !Setup.txt 模板不存在，请重新导入')
            self._refresh_safe_setup_status(group)
            return
        self._open_directory(str(template_path.parent))

    def open_other_settings(self):
        AdvancedSettingsDialog(self.root, self)

    def open_help(self):
        HelpDialog(self.root)

    def _open_directory(self, path_str: str):
        if not path_str:
            return
        try:
            path = str(Path(path_str))
            if sys.platform.startswith('win'):
                os.startfile(path)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', path])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            self.log(f'打开导出目录失败：{e}')

    def save_group(self):
        name = self.current_group_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        existing = self._get_loaded_group_object() or PackGroup(name=name)
        group = PackGroup(
            name=name,
            import_dir=self.import_dir_var.get().strip(),
            export_dir=self.export_dir_var.get().strip(),
            export_presets=self._get_group_export_presets(existing),
            full_export_dir=self.full_export_dir_var.get().strip(),
            hotfix_export_dir=self.hotfix_export_dir_var.get().strip(),
            safe_setup_path=getattr(existing, 'safe_setup_path', '') or '',
            safe_setup_template=getattr(existing, 'safe_setup_template', '') or '',
            safe_setup_md5=getattr(existing, 'safe_setup_md5', '') or '',
            safe_setup_import_time=getattr(existing, 'safe_setup_import_time', '') or '',
        )
        self.store.save_group(group)
        self._refresh_export_preset_combo(group, keep_current=group.export_dir)
        self._refresh_safe_setup_status(group)
        self._load_groups()
        self.current_group_name.set(name)
        self._save_ui_config()
        self.log(f'已保存打包组：{name}')

    def new_group(self):
        name = simpledialog.askstring('新建组', '请输入组名：', parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if self.store.load_group(name):
            messagebox.showwarning('提示', '同名打包组已存在')
            return

        self.store.save_group(PackGroup(name=name))

        groups = self.store.list_groups()
        self.group_combo['values'] = groups
        self.current_group_name.set(name)

        # v97: 新建组必须彻底清空当前界面路径，避免沿用上一个打包组的导出目录。
        self.import_dir_var.set('')
        self.export_dir_var.set('')
        self.full_export_dir_var.set('')
        self.hotfix_export_dir_var.set('')
        self._refresh_export_preset_combo(PackGroup(name=name), keep_current='')
        self.current_export_preset_name.set('预设▼')
        self.rule_combo['values'] = []
        self.current_rule_name.set('')
        self._refresh_safe_setup_status(PackGroup(name=name))

        self.loaded_group_name = name
        self.loaded_rule_name = ''
        self._loaded_rule_signature = None
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''

        self._save_ui_config()
        self._clear_file_area()
        self._refresh_rule_dirty_state()
        self.log(f'已新建打包组：{name}')

    def delete_group(self):
        name = self.current_group_name.get().strip()
        if not name:
            messagebox.showwarning('提示', '请先选择一个打包组')
            return
        if not self.store.load_group(name):
            messagebox.showwarning('提示', '当前打包组不存在')
            return
        if not messagebox.askyesno('删除组', f'确定要删除打包组“{name}”吗？\n\n会同时删除该组下的所有规则和对应基线记录。', parent=self.root):
            return
        try:
            shutil.rmtree(self.store.group_root(name), ignore_errors=False)
        except FileNotFoundError:
            pass
        except Exception as exc:
            messagebox.showerror('错误', f'删除打包组失败：{exc}')
            return
        try:
            manifest_path = self.store.manifest_json(name)
            if manifest_path.exists():
                manifest_path.unlink()
        except Exception:
            pass
        browse_positions = self.ui_config.get('browse_positions')
        if isinstance(browse_positions, dict):
            browse_positions.pop(name, None)
        if self.ui_config.get('current_group_name') == name:
            self.ui_config.pop('current_group_name', None)
        remaining = self.store.list_groups()
        self.group_combo['values'] = remaining
        if remaining:
            self.current_group_name.set(remaining[0])
        else:
            self.current_group_name.set('')
            self.import_dir_var.set('')
            self.current_export_preset_name.set('')
            # v83: 导出目录历史使用按钮弹出菜单，不再维护 Combobox values。
            self.export_dir_var.set('')
            self.full_export_dir_var.set('')
            self.hotfix_export_dir_var.set('')
            self.rule_combo['values'] = []
            self.current_rule_name.set('')
            self._refresh_safe_setup_status(None)
            self.loaded_group_name = ''
            self.loaded_rule_name = ''
            self._loaded_rule_signature = None
            self._clear_file_area()
        self._save_ui_config()
        if remaining:
            self.on_group_changed()
        self.log(f'已删除打包组：{name}')

    def new_rule(self):
        group = self.current_group_name.get().strip()
        if not group:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        if not self._has_any_checked():
            messagebox.showwarning('提示', '当前没有勾选任何文件或文件夹，无法新建规则')
            return
        name = simpledialog.askstring('新建规则', '请输入规则名：', parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if self.store.load_rule(group, name):
            messagebox.showwarning('提示', '同名规则已存在')
            return
        rule = self._rule_from_current_ui(name)
        self.store.save_rule(group, rule)
        rules = self.store.list_rules(group)
        self.rule_combo['values'] = rules
        self.current_rule_name.set(name)
        self.loaded_group_name = group
        self.loaded_rule_name = name
        self._loaded_rule_signature = self._rule_signature_from_rule(rule)
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self._refresh_rule_dirty_state()
        self.log(f'已新建规则：{name}')

    def delete_rule(self):
        group = self.current_group_name.get().strip()
        if not group:
            messagebox.showwarning('提示', '请先选择一个打包组')
            return
        rule_name = self.current_rule_name.get().strip()
        if not rule_name:
            messagebox.showwarning('提示', '请先选择一条规则')
            return
        rule_path = self.store.rule_json(group, rule_name)
        if not rule_path.exists():
            messagebox.showwarning('提示', '当前规则不存在')
            return
        if not messagebox.askyesno('删除规则', f'确定要删除规则“{rule_name}”吗？', parent=self.root):
            return
        try:
            rule_path.unlink()
        except Exception as exc:
            messagebox.showerror('错误', f'删除规则失败：{exc}')
            return
        rules = self.store.list_rules(group)
        self.rule_combo['values'] = rules
        if rules:
            self.current_rule_name.set(rules[0])
        else:
            self.current_rule_name.set('')
        self.loaded_group_name = group
        self.loaded_rule_name = self.current_rule_name.get().strip()
        self._loaded_rule_signature = self._rule_signature_from_rule(self.store.load_rule(group, self.loaded_rule_name))
        self._save_ui_config()
        if self.current_rule_name.get().strip():
            self.on_rule_changed()
        self.log(f'已删除规则：{rule_name}')

    def save_rule(self):
        group = self.current_group_name.get().strip()
        if not group:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        rule_name = self.current_rule_name.get().strip()
        if not rule_name:
            messagebox.showwarning('提示', '请先新建或选择一条规则')
            return
        if not self._has_any_checked():
            messagebox.showwarning('提示', '当前没有勾选任何文件或文件夹，无法保存规则')
            return
        rule = self._rule_from_current_ui(rule_name)
        self.store.save_rule(group, rule)
        self.loaded_group_name = group
        self.loaded_rule_name = rule_name
        self._loaded_rule_signature = self._rule_signature_from_rule(rule)
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self._refresh_rule_dirty_state()
        self.log(f'已保存规则：{rule_name}')

    def _rule_from_current_ui(self, name: str) -> PackRule:
        checked_dirs, checked_files, excluded_files = [], [], []
        for meta in self.tree_meta.values():
            if meta.get('filtered'):
                continue
            rel = meta['rel_path']
            if not rel:
                continue
            if meta.get('type') == 'file' and is_admin_list_file(rel):
                continue
            state = meta.get('check_state', 0)
            if meta['type'] == 'dir':
                if state == 1:
                    checked_dirs.append(rel)
            else:
                if state == 1:
                    checked_files.append(rel)
                else:
                    excluded_files.append(rel)
        return PackRule(name=name, checked_dirs=sorted(set(checked_dirs)), checked_files=sorted(set(checked_files)), excluded_files=sorted(set(excluded_files)))

    def load_rule_to_ui(self):
        group = self.current_group_name.get().strip()
        rule_name = self.current_rule_name.get().strip()
        if not group or not rule_name or not self.tree_meta:
            return
        rule = self.store.load_rule(group, rule_name)
        if not rule:
            return
        checked_dirs = {norm_rel(x) for x in rule.checked_dirs}
        checked_files = {norm_rel(x) for x in rule.checked_files}
        excluded_files = {norm_rel(x) for x in rule.excluded_files}
        for meta in self.tree_meta.values():
            if meta.get('filtered'):
                meta['check_state'] = -1
            else:
                meta['check_state'] = 0
        for iid, meta in self.tree_meta.items():
            if meta['type'] == 'dir' and norm_rel(meta['rel_path']) in checked_dirs and not meta.get('filtered'):
                self._set_subtree_state(iid, True, skip_filtered=True)
        for iid, meta in self.tree_meta.items():
            rel = norm_rel(meta['rel_path'])
            if meta['type'] == 'file' and not meta.get('filtered'):
                if rel in checked_files:
                    meta['check_state'] = 1
                elif rel in excluded_files:
                    meta['check_state'] = 0
        self._recalc_all_parent_states()
        self.loaded_group_name = group
        self.loaded_rule_name = rule_name
        self._loaded_rule_signature = self._rule_signature_from_rule(rule)
        self.rule_dirty_by_user = False
        self.rule_dirty_reason = ''
        self._refresh_all_texts()
        self._refresh_rule_dirty_state()
        self.log(f'已载入规则：{rule_name}')

    def edit_filters(self):
        FilterEditor(self.root, self.store, self._on_filters_saved)

    def _on_filters_saved(self):
        self.log('已保存过滤配置')
        self.refresh_files()


    def _set_busy(self, busy: bool):
        self.scanning = busy
        state = 'disabled' if busy else 'normal'
        for btn_name in ('btn_refresh', 'btn_pack_rule', 'btn_pack_checked', 'btn_set_baseline', 'btn_pack_full'):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def _build_shallow_entries(self, import_dir: str, filter_cfg: Dict, include_hidden: bool = False) -> Dict[str, Dict]:
        root_path = Path(import_dir)
        entries: Dict[str, Dict] = {}
        root_label = root_path.name
        entries[''] = {
            'rel_path': '', 'name': root_label, 'type': 'dir', 'filtered': False,
            'status': 'normal', 'abs_path': str(root_path), 'size': 0, 'mtime': 0, 'md5': ''
        }
        try:
            for child in root_path.iterdir():
                rel = norm_rel(str(child.relative_to(root_path)))
                is_dir = child.is_dir()
                hidden = ui_hidden_excluded(rel, is_dir, filter_cfg)
                if not is_dir and (is_force_hidden_path(rel) or is_admin_list_file(rel)):
                    continue
                if hidden and not include_hidden:
                    continue
                allowed = path_allowed(rel, is_dir, filter_cfg)
                try:
                    st = child.stat() if not is_dir else None
                except Exception:
                    st = None
                entries[rel] = {
                    'rel_path': rel,
                    'name': child.name,
                    'type': 'dir' if is_dir else 'file',
                    'filtered': not allowed,
                    'hidden': hidden,
                    'status': 'normal',
                    'abs_path': str(child),
                    'size': 0 if is_dir or st is None else st.st_size,
                    'mtime': 0 if is_dir or st is None else int(st.st_mtime),
                    'md5': '',
                }
        except Exception:
            pass
        return entries

    def _quick_init_baseline_worker(self, generation: int, group_name: str, import_dir: str, filter_cfg: Dict, include_hidden: bool = False):
        try:
            root_path = Path(import_dir)
            all_entries: Dict[str, Dict] = {}
            root_label = root_path.name
            all_entries[''] = {
                'rel_path': '', 'name': root_label, 'type': 'dir', 'filtered': False,
                'status': 'normal', 'abs_path': str(root_path), 'size': 0, 'mtime': 0, 'md5': ''
            }
            manifest = {
                'group_name': group_name,
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'files': {}
            }

            for dirpath, dirnames, filenames in os.walk(root_path):
                current = Path(dirpath)
                rel_dir = '' if current == root_path else norm_rel(str(current.relative_to(root_path)))

                kept_dirs = []
                for d in dirnames:
                    child_rel = norm_rel(str((current / d).relative_to(root_path)))
                    child_hidden = ui_hidden_excluded(child_rel, True, filter_cfg)
                    if is_force_hidden_path(child_rel) or is_admin_list_file(child_rel):
                        continue
                    if child_hidden and not include_hidden:
                        continue
                    if not path_allowed(child_rel, True, filter_cfg):
                        continue
                    kept_dirs.append(d)
                dirnames[:] = kept_dirs

                if rel_dir:
                    hidden = ui_hidden_excluded(rel_dir, True, filter_cfg)
                    allowed = path_allowed(rel_dir, True, filter_cfg)
                    all_entries.setdefault(rel_dir, {
                        'rel_path': rel_dir,
                        'name': current.name,
                        'type': 'dir',
                        'filtered': not allowed,
                        'hidden': hidden,
                        'status': 'normal',
                        'abs_path': str(current),
                        'size': 0,
                        'mtime': 0,
                        'md5': ''
                    })
                for fn in filenames:
                    abs_file = current / fn
                    rel_file = norm_rel(str(abs_file.relative_to(root_path)))
                    hidden = ui_hidden_excluded(rel_file, False, filter_cfg)
                    if is_force_hidden_path(rel_file) or is_admin_list_file(rel_file):
                        continue
                    if hidden and not include_hidden:
                        continue
                    allowed = path_allowed(rel_file, False, filter_cfg)
                    stat = abs_file.stat()
                    size = stat.st_size
                    mtime = int(stat.st_mtime)

                    all_entries[rel_file] = {
                        'rel_path': rel_file,
                        'name': fn,
                        'type': 'file',
                        'filtered': not allowed,
                        'hidden': hidden,
                        'status': 'normal',
                        'abs_path': str(abs_file),
                        'size': size,
                        'mtime': mtime,
                        'md5': '',
                    }
                    if not hidden:
                        manifest['files'][rel_file] = {
                            'size': size,
                            'mtime': mtime,
                            'md5': '',
                        }

            for rel_path, meta in list(all_entries.items()):
                if rel_path and not meta['filtered']:
                    p = Path(rel_path)
                    parents = list(p.parents)[:-1]
                    for parent in parents:
                        pr = norm_rel(str(parent))
                        if pr and pr in all_entries:
                            all_entries[pr]['filtered'] = False

            self.scan_queue.put(('init_done', generation, all_entries, manifest))
        except Exception as e:
            self.scan_queue.put(('error', generation, str(e)))

    def _scan_worker(self, generation: int, group_name: str, import_dir: str, filter_cfg: Dict, old_files: Dict, include_hidden: bool = False):
        try:
            root_path = Path(import_dir)
            all_entries: Dict[str, Dict] = {}
            root_label = root_path.name
            all_entries[''] = {
                'rel_path': '', 'name': root_label, 'type': 'dir', 'filtered': False,
                'status': 'normal', 'abs_path': str(root_path), 'size': 0, 'mtime': 0, 'md5': ''
            }

            for dirpath, dirnames, filenames in os.walk(root_path):
                current = Path(dirpath)
                rel_dir = '' if current == root_path else norm_rel(str(current.relative_to(root_path)))

                kept_dirs = []
                for d in dirnames:
                    child_rel = norm_rel(str((current / d).relative_to(root_path)))
                    child_hidden = ui_hidden_excluded(child_rel, True, filter_cfg)
                    if is_force_hidden_path(child_rel) or is_admin_list_file(child_rel):
                        continue
                    if child_hidden and not include_hidden:
                        continue
                    if not path_allowed(child_rel, True, filter_cfg):
                        continue
                    kept_dirs.append(d)
                dirnames[:] = kept_dirs

                if rel_dir:
                    hidden = ui_hidden_excluded(rel_dir, True, filter_cfg)
                    allowed = path_allowed(rel_dir, True, filter_cfg)
                    all_entries.setdefault(rel_dir, {
                        'rel_path': rel_dir,
                        'name': current.name,
                        'type': 'dir',
                        'filtered': not allowed,
                        'hidden': hidden,
                        'status': 'normal',
                        'abs_path': str(current),
                        'size': 0,
                        'mtime': 0,
                        'md5': ''
                    })
                for fn in filenames:
                    abs_file = current / fn
                    rel_file = norm_rel(str(abs_file.relative_to(root_path)))
                    hidden = ui_hidden_excluded(rel_file, False, filter_cfg)
                    if is_force_hidden_path(rel_file) or is_admin_list_file(rel_file):
                        continue
                    if hidden and not include_hidden:
                        continue
                    allowed = path_allowed(rel_file, False, filter_cfg)
                    stat = abs_file.stat()
                    size = stat.st_size
                    mtime = int(stat.st_mtime)

                    old = old_files.get(rel_file)
                    status = 'normal'
                    if not old:
                        status = 'new'
                        md5v = md5_of_file(abs_file)
                    else:
                        old_size = int(old.get('size', -1))
                        old_mtime = int(old.get('mtime', -1))
                        old_md5 = old.get('md5', '') or ''
                        if old_size == size and old_mtime == mtime:
                            md5v = old_md5
                        else:
                            md5v = md5_of_file(abs_file)
                            if old_md5 == '':
                                status = 'modified'
                            elif old_md5 != md5v:
                                status = 'modified'

                    all_entries[rel_file] = {
                        'rel_path': rel_file,
                        'name': fn,
                        'type': 'file',
                        'filtered': not allowed,
                        'hidden': hidden,
                        'status': status,
                        'abs_path': str(abs_file),
                        'size': size,
                        'mtime': mtime,
                        'md5': md5v,
                    }

            for rel_path, meta in list(all_entries.items()):
                if rel_path and not meta['filtered']:
                    p = Path(rel_path)
                    parents = list(p.parents)[:-1]
                    for parent in parents:
                        pr = norm_rel(str(parent))
                        if pr and pr in all_entries:
                            all_entries[pr]['filtered'] = False

            new_count = sum(1 for x in all_entries.values() if x['type'] == 'file' and x['status'] == 'new' and not x['filtered'] and not x.get('hidden'))
            mod_count = sum(1 for x in all_entries.values() if x['type'] == 'file' and x['status'] == 'modified' and not x['filtered'] and not x.get('hidden'))
            self.scan_queue.put(('done', generation, all_entries, new_count, mod_count))
        except Exception as e:
            self.scan_queue.put(('error', generation, str(e)))

    def _process_scan_queue(self):
        try:
            while True:
                item = self.scan_queue.get_nowait()
                kind = item[0]
                generation = item[1]
                if generation != self.scan_generation:
                    continue
                self._set_busy(False)
                if kind == 'done':
                    _, _, all_entries, new_count, mod_count = item
                    self.scan_entries = all_entries
                    self._build_tree_from_entries()
                    self.expand_to_level('root', 1)
                    self.root.after_idle(self._restore_browse_position_after_refresh)
                    self.log('后台扫描完成，已全局渲染完整目录')
                    self.log(f'新增 {new_count} 个，修改 {mod_count} 个')
                elif kind == 'init_done':
                    _, _, all_entries, manifest = item
                    self.store.save_manifest(manifest['group_name'], manifest)
                    self.scan_entries = all_entries
                    self._build_tree_from_entries()
                    self.expand_to_level('root', 1)
                    self.root.after_idle(self._restore_browse_position_after_refresh)
                    self.log('当前组无基线，已快速建立初始基线')
                    self.log('后续刷新将进入增量模式')
                elif kind == 'error':
                    _, _, err = item
                    self.log(f'刷新文件失败：{err}')
                    messagebox.showerror('错误', f'刷新文件失败：{err}')
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._process_scan_queue)

    def refresh_files(self):
        group_name = self.current_group_name.get().strip()
        if not group_name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        import_dir = self.import_dir_var.get().strip()
        if not import_dir or not Path(import_dir).exists():
            messagebox.showwarning('提示', '导入目录不存在，请检查')
            return
        if self.scanning:
            return

        filter_cfg = self.store.load_filter_config()
        include_hidden = bool(self.show_hidden_items.get())
        manifest = self.store.load_manifest(group_name)
        old_files = manifest.get('files', {})

        # 开屏只渲染 1 级
        self.scan_entries = self._build_shallow_entries(import_dir, filter_cfg, include_hidden)
        self._build_tree_from_entries()
        self.expand_to_level('root', 0)
        # self.log('已渲染首屏 1 级内容')

        # 当前组无基线时，允许快速初始化
        if not old_files:
            choice = messagebox.askyesnocancel(
                '当前组无基线',
                '当前打包组没有基线数据。\n\n'
                '是：快速初始化基线（推荐，速度快）\n'
                '否：完整扫描并计算 md5（较慢）\n'
                '取消：终止本次刷新'
            )
            if choice is None:
                self.log('已取消刷新')
                return

            self.scan_generation += 1
            generation = self.scan_generation
            self._set_busy(True)

            if choice:
                self.log('开始快速初始化基线...')
                t = threading.Thread(
                    target=self._quick_init_baseline_worker,
                    args=(generation, group_name, import_dir, filter_cfg, include_hidden),
                    daemon=True
                )
            else:
                self.log('开始完整扫描并计算 md5...')
                t = threading.Thread(
                    target=self._scan_worker,
                    args=(generation, group_name, import_dir, filter_cfg, old_files, include_hidden),
                    daemon=True
                )
            t.start()
            return

        # 有基线时走后台增量扫描
        self.scan_generation += 1
        generation = self.scan_generation
        self._set_busy(True)
        self.log('开始后台扫描完整目录...')
        t = threading.Thread(
            target=self._scan_worker,
            args=(generation, group_name, import_dir, filter_cfg, old_files, include_hidden),
            daemon=True
        )
        t.start()

    def refresh_tree_only(self):
        if self.scan_entries:
            self._build_tree_from_entries()
        else:
            self._hide_frozen_anchor()

    def on_show_hidden_items_toggle(self):
        # 开启“显示隐藏项”时需要重新扫描一次，才能把原本不进入文件树的隐藏项临时带入显示。
        # 关闭时只重建当前树即可，隐藏项会重新从界面消失。
        if self.show_hidden_items.get():
            self.refresh_files()
        else:
            self.refresh_tree_only()

    def _build_tree_from_entries(self):
        self.tree.delete(*self.tree.get_children())
        self.tree_meta = {}
        self.current_selected_iid = None
        self.hover_iid = None
        self.context_iid = None
        show_filtered_only = self.show_filtered_only.get()
        show_hidden_items = self.show_hidden_items.get()

        root_meta = self.scan_entries[''].copy()
        root_meta.update({'iid': 'root', 'parent_iid': '', 'check_state': 0, 'display_disabled': False, 'base_tags': []})
        self.tree_meta['root'] = root_meta
        self._tree_insert_with_icon('', 'root', self._make_display_text(root_meta), True)

        visible_entries = {}
        for rel, meta in self.scan_entries.items():
            if rel == '':
                continue
            if meta.get('hidden') and not show_hidden_items:
                continue
            if show_filtered_only and meta['filtered']:
                continue
            visible_entries[rel] = meta

        children_map = {'': []}
        for rel, meta in visible_entries.items():
            parent_rel = norm_rel(str(Path(rel).parent)) if '/' in rel else ''
            children_map.setdefault(parent_rel, []).append((rel, meta))
            children_map.setdefault(rel, [])

        def sort_key(item):
            rel, meta = item
            type_order = 0 if meta['type'] == 'dir' else 1
            return (type_order, meta['name'].lower(), rel.lower())

        iid_map = {'': 'root'}

        def insert_children(parent_rel: str):
            parent_iid = iid_map.get(parent_rel, 'root')
            for rel, meta in sorted(children_map.get(parent_rel, []), key=sort_key):
                iid = f'n::{rel}'
                iid_map[rel] = iid
                node_meta = meta.copy()
                node_meta.update({
                    'iid': iid,
                    'parent_iid': parent_iid,
                    'check_state': -1 if (meta['filtered'] or meta.get('hidden')) else 0,
                    'display_disabled': bool(meta['filtered'] or meta.get('hidden')),
                })
                tags = []
                if meta.get('hidden'):
                    tags.append('hidden')
                elif meta['filtered'] and not show_filtered_only:
                    tags.append('filtered')
                elif meta['status'] == 'new':
                    tags.append('new')
                elif meta['status'] == 'modified':
                    tags.append('modified')
                node_meta['base_tags'] = tags[:]
                self.tree_meta[iid] = node_meta
                self._tree_insert_with_icon(parent_iid, iid, self._make_display_text(node_meta), False, tags=tuple(tags))
                if meta['type'] == 'dir':
                    insert_children(rel)

        insert_children('')

        if self.current_rule_name.get().strip() and self.rule_mapping_enabled.get():
            self.load_rule_to_ui()
        else:
            self._refresh_all_texts()
            self._mark_current_rule_clean()
        self.root.after_idle(self._restore_browse_position_after_refresh)
        self.root.after_idle(self.update_frozen_anchor)

    def _make_display_text(self, meta: Dict) -> str:
        if meta['iid'] == 'root':
            return meta['name']
        if meta.get('hidden'):
            return f'[隐藏] {meta["name"]}'
        if meta.get('display_disabled'):
            return f'[已过滤] {meta["name"]}'
        status = ''
        if meta['status'] == 'new':
            status = '[新增] '
        elif meta['status'] == 'modified':
            status = '[修改] '
        return f'{status}{meta["name"]}'

    def _update_item_tags(self, iid: str):
        if iid not in self.tree_meta:
            return
        tags = list(self.tree_meta[iid].get('base_tags', []))
        if iid == self.hover_iid:
            tags.append('hover')
        if iid == self.context_iid:
            tags.append('context')
        try:
            self.tree.item(iid, tags=tuple(tags))
        except tk.TclError:
            pass

    def _refresh_all_texts(self):
        for iid, meta in self.tree_meta.items():
            try:
                kwargs = {'text': self._make_display_text(meta)}
                icon = self._get_icon_for_meta(meta)
                if icon is not None:
                    kwargs['image'] = icon
                self.tree.item(iid, **kwargs)
                self._update_item_tags(iid)
            except tk.TclError:
                pass
        self._refresh_rule_dirty_state()
        self.root.after_idle(self.update_frozen_anchor)

    def _event_has_shift(self, event) -> bool:
        return bool(event is not None and (getattr(event, 'state', 0) & 0x0001))

    def _apply_check_range_by_visible(self, from_iid: str, to_iid: str, target_state: bool):
        if not from_iid or not to_iid:
            return False
        visible = self._get_visible_iids_in_order()
        if not visible:
            return False
        try:
            a = visible.index(from_iid)
            b = visible.index(to_iid)
        except ValueError:
            return False
        if a > b:
            a, b = b, a
        changed = False
        for iid in visible[a:b + 1]:
            meta = self.tree_meta.get(iid)
            if not meta or meta.get('display_disabled'):
                continue
            self._set_subtree_state(iid, target_state, skip_filtered=True)
            self._update_ancestors(iid)
            changed = True
        if changed:
            self._refresh_all_texts()
            self._mark_current_rule_dirty('已修改勾选')
        return changed

    def on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            self.current_selected_iid = None
            self.current_node_label.configure(text='当前节点: (无)')
            for node_iid in self.tree_meta.keys():
                self._update_item_tags(node_iid)
            return
        iid = sel[0]
        self.current_selected_iid = iid
        meta = self.tree_meta.get(iid)
        if meta:
            label = meta['rel_path'] if meta['rel_path'] else meta['name']
            self.current_node_label.configure(text=f'当前节点: {label}')
        for node_iid in self.tree_meta.keys():
            self._update_item_tags(node_iid)
        self._schedule_save_browse_position()

    def on_tree_motion(self, event=None):
        iid = self.tree.identify_row(event.y)
        if iid == self.hover_iid:
            return
        old = self.hover_iid
        self.hover_iid = iid if iid in self.tree_meta else None
        if old:
            self._update_item_tags(old)
        if self.hover_iid:
            self._update_item_tags(self.hover_iid)

    def on_tree_leave(self, event=None):
        if self.hover_iid:
            old = self.hover_iid
            self.hover_iid = None
            self._update_item_tags(old)

    def _get_dir_hot_right(self, iid: str) -> int:
        depth = 0
        parent_iid = iid
        while True:
            parent_iid = self.tree.parent(parent_iid)
            if not parent_iid:
                break
            depth += 1
        return 6 + depth * 20 + 30

    def _clear_tree_selection_visual(self):
        try:
            self.tree.selection_remove(self.tree.selection())
            self.tree.focus('')
        except tk.TclError:
            pass
        self.current_selected_iid = None
        self.shift_anchor_iid = None
        self.current_node_label.configure(text='当前节点: (无)')
        for node_iid in self.tree_meta.keys():
            self._update_item_tags(node_iid)

    def _set_current_node_from_iid(self, iid: Optional[str]):
        self.current_selected_iid = iid if iid in self.tree_meta else None
        if not self.current_selected_iid:
            self.current_node_label.configure(text='当前节点: (无)')
            return
        meta = self.tree_meta.get(self.current_selected_iid)
        if not meta:
            self.current_node_label.configure(text='当前节点: (无)')
            return
        label = meta['rel_path'] if meta['rel_path'] else meta['name']
        self.current_node_label.configure(text=f'当前节点: {label}')
        self._schedule_save_browse_position()

    def _refresh_drag_visible_cache(self):
        self.drag_visible_iids = self._get_visible_iids_in_order()
        self.drag_visible_index = {iid: idx for idx, iid in enumerate(self.drag_visible_iids)}

    def _flush_drag_refresh(self):
        if self.drag_refresh_pending:
            self.drag_refresh_pending = False
            self._refresh_all_texts()

    def _begin_drag_check(self, iid: str, target_state: bool):
        self.drag_check_active = True
        self.drag_target_state = target_state
        self.drag_visited_iids = set()
        self.drag_last_iid = iid
        self.drag_refresh_pending = False
        self._refresh_drag_visible_cache()
        self._apply_drag_check_to_iid(iid, refresh=False)
        self._flush_drag_refresh()

    def _apply_drag_check_to_iid(self, iid: str, refresh: bool = False):
        if not self.drag_check_active or not iid or iid in self.drag_visited_iids:
            return False
        meta = self.tree_meta.get(iid)
        if not meta or meta.get('display_disabled'):
            return False
        self.drag_visited_iids.add(iid)
        self._set_subtree_state(iid, self.drag_target_state, skip_filtered=True)
        self._update_ancestors(iid)
        self.drag_refresh_pending = True
        if refresh:
            self._flush_drag_refresh()
        return True

    def _get_visible_iids_in_order(self):
        result = []

        def walk(parent=''):
            try:
                children = self.tree.get_children(parent)
            except tk.TclError:
                return
            for child in children:
                if child in self.tree_meta:
                    result.append(child)
                try:
                    opened = bool(self.tree.item(child, 'open'))
                except tk.TclError:
                    opened = False
                if opened:
                    walk(child)

        walk('')
        return result

    def _apply_drag_check_range(self, from_iid: str, to_iid: str):
        if not self.drag_check_active or not from_iid or not to_iid:
            return
        if from_iid == to_iid:
            self._apply_drag_check_to_iid(to_iid, refresh=False)
            self._flush_drag_refresh()
            return

        visible_index = self.drag_visible_index
        if not visible_index:
            self._refresh_drag_visible_cache()
            visible_index = self.drag_visible_index
        visible = self.drag_visible_iids
        if not visible:
            self._apply_drag_check_to_iid(to_iid, refresh=False)
            self._flush_drag_refresh()
            return

        a = visible_index.get(from_iid)
        b = visible_index.get(to_iid)
        if a is None or b is None:
            self._refresh_drag_visible_cache()
            visible_index = self.drag_visible_index
            visible = self.drag_visible_iids
            a = visible_index.get(from_iid)
            b = visible_index.get(to_iid)
        if a is None or b is None:
            self._apply_drag_check_to_iid(to_iid, refresh=False)
            self._flush_drag_refresh()
            return

        if a > b:
            a, b = b, a
        for iid in visible[a:b + 1]:
            self._apply_drag_check_to_iid(iid, refresh=False)
        self._flush_drag_refresh()

    def _set_context_highlight(self, iid: str):
        old = self.context_iid
        self.context_iid = iid if iid in self.tree_meta else None
        if old and old != self.context_iid:
            self._update_item_tags(old)
        if self.context_iid:
            self._update_item_tags(self.context_iid)

    def _clear_context_highlight(self):
        old = self.context_iid
        self.context_iid = None
        if old:
            self._update_item_tags(old)

    def on_tree_right_click(self, event=None):
        if event is None or not hasattr(self, 'tree'):
            return 'break'

        # 如果上一个右键菜单还挂着，先主动销毁；避免连续右键不同节点时旧菜单/旧轮询覆盖新高亮。
        old_menu = getattr(self, 'context_menu', None)
        if old_menu is not None:
            try:
                old_menu.unpost()
            except Exception:
                pass
            try:
                old_menu.destroy()
            except Exception:
                pass
            self.context_menu = None

        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.tree_meta or iid == 'root':
            self._clear_context_highlight()
            return 'break'
        meta = self.tree_meta.get(iid)
        if not meta:
            self._clear_context_highlight()
            return 'break'
        rel = norm_rel(meta.get('rel_path', '') or '')
        if not rel:
            self._clear_context_highlight()
            return 'break'
        is_dir = meta.get('type') == 'dir'

        # 右键菜单打开期间临时高亮当前节点；不改变正式选择，避免影响左键勾选和范围选择。
        self._set_context_highlight(iid)
        self.context_menu_generation += 1
        menu_generation = self.context_menu_generation

        menu = tk.Menu(self.root, tearoff=0)
        self.context_menu = menu
        menu.add_command(label='复制相对路径', command=lambda r=rel: self.copy_relative_path(r))
        menu.add_command(label='打开所在目录', command=lambda m=meta: self.open_node_directory(m))
        menu.add_separator()
        if meta.get('hidden'):
            menu.add_command(label='移出不显示列表', command=lambda r=rel, d=is_dir: self.remove_node_from_filter_list(r, d, 'hidden_dirs', 'hidden_files', '不显示列表', refresh=True))
        else:
            menu.add_command(label='加入不显示列表', command=lambda r=rel, d=is_dir: self.add_node_to_filter_list(r, d, 'hidden_dirs', 'hidden_files', '不显示列表', refresh=True))
        menu.add_command(label='加入黑名单', command=lambda r=rel, d=is_dir: self.add_node_to_filter_list(r, d, 'blacklist_dirs', 'blacklist_files', '黑名单', refresh=True))
        menu.add_separator()
        menu.add_command(label='加入母包排除', command=lambda r=rel, d=is_dir: self.add_node_to_filter_list(r, d, 'full_package_exclude_dirs', 'full_package_exclude_files', '母包排除', refresh=False))
        menu.add_command(label='加入规则包排除', command=lambda r=rel, d=is_dir: self.add_node_to_filter_list(r, d, 'rule_package_exclude_dirs', 'rule_package_exclude_files', '规则包排除', refresh=False))

        def clear_when_closed(_event=None, gen=menu_generation):
            if gen != self.context_menu_generation:
                return
            self.context_menu = None
            self.root.after_idle(self._clear_context_highlight)

        def poll_menu_closed(gen=menu_generation):
            # 某些 Windows/Tk 版本点击空白处关闭菜单时，不会稳定触发 Unmap/FocusOut。
            # 轮询菜单映射状态作为兜底，确保右键临时高亮一定被清理。
            if gen != self.context_menu_generation:
                return
            try:
                exists = bool(menu.winfo_exists())
                mapped = bool(menu.winfo_ismapped()) if exists else False
            except Exception:
                exists = False
                mapped = False
            if not exists or not mapped:
                self.context_menu = None
                self._clear_context_highlight()
                return
            self.root.after(80, poll_menu_closed)

        menu.bind('<Unmap>', clear_when_closed, add='+')
        menu.bind('<FocusOut>', clear_when_closed, add='+')
        try:
            menu.tk_popup(event.x_root, event.y_root)
            self.root.after(80, poll_menu_closed)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return 'break'

    def copy_relative_path(self, rel_path: str):
        rel_path = norm_rel(rel_path).replace('/', '\\')
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(rel_path)
            self.log(f'已复制相对路径：{rel_path}')
        except Exception as exc:
            self.log(f'复制相对路径失败：{exc}')

    def open_node_directory(self, meta: Dict):
        abs_path = str(meta.get('abs_path', '') or '')
        if not abs_path:
            return
        p = Path(abs_path)
        target = p if meta.get('type') == 'dir' else p.parent
        self._open_directory(str(target))

    def add_node_to_filter_list(self, rel_path: str, is_dir: bool, dir_key: str, file_key: str, label: str, refresh: bool = False):
        rel_path = norm_rel(rel_path)
        if not rel_path:
            return
        cfg = self.store.load_filter_config()
        key = dir_key if is_dir else file_key
        value = rel_path
        changed = append_unique_rule(cfg, key, value)
        if not changed:
            self.log(f'{label}中已存在：{display_rule_path(value, is_dir=is_dir)}')
            return
        self.store.save_filter_config(cfg)
        self.log(f'已加入{label}：{display_rule_path(value, is_dir=is_dir)}')
        if refresh:
            self.refresh_files()

    def remove_node_from_filter_list(self, rel_path: str, is_dir: bool, dir_key: str, file_key: str, label: str, refresh: bool = False):
        rel_path = norm_rel(rel_path)
        if not rel_path:
            return
        cfg = self.store.load_filter_config()
        removed = None
        removed_is_dir = False

        # 文件规则优先精确删除；如果当前节点是因为父目录规则被隐藏，则删除最具体的父目录规则。
        if not is_dir:
            files = cfg.get(file_key, [])
            for i, item in enumerate(list(files)):
                if norm_rel(item).lower() == rel_path.lower():
                    removed = norm_rel(item)
                    removed_is_dir = False
                    del files[i]
                    break

        if removed is None:
            dirs = cfg.get(dir_key, [])
            matches = []
            for i, item in enumerate(list(dirs)):
                rule = norm_rel(item)
                if is_prefix_path(rule, rel_path):
                    matches.append((len(rule), i, rule))
            if matches:
                _, idx, rule = sorted(matches, reverse=True)[0]
                removed = rule
                removed_is_dir = True
                del dirs[idx]

        if removed is None and is_dir:
            files = cfg.get(file_key, [])
            for i, item in enumerate(list(files)):
                if norm_rel(item).lower() == rel_path.lower():
                    removed = norm_rel(item)
                    removed_is_dir = False
                    del files[i]
                    break

        if removed is None:
            self.log(f'{label}中未找到可移出的规则：{display_rule_path(rel_path, is_dir=is_dir)}')
            return

        self.store.save_filter_config(cfg)
        self.log(f'已移出{label}：{display_rule_path(removed, is_dir=removed_is_dir)}')
        if refresh:
            self.refresh_files()

    def on_tree_single_click(self, event=None):
        if event is None:
            return None

        self.drag_check_active = False
        self.drag_visited_iids = set()
        self.drag_last_iid = None
        self.drag_visible_iids = []
        self.drag_visible_index = {}
        self.drag_refresh_pending = False

        element = self.tree.identify_element(event.x, event.y)
        iid = self.tree.identify_row(event.y)

        if element == 'Treeitem.indicator':
            return None

        if not iid or iid not in self.tree_meta:
            self._clear_tree_selection_visual()
            return 'break'

        meta = self.tree_meta[iid]

        if meta.get('type') == 'dir':
            hot_right = self._get_dir_hot_right(iid)
            if event.x <= hot_right:
                try:
                    opened = bool(self.tree.item(iid, 'open'))
                    self.tree.item(iid, open=not opened)
                    self.tree.focus(iid)
                except tk.TclError:
                    return 'break'
                self.current_selected_iid = iid
                self.shift_anchor_iid = iid
                self._set_current_node_from_iid(iid)
                try:
                    self.tree.selection_remove(self.tree.selection())
                except tk.TclError:
                    pass
                for node_iid in self.tree_meta.keys():
                    self._update_item_tags(node_iid)
                self.root.after_idle(self.update_frozen_anchor)
                return 'break'

        try:
            self.tree.focus(iid)
        except tk.TclError:
            return 'break'
        previous_anchor = self.shift_anchor_iid if self.shift_anchor_iid in self.tree_meta else self.current_selected_iid
        self.current_selected_iid = iid
        self._set_current_node_from_iid(iid)
        try:
            self.tree.selection_remove(self.tree.selection())
        except tk.TclError:
            pass
        for node_iid in self.tree_meta.keys():
            self._update_item_tags(node_iid)

        if meta.get('display_disabled'):
            self.shift_anchor_iid = iid
            return 'break'

        current = meta.get('check_state', 0)
        new_state = current != 1

        if self._event_has_shift(event) and previous_anchor and previous_anchor in self.tree_meta and previous_anchor != iid:
            if self._apply_check_range_by_visible(previous_anchor, iid, new_state):
                self.shift_anchor_iid = iid
                self._mark_current_rule_dirty('已修改勾选')
                self.root.after_idle(self.update_frozen_anchor)
                return 'break'

        self.shift_anchor_iid = iid
        self._begin_drag_check(iid, new_state)
        self._mark_current_rule_dirty('已修改勾选')
        return 'break'

    def on_tree_drag_motion(self, event=None):
        if event is None or not self.drag_check_active:
            return None

        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.tree_meta:
            return 'break'

        self._set_current_node_from_iid(iid)
        prev_iid = self.drag_last_iid or iid
        self._apply_drag_check_range(prev_iid, iid)
        self.drag_last_iid = iid
        return 'break'

    def on_tree_drag_release(self, event=None):
        self._flush_drag_refresh()
        self.root.after_idle(self.update_frozen_anchor)
        self.drag_check_active = False
        self.drag_visited_iids = set()
        self.drag_last_iid = None
        self.drag_visible_iids = []
        self.drag_visible_index = {}
        self.drag_refresh_pending = False
        return None

    def on_tree_toggle_selected(self, event=None):
        iid = self.current_selected_iid
        if not iid or iid not in self.tree_meta:
            return 'break'
        meta = self.tree_meta[iid]
        self.shift_anchor_iid = iid
        if meta.get('display_disabled'):
            return 'break'
        current = meta.get('check_state', 0)
        new_state = current != 1
        self._set_subtree_state(iid, new_state, skip_filtered=True)
        self._update_ancestors(iid)
        self._refresh_all_texts()
        self._mark_current_rule_dirty('已修改勾选')
        return 'break'

    def _set_subtree_state(self, iid: str, checked: bool, skip_filtered: bool = True):
        if iid not in self.tree_meta:
            return
        meta = self.tree_meta[iid]
        if skip_filtered and meta.get('display_disabled'):
            return
        meta['check_state'] = 1 if checked else 0
        for child in self.tree.get_children(iid):
            self._set_subtree_state(child, checked, skip_filtered=skip_filtered)

    def _update_ancestors(self, iid: str):
        parent = self.tree_meta.get(iid, {}).get('parent_iid')
        while parent and parent in self.tree_meta:
            children = [self.tree_meta[c] for c in self.tree.get_children(parent) if c in self.tree_meta and not self.tree_meta[c].get('display_disabled')]
            if not children:
                self.tree_meta[parent]['check_state'] = 0
            else:
                states = {c['check_state'] for c in children}
                if states == {1}:
                    self.tree_meta[parent]['check_state'] = 1
                elif states == {0}:
                    self.tree_meta[parent]['check_state'] = 0
                else:
                    self.tree_meta[parent]['check_state'] = 2
            parent = self.tree_meta[parent].get('parent_iid')

    def _recalc_all_parent_states(self):
        nodes = sorted(self.tree_meta.values(), key=lambda x: x['rel_path'].count('/'), reverse=True)
        for meta in nodes:
            iid = meta['iid']
            if iid == 'root':
                continue
            self._update_ancestors(iid)

    def set_current_node_check(self, checked: bool):
        iid = self.current_selected_iid
        if not iid or iid not in self.tree_meta:
            return
        self._set_subtree_state(iid, checked, skip_filtered=True)
        self._update_ancestors(iid)
        self._refresh_all_texts()

    def _has_any_checked(self) -> bool:
        for meta in self.tree_meta.values():
            if meta.get('display_disabled'):
                continue
            if meta.get('check_state') == 1 and meta.get('iid') != 'root':
                return True
        return False

    def check_all_changed(self):
        for iid, meta in self.tree_meta.items():
            if meta.get('display_disabled'):
                continue
            if meta['type'] == 'file' and meta['status'] in ('new', 'modified'):
                meta['check_state'] = 1
        self._recalc_all_parent_states()
        self._refresh_all_texts()
        self._mark_current_rule_dirty('已修改勾选')
        self.log('已勾选所有变化文件')

    def clear_all_checks(self):
        for iid, meta in self.tree_meta.items():
            if meta.get('display_disabled'):
                continue
            if iid != 'root':
                meta['check_state'] = 0
        self._recalc_all_parent_states()
        self._refresh_all_texts()
        self._mark_current_rule_dirty('已修改勾选')
        self.log('已清空勾选')

    def expand_all(self):
        for iid in self.tree.get_children(''):
            self._set_open_recursive(iid, True)
        self.root.after_idle(self.update_frozen_anchor)

    def collapse_all(self):
        for iid in self.tree.get_children(''):
            self._set_open_recursive(iid, False)
        self.tree.item('root', open=True)
        self.root.after_idle(self.update_frozen_anchor)

    def _set_open_recursive(self, iid: str, opened: bool):
        try:
            self.tree.item(iid, open=opened)
        except tk.TclError:
            return
        for child in self.tree.get_children(iid):
            self._set_open_recursive(child, opened)

    def expand_to_level(self, iid: str, level: int):
        if level < 0:
            return
        self.tree.item(iid, open=True)
        if level == 0:
            return
        for child in self.tree.get_children(iid):
            self.expand_to_level(child, level - 1)

    def _get_safe_setup_path_for_pack(self) -> Optional[Path]:
        group = self._get_loaded_group_object()
        template_path = self.store.resolve_safe_template(group)
        if template_path is None:
            messagebox.showwarning('提示', '本次打包覆盖到 !Setup.txt，请先导入当前打包组的安全 !Setup.txt 模板')
            self._refresh_safe_setup_status(group)
            return None
        if not template_path.is_file():
            messagebox.showwarning('提示', f'工具内部安全 !Setup.txt 模板不存在，请重新导入：\n{template_path}')
            self._refresh_safe_setup_status(group)
            return None
        if template_path.name.lower() != SAFE_SETUP_FILENAME.lower():
            messagebox.showwarning('提示', '内部安全模板文件名异常，必须是 !Setup.txt，请重新导入')
            return None
        return template_path

    def _build_safe_setup_meta(self, rel_path: str = 'Mir200/!Setup.txt') -> Optional[Dict]:
        rel_path = norm_rel(rel_path) or ('Mir200/' + SAFE_SETUP_FILENAME)
        safe_setup_path = self._get_safe_setup_path_for_pack()
        if safe_setup_path is None:
            return None
        try:
            st = safe_setup_path.stat()
        except OSError as exc:
            messagebox.showwarning('提示', f'读取安全 !Setup.txt 失败：{exc}')
            return None
        return {
            'rel_path': rel_path,
            'name': SAFE_SETUP_FILENAME,
            'type': 'file',
            'filtered': False,
            'status': 'normal',
            'abs_path': str(safe_setup_path),
            'size': st.st_size,
            'mtime': int(st.st_mtime),
            'md5': '',
        }

    def _collect_files_from_current_checked(self) -> Optional[List[Dict]]:
        result_by_rel: Dict[str, Dict] = {}
        for meta in self.tree_meta.values():
            if meta.get('display_disabled') or meta.get('check_state') != 1:
                continue
            rel = norm_rel(meta.get('rel_path', ''))
            if not rel or is_force_hidden_path(rel) or is_admin_list_file(rel):
                continue
            if meta.get('type') == 'dir':
                # 勾选空目录时也要进入压缩包；否则只有文件会被复制，空目录会丢失。
                result_by_rel[rel] = self._build_dir_meta(rel)
                continue
            if meta.get('type') != 'file':
                continue
            if is_setup_file(rel):
                safe_meta = self._build_safe_setup_meta(rel)
                if safe_meta is None:
                    return None
                result_by_rel[rel] = safe_meta
                continue
            result_by_rel[rel] = meta
        return [result_by_rel[k] for k in sorted(result_by_rel)]

    def _build_dir_meta(self, rel_dir: str) -> Dict:
        rel_dir = norm_rel(rel_dir)
        return {
            'rel_path': rel_dir,
            'name': Path(rel_dir).name,
            'type': 'dir',
            'filtered': False,
            'status': 'normal',
            'abs_path': '',
            'size': 0,
            'mtime': 0,
            'md5': '',
        }

    def _build_file_meta_from_disk(self, import_root: Path, abs_file: Path, rel_file: str, status: str = 'normal') -> Optional[Dict]:
        rel_file = norm_rel(rel_file)
        if is_force_hidden_path(rel_file) or is_admin_list_file(rel_file):
            return None
        try:
            st = abs_file.stat()
        except OSError:
            return None
        return {
            'rel_path': rel_file,
            'name': abs_file.name,
            'type': 'file',
            'filtered': False,
            'status': status,
            'abs_path': str(abs_file),
            'size': st.st_size,
            'mtime': int(st.st_mtime),
            'md5': '',
        }

    def _collect_files_from_rule(self, rule: PackRule) -> Optional[List[Dict]]:
        """按规则实时解析磁盘文件。

        规则里保存的是目录时，打包时必须实时扫描该目录，避免新建文件因为未刷新文件树而漏包；
        规则里保存的是具体文件时，只打包该具体文件。
        """
        import_dir = self.import_dir_var.get().strip()
        if not import_dir or not Path(import_dir).exists():
            messagebox.showwarning('提示', '导入目录不存在，请检查')
            return []

        import_root = Path(import_dir)
        filter_cfg = self.store.load_filter_config()
        checked_dirs = sorted({norm_rel(x) for x in rule.checked_dirs if norm_rel(x)})
        checked_files = sorted({norm_rel(x) for x in rule.checked_files if norm_rel(x)})
        excluded = {norm_rel(x) for x in rule.excluded_files}
        result_by_rel: Dict[str, Dict] = {}

        def add_dir(rel_dir: str):
            rel_dir = norm_rel(rel_dir)
            if not rel_dir or rel_dir in excluded or rule_package_user_excluded(rel_dir, True, filter_cfg):
                return
            if is_force_hidden_path(rel_dir) or is_admin_list_file(rel_dir):
                return
            if not path_allowed(rel_dir, True, filter_cfg):
                return
            result_by_rel.setdefault(rel_dir, self._build_dir_meta(rel_dir))

        def add_file(abs_file: Path, rel_file: str, status: str = 'normal'):
            rel_file = norm_rel(rel_file)
            if not rel_file or rel_file in excluded or rule_package_user_excluded(rel_file, False, filter_cfg):
                return True
            if is_force_hidden_path(rel_file) or is_admin_list_file(rel_file):
                return True
            if is_setup_file(rel_file):
                safe_meta = self._build_safe_setup_meta(rel_file)
                if safe_meta is None:
                    return False
                result_by_rel[rel_file] = safe_meta
                return True
            if not path_allowed(rel_file, False, filter_cfg):
                return True
            meta = self._build_file_meta_from_disk(import_root, abs_file, rel_file, status=status)
            if meta is not None:
                result_by_rel[rel_file] = meta
            return True

        # 1. 目录规则实时扫描：目录中新建的文件无需刷新文件树，也能进入规则包。
        for rel_dir in checked_dirs:
            if not rel_dir or rel_dir in excluded or rule_package_user_excluded(rel_dir, True, filter_cfg):
                continue
            if is_force_hidden_path(rel_dir) or is_admin_list_file(rel_dir):
                continue
            if not path_allowed(rel_dir, True, filter_cfg):
                continue
            abs_dir = import_root / Path(rel_dir)
            if not abs_dir.is_dir():
                continue

            for dirpath, dirnames, filenames in os.walk(abs_dir):
                current = Path(dirpath)
                cur_rel = norm_rel(str(current.relative_to(import_root)))

                kept_dirs = []
                for d in dirnames:
                    child_rel = norm_rel(str((current / d).relative_to(import_root)))
                    if not path_allowed(child_rel, True, filter_cfg):
                        continue
                    if child_rel in excluded or rule_package_user_excluded(child_rel, True, filter_cfg):
                        continue
                    if is_force_hidden_path(child_rel) or is_admin_list_file(child_rel):
                        continue
                    kept_dirs.append(d)
                dirnames[:] = kept_dirs

                if cur_rel and (not path_allowed(cur_rel, True, filter_cfg) or rule_package_user_excluded(cur_rel, True, filter_cfg)):
                    continue
                add_dir(cur_rel)
                for fn in filenames:
                    abs_file = current / fn
                    rel_file = norm_rel(str(abs_file.relative_to(import_root)))
                    if add_file(abs_file, rel_file) is False:
                        return None

        # 2. 文件规则精确引用：只打规则内保存的文件，不因为同目录新增文件而扩展。
        for rel_file in checked_files:
            if not rel_file or rel_file in excluded or rule_package_user_excluded(rel_file, False, filter_cfg):
                continue
            if is_force_hidden_path(rel_file) or is_admin_list_file(rel_file):
                continue
            if is_setup_file(rel_file):
                safe_meta = self._build_safe_setup_meta(rel_file)
                if safe_meta is None:
                    return None
                result_by_rel[norm_rel(rel_file)] = safe_meta
                continue
            abs_file = import_root / Path(rel_file)
            if abs_file.is_file():
                if add_file(abs_file, rel_file) is False:
                    return None
                continue

            # 兼容旧扫描结果：如果文件树里已有对应 meta，但磁盘此刻不可见，则忽略，不报错。
            meta = self.scan_entries.get(rel_file)
            if meta and meta.get('type') == 'file' and not meta.get('filtered'):
                rel = norm_rel(meta.get('rel_path', ''))
                if rel and rel not in excluded and not is_force_hidden_path(rel) and not is_admin_list_file(rel):
                    if is_setup_file(rel):
                        safe_meta = self._build_safe_setup_meta(rel)
                        if safe_meta is None:
                            return None
                        result_by_rel[rel] = safe_meta
                    else:
                        result_by_rel[rel] = meta

        return [result_by_rel[k] for k in sorted(result_by_rel)]

    def _set_pack_busy(self, busy: bool):
        self.packing = busy
        state = 'disabled' if busy else 'normal'
        for btn_name in ('btn_pack_rule', 'btn_pack_checked', 'btn_refresh', 'btn_set_baseline', 'btn_pack_full'):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def _show_pack_progress(self, title: str, total_steps: int):
        self._close_pack_progress()
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.protocol('WM_DELETE_WINDOW', lambda: None)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=title).pack(anchor='w')
        self.pack_status_label = ttk.Label(body, text='准备中...')
        self.pack_status_label.pack(anchor='w', pady=(8, 6))
        self.pack_progress = ttk.Progressbar(body, mode='determinate', length=360, maximum=max(1, total_steps))
        self.pack_progress.pack(fill='x')

        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
            dlg.geometry(f'+{max(0, x)}+{max(0, y)}')
        except Exception:
            pass

        self.pack_dialog = dlg

    def _close_pack_progress(self):
        try:
            if self.pack_dialog is not None and self.pack_dialog.winfo_exists():
                self.pack_dialog.destroy()
        except Exception:
            pass
        self.pack_dialog = None
        self.pack_progress = None
        self.pack_status_label = None

    def _pack_worker(self, export_dir: str, import_root_name: str, files: List[Dict]):
        export_path = Path(export_dir)
        export_path.mkdir(parents=True, exist_ok=True)
        temp_dir = export_path / f'_temp_build_{os.getpid()}_{int(datetime.now().timestamp())}'
        total_steps = max(1, len(files) * 2 + 4)
        step = 0

        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            step += 1
            self.pack_queue.put(('progress', step, total_steps, f'已创建临时目录：{temp_dir.name}'))

            copied_items = []
            for meta in files:
                dest = temp_dir / Path(meta['rel_path'])
                if meta.get('type') == 'dir':
                    dest.mkdir(parents=True, exist_ok=True)
                    copied_items.append((dest, 'dir'))
                    step += 1
                    self.pack_queue.put(('progress', step, total_steps, f'创建目录：{meta["rel_path"]}'))
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                if meta.get('virtual_content') is not None:
                    content = meta.get('virtual_content')
                    if isinstance(content, bytes):
                        dest.write_bytes(content)
                    else:
                        dest.write_text(str(content), encoding='utf-8')
                else:
                    src = Path(meta['abs_path'])
                    shutil.copy2(src, dest)
                copied_items.append((dest, 'file'))
                step += 1
                self.pack_queue.put(('progress', step, total_steps, f'复制文件：{meta["name"]}'))

            zip_path = export_path / f'{import_root_name}.zip'
            if zip_path.exists():
                zip_path.unlink()

            def write_zip_file():
                nonlocal step
                if zip_path.exists():
                    zip_path.unlink()

                def zip_dt(path_obj: Optional[Path] = None):
                    try:
                        ts = path_obj.stat().st_mtime if path_obj is not None and path_obj.exists() else datetime.now().timestamp()
                        dt = datetime.fromtimestamp(ts)
                    except Exception:
                        dt = datetime.now()
                    year = max(1980, dt.year)
                    return (year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

                def make_dir_info(arc_dir: str, src_dir: Optional[Path] = None):
                    if not arc_dir.endswith('/'):
                        arc_dir += '/'
                    info = zipfile.ZipInfo(arc_dir, date_time=zip_dt(src_dir))
                    info.create_system = 0
                    info.external_attr = 0x10
                    info.compress_type = zipfile.ZIP_STORED
                    return info

                def make_file_info(arc_file: str, src_file: Path):
                    info = zipfile.ZipInfo(arc_file, date_time=zip_dt(src_file))
                    info.create_system = 0
                    info.external_attr = 0x80
                    info.compress_type = zipfile.ZIP_DEFLATED
                    return info

                def collect_parent_dirs(arc_file: str):
                    parts = arc_file.split('/')[:-1]
                    dirs = []
                    cur = ''
                    for part in parts:
                        if not part:
                            continue
                        cur = f'{cur}/{part}' if cur else part
                        dirs.append(cur + '/')
                    return dirs

                written_dirs = set()

                def write_dir(zf, arc_dir: str, src_dir: Optional[Path] = None, progress_name: str = ''):
                    if not arc_dir.endswith('/'):
                        arc_dir += '/'
                    if arc_dir in written_dirs:
                        return
                    zf.writestr(make_dir_info(arc_dir, src_dir), b'')
                    written_dirs.add(arc_dir)
                    if progress_name:
                        self.pack_queue.put(('progress', step, total_steps, f'压缩目录：{progress_name}'))

                with zipfile.ZipFile(zip_path, 'w') as zf:
                    sorted_items = sorted(copied_items, key=lambda item: (0 if item[1] == 'dir' else 1, item[0].as_posix()))
                    for p, kind in sorted_items:
                        arcname = (Path(import_root_name) / p.relative_to(temp_dir)).as_posix()
                        if kind == 'dir':
                            if not arcname.endswith('/'):
                                arcname += '/'
                            # v108: 使用 Windows/WinRAR 风格目录项，并补齐父级目录，提升上传平台兼容性。
                            for d in collect_parent_dirs(arcname.rstrip('/')):
                                write_dir(zf, d)
                            write_dir(zf, arcname, p)
                            step += 1
                            self.pack_queue.put(('progress', step, total_steps, f'压缩目录：{p.name}'))
                        else:
                            # v108: 文件写入前显式写入父级目录 entry，避免平台无法解析深层路径。
                            for d in collect_parent_dirs(arcname):
                                write_dir(zf, d)
                            zf.writestr(make_file_info(arcname, p), p.read_bytes())
                            step += 1
                            self.pack_queue.put(('progress', step, total_steps, f'压缩文件：{p.name}'))

            write_zip_file()

            shutil.rmtree(temp_dir, ignore_errors=True)
            step += 1
            self.pack_queue.put(('progress', step, total_steps, '已清理临时目录'))
            self.pack_queue.put(('done', str(zip_path)))
        except Exception as e:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            self.pack_queue.put(('error', str(e)))

    def _get_output_root_name(self, prefix: str = 'MirServer') -> str:
        if self.rename_export_zip.get():
            return 'MirServer'
        import_name = Path(self.import_dir_var.get().strip()).name.strip()
        return import_name or prefix or 'MirServer'

    def _start_pack_task(self, files: List[Dict], prefix: str, update_baseline: bool, export_kind: str = 'single'):
        export_dir = self._resolve_export_dir(export_kind)
        if not export_dir:
            messagebox.showwarning('提示', '导出目录为空，请检查')
            return
        if self.packing:
            return

        import_root_name = self._get_output_root_name(prefix)
        self.pack_context = {
            'group_name': self.current_group_name.get().strip(),
            'files': files,
            'update_baseline': update_baseline,
            'export_dir': export_dir,
            'export_kind': export_kind,
        }
        total_steps = max(1, len(files) * 2 + 4)
        self._set_pack_busy(True)
        self._show_pack_progress('正在打包', total_steps)
        self.log(f'开始后台打包，共 {len(files)} 个项目')

        t = threading.Thread(
            target=self._pack_worker,
            args=(export_dir, import_root_name, files),
            daemon=True
        )
        t.start()

    def _process_pack_queue(self):
        try:
            while True:
                item = self.pack_queue.get_nowait()
                kind = item[0]
                if kind == 'progress':
                    _, current, total, message = item
                    if self.pack_progress is not None:
                        self.pack_progress.configure(maximum=max(1, total), value=current)
                    if self.pack_status_label is not None:
                        self.pack_status_label.configure(text=message)
                elif kind == 'done':
                    _, zip_path = item
                    self._set_pack_busy(False)
                    self._close_pack_progress()
                    if self.pack_context:
                        self._remember_export_dir(self.pack_context.get('export_dir', ''), self.pack_context.get('export_kind', 'single'))
                    if self.pack_context and self.pack_context.get('update_baseline'):
                        self._update_baseline_for_files(self.pack_context['group_name'], self.pack_context['files'])
                        self.log('已根据勾选打包结果更新基线')
                    zip_size = Path(zip_path).stat().st_size if Path(zip_path).exists() else 0
                    self.log(f'压缩包生成成功：{Path(zip_path).name}（{zip_size / 1024:.1f} KB）')
                    messagebox.showinfo('完成', f'压缩包生成成功：\n{zip_path}')
                    if self.pack_context and self.open_export_dir_after_pack.get():
                        self._open_directory(self.pack_context.get('export_dir', ''))
                    self.pack_context = None
                elif kind == 'error':
                    _, err = item
                    self._set_pack_busy(False)
                    self._close_pack_progress()
                    self.log(f'打包失败：{err}')
                    messagebox.showerror('错误', f'打包失败：{err}')
                    self.pack_context = None
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._process_pack_queue)

    def _validate_common_pack_paths(self, export_kind: str = 'single'):
        group_name = self.current_group_name.get().strip()
        if not group_name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return None, None, None
        import_dir = self.import_dir_var.get().strip()
        if not import_dir or not Path(import_dir).exists():
            messagebox.showwarning('提示', '导入目录不存在，请检查')
            return None, None, None
        export_dir = self._resolve_export_dir(export_kind)
        if not export_dir:
            messagebox.showwarning('提示', '导出目录为空，请检查')
            return None, None, None
        return group_name, Path(import_dir), export_dir

    def _collect_full_package_files(self) -> Optional[List[Dict]]:
        group = self._get_loaded_group_object()
        group_name, import_root, export_dir = self._validate_common_pack_paths('full')
        if not group_name:
            return None
        safe_setup_path = self._get_safe_setup_path_for_pack()
        if safe_setup_path is None:
            return None
        filter_cfg = self.store.load_filter_config()

        result_by_rel: Dict[str, Dict] = {}
        for root_name in FULL_PACKAGE_INCLUDE_ROOTS:
            scan_root = import_root / root_name
            if not scan_root.exists():
                self.log(f'[母包] 跳过不存在目录：{root_name}')
                continue
            if scan_root.is_file():
                continue
            if user_blacklist_excluded(root_name, True, filter_cfg) or full_package_user_excluded(root_name, True, filter_cfg):
                self.log(f'[母包] 用户规则排除目录：{root_name}')
                continue
            for dirpath, dirnames, filenames in os.walk(scan_root):
                current = Path(dirpath)
                rel_dir = norm_rel(str(current.relative_to(import_root)))
                # 原地裁剪排除目录，避免继续向下扫描。
                kept = []
                for d in dirnames:
                    child_rel = norm_rel(str((current / d).relative_to(import_root)))
                    if (
                        is_full_package_excluded(child_rel, True)
                        or user_blacklist_excluded(child_rel, True, filter_cfg)
                        or full_package_user_excluded(child_rel, True, filter_cfg)
                    ):
                        continue
                    kept.append(d)
                dirnames[:] = kept
                if rel_dir and (
                    is_full_package_excluded(rel_dir, True)
                    or user_blacklist_excluded(rel_dir, True, filter_cfg)
                    or full_package_user_excluded(rel_dir, True, filter_cfg)
                ):
                    continue
                if rel_dir:
                    result_by_rel.setdefault(rel_dir, self._build_dir_meta(rel_dir))
                for fn in filenames:
                    abs_file = current / fn
                    rel_file = norm_rel(str(abs_file.relative_to(import_root)))
                    # 母包中 AdminList.txt 由工具创建空文件，不引用导入目录内容。
                    if rel_file.lower() == ADMIN_LIST_REL_PATH.lower():
                        continue
                    if (
                        is_force_hidden_path(rel_file)
                        or is_setup_file(rel_file)
                        or is_full_package_excluded(rel_file, False)
                        or user_blacklist_excluded(rel_file, False, filter_cfg)
                        or full_package_user_excluded(rel_file, False, filter_cfg)
                    ):
                        continue
                    try:
                        st = abs_file.stat()
                    except OSError:
                        continue
                    result_by_rel[rel_file] = {
                        'rel_path': rel_file,
                        'name': fn,
                        'type': 'file',
                        'filtered': False,
                        'status': 'normal',
                        'abs_path': str(abs_file),
                        'size': st.st_size,
                        'mtime': int(st.st_mtime),
                        'md5': '',
                    }

        # !Setup.txt 不从导入目录读取，只从当前打包组安全模板注入。
        try:
            st = safe_setup_path.stat()
            result_by_rel['Mir200/' + SAFE_SETUP_FILENAME] = {
                'rel_path': 'Mir200/' + SAFE_SETUP_FILENAME,
                'name': SAFE_SETUP_FILENAME,
                'type': 'file',
                'filtered': False,
                'status': 'normal',
                'abs_path': str(safe_setup_path),
                'size': st.st_size,
                'mtime': int(st.st_mtime),
                'md5': '',
            }
        except OSError as exc:
            messagebox.showwarning('提示', f'读取安全 !Setup.txt 失败：{exc}')
            return None

        # AdminList.txt 保存管理员账号，母包不引用导入目录内容，统一生成空文件。
        result_by_rel[ADMIN_LIST_REL_PATH] = {
            'rel_path': ADMIN_LIST_REL_PATH,
            'name': 'AdminList.txt',
            'type': 'file',
            'filtered': False,
            'status': 'normal',
            'abs_path': '',
            'size': 0,
            'mtime': 0,
            'md5': '',
            'virtual_content': '',
        }
        self.log(f'[母包] 已生成空白管理员列表：{ADMIN_LIST_REL_PATH}')

        result = [result_by_rel[k] for k in sorted(result_by_rel)]
        file_count = sum(1 for x in result if x.get('type') == 'file')
        dir_count = sum(1 for x in result if x.get('type') == 'dir')
        self.log(f'[母包] 扫描完成，共 {file_count} 个文件，{dir_count} 个目录')
        self.log(f'[母包] 使用工具内部安全 !Setup.txt 模板：{safe_setup_path}')
        return result

    def pack_full_package(self):
        files = self._collect_full_package_files()
        if not files:
            return
        # 母包打包不读取“打包前预览”复选框状态；该选项仅用于规则打包和勾选打包。
        self._start_pack_task(files, 'mother_package', update_baseline=False, export_kind='full')

    def pack_by_rule(self):
        group_name = self.current_group_name.get().strip()
        if not group_name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        rule_name = self.current_rule_name.get().strip()
        if not rule_name:
            messagebox.showwarning('提示', '请先新建或选择一条规则')
            return
        rule = self.store.load_rule(group_name, rule_name)
        if not rule:
            messagebox.showwarning('提示', '当前规则不存在')
            return
        files = self._collect_files_from_rule(rule)
        if files is None:
            return
        if not files:
            messagebox.showwarning('提示', '当前规则没有可打包文件')
            return
        prefix = f'{group_name}_{rule_name}'
        if self.preview_before_pack.get():
            self._show_checked_pack_preview(files, prefix, update_baseline=False, export_kind='hotfix')
            return
        self._start_pack_task(files, prefix, update_baseline=False, export_kind='hotfix')

    def _show_checked_pack_preview(self, files: List[Dict], prefix: str, update_baseline: bool, export_kind: str = 'single'):
        if self.packing:
            return
        export_dir = self._resolve_export_dir(export_kind)
        import_root_name = self._get_output_root_name(prefix)
        zip_name = f'{import_root_name}.zip'
        preview_files = list(files)

        dlg = tk.Toplevel(self.root)
        dlg.title('打包预览')
        dlg.transient(self.root)
        dlg.resizable(True, True)
        dlg.minsize(680, 460)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill='both', expand=True)

        count_var = tk.StringVar()
        ttk.Label(body, text='本次将按以下最终文件清单进行打包：').pack(anchor='w')
        ttk.Label(body, textvariable=count_var).pack(anchor='w', pady=(8, 0))
        ttk.Label(body, text=f'压缩包名称：{zip_name}').pack(anchor='w', pady=(2, 0))
        ttk.Label(body, text=f'导出目录：{export_dir}').pack(anchor='w', pady=(2, 6))
        ttk.Label(body, text='可多选后移除：支持 Ctrl 多选、Shift 连选、Delete 删除选中、Ctrl+A 全选。', foreground='#666666').pack(anchor='w', pady=(0, 8))

        list_wrap = ttk.Frame(body)
        list_wrap.pack(fill='both', expand=True)
        preview_list = tk.Listbox(list_wrap, activestyle='dotbox', selectmode='extended')
        ybar = ttk.Scrollbar(list_wrap, orient='vertical', command=preview_list.yview)
        preview_list.configure(yscrollcommand=ybar.set)
        preview_list.pack(side='left', fill='both', expand=True)
        ybar.pack(side='right', fill='y')

        def refresh_preview_list():
            preview_list.delete(0, 'end')
            for meta in preview_files:
                preview_list.insert('end', meta.get('rel_path', ''))
            count_var.set(f'项目总数：{len(preview_files)}（包含文件和需要保留的空目录）')

        def remove_selected():
            sels = list(preview_list.curselection())
            if not sels:
                return
            for idx in reversed(sels):
                if 0 <= idx < len(preview_files):
                    preview_files.pop(idx)
            refresh_preview_list()
            if preview_files:
                next_idx = min(sels[0], len(preview_files) - 1)
                preview_list.selection_set(next_idx)
                preview_list.activate(next_idx)
                preview_list.see(next_idx)

        def select_all(_event=None):
            preview_list.selection_set(0, 'end')
            return 'break'

        def delete_selected(_event=None):
            remove_selected()
            return 'break'

        preview_list.bind('<Control-a>', select_all)
        preview_list.bind('<Control-A>', select_all)
        preview_list.bind('<Delete>', delete_selected)
        refresh_preview_list()

        btn_row = ttk.Frame(body)
        btn_row.pack(fill='x', pady=(10, 0))

        def copy_list():
            lines = [str(meta.get('rel_path', '')) for meta in preview_files]
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append('\n'.join(lines))
                self.log(f'已复制打包预览列表，共 {len(lines)} 项')
            except Exception:
                pass

        def confirm():
            if not preview_files:
                messagebox.showwarning('提示', '预览列表为空，无法打包', parent=dlg)
                return
            final_files = list(preview_files)
            try:
                dlg.destroy()
            except Exception:
                pass
            self._start_pack_task(final_files, prefix, update_baseline=update_baseline, export_kind=export_kind)

        ttk.Button(btn_row, text='移除选中', command=remove_selected).pack(side='left')
        ttk.Button(btn_row, text='复制列表', command=copy_list).pack(side='left', padx=(8, 0))
        ttk.Button(btn_row, text='取消', command=dlg.destroy).pack(side='right')
        ttk.Button(btn_row, text='确认打包', command=confirm).pack(side='right', padx=(0, 6))

        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
            dlg.geometry(f'{max(680, dlg.winfo_width())}x{max(460, dlg.winfo_height())}+{max(0, x)}+{max(0, y)}')
        except Exception:
            pass
        try:
            dlg.grab_set()
        except Exception:
            pass


    def pack_by_checked(self):
        group_name = self.current_group_name.get().strip()
        if not group_name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        files = self._collect_files_from_current_checked()
        if files is None:
            return
        if not files:
            messagebox.showwarning('提示', '当前没有勾选任何文件')
            return
        prefix = f'{group_name}_checked'
        if self.preview_before_pack.get():
            self._show_checked_pack_preview(files, prefix, update_baseline=True, export_kind='hotfix')
            return
        self._start_pack_task(files, prefix, update_baseline=True, export_kind='hotfix')

    def _update_baseline_for_files(self, group_name: str, files: List[Dict]):
        manifest = self.store.load_manifest(group_name)
        manifest.setdefault('files', {})
        manifest['group_name'] = group_name
        manifest['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for meta in files:
            manifest['files'][meta['rel_path']] = {
                'size': meta['size'],
                'mtime': meta['mtime'],
                'md5': meta['md5'],
            }
        self.store.save_manifest(group_name, manifest)

    def set_current_baseline(self):
        group_name = self.current_group_name.get().strip()
        if not group_name:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        if not self.scan_entries:
            messagebox.showwarning('提示', '请先刷新文件')
            return
        manifest = {
            'group_name': group_name,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'files': {}
        }
        for rel, meta in self.scan_entries.items():
            if not rel or meta['type'] != 'file':
                continue
            manifest['files'][rel] = {
                'size': meta['size'],
                'mtime': meta['mtime'],
                'md5': meta['md5'],
            }
        self.store.save_manifest(group_name, manifest)
        self.log('已设为当前基线')
        # 立即重新渲染一次，让新增/修改状态及时消失
        self.refresh_files()
        messagebox.showinfo('完成', '当前状态已写入基线，并已刷新文件列表')


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
    except Exception:
        pass
    App(root)
    center_root_window(root)
    root.deiconify()
    root.mainloop()


if __name__ == '__main__':
    main()
