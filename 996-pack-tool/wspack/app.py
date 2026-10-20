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

from .constants import *
from .models import PackGroup, PackRule
from .store import DataStore
from .filters import *
from .files import md5_of_file, resource_path, config_base_dir
from .widgets import *

from .dialogs import *
from .scanner import FileScanner
from .collector import FileCollector
from .archive import ZipBuilder
from .client import ClientActions
from .task_ui import TaskActions

class App(FileScanner, FileCollector, ZipBuilder, ClientActions, TaskActions):
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry('1220x850')
        self.root.minsize(1080, 750)

        self.store = DataStore(config_base_dir())

        self.current_group_name = tk.StringVar()
        self.current_rule_name = tk.StringVar()
        self.current_export_preset_name = tk.StringVar()
        self.import_dir_var = tk.StringVar()
        self.export_dir_var = tk.StringVar()
        self.full_export_dir_var = tk.StringVar()
        self.hotfix_export_dir_var = tk.StringVar()
        self.client_import_var = tk.StringVar()
        self.client_export_var = tk.StringVar()
        self.show_filtered_only = tk.BooleanVar(value=True)
        self.show_hidden_items = tk.BooleanVar(value=False)

        self.ui_config = self.store.load_ui_config()
        self.encrypt_enabled = tk.BooleanVar(value=bool(self.ui_config.get('encrypt_enabled', False)))
        self.obfuscate_enabled = tk.BooleanVar(value=bool(self.ui_config.get('obfuscate_enabled', False)))
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
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
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
        self.ui_config['encrypt_enabled'] = bool(self.encrypt_enabled.get())
        self.ui_config['obfuscate_enabled'] = bool(self.obfuscate_enabled.get())
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

        self._build_client_row(top)

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
        # tk.Checkbutton retains explicit label colors under Windows vista themes.
        common_bg = ttk.Style(self.root).lookup('TFrame', 'background') or self.root.cget('background')
        self.encrypt_checkbox = tk.Checkbutton(file_ops, text='开启加密', variable=self.encrypt_enabled,
                                              command=self.on_protection_toggle, foreground='#c43b35',
                                              activeforeground='#c43b35', background=common_bg, activebackground=common_bg)
        self.encrypt_checkbox.pack(side='left', padx=(14, 0))
        self.obfuscate_checkbox = tk.Checkbutton(file_ops, text='开启混淆', variable=self.obfuscate_enabled,
                                                command=self.on_protection_toggle, foreground='#7c3aed',
                                                activeforeground='#7c3aed', background=common_bg, activebackground=common_bg)
        self.obfuscate_checkbox.pack(side='left', padx=(6, 0))
        WidgetTooltip(self.encrypt_checkbox, '服务端与客户端共用：只保护本次Lua，删除业务注释并保留行号')
        WidgetTooltip(self.obfuscate_checkbox, '服务端与客户端共用：删除业务注释，仅混淆局部名称，保留字段、字符串和行号')

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
        # Ignore a previous group's background scan after changing/new/deleting groups.
        self.scan_generation += 1
        if self.scanning:
            self._set_busy(False)
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
        self.client_import_var.set(getattr(group, 'client_import', ''))
        self.client_export_var.set(getattr(group, 'client_export', ''))
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
            client_import=self.client_import_var.get().strip(),
            client_export=self.client_export_var.get().strip(),
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
        self.client_import_var.set('')
        self.client_export_var.set('')
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
            self.client_import_var.set('')
            self.client_export_var.set('')
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
        if self.packing:
            return
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







    def _set_pack_busy(self, busy: bool):
        self.packing = busy
        if busy:
            self._task_control_states = []

            def disable(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, tk.Toplevel):
                        continue
                    try:
                        state = str(widget.cget('state'))
                        widget.configure(state='disabled')
                        self._task_control_states.append((widget, state))
                    except tk.TclError:
                        pass
                    disable(widget)

            disable(self.root)
        else:
            for widget, state in getattr(self, '_task_control_states', []):
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass
            self._task_control_states = []

    def _on_close(self):
        if self.packing:
            messagebox.showwarning('任务进行中', '请先取消当前任务，等待暂存清理或输出回滚完成后再关闭工具。')
            return
        self.root.destroy()

    def _show_pack_progress(self, title: str, total_steps: int):
        self._close_pack_progress()
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.protocol('WM_DELETE_WINDOW', self.cancel_current_task)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=title).pack(anchor='w')
        self.pack_status_label = ttk.Label(body, text='准备中...')
        self.pack_status_label.pack(anchor='w', pady=(8, 6))
        self.pack_progress = ttk.Progressbar(body, mode='determinate', length=360, maximum=max(1, total_steps))
        self.pack_progress.pack(fill='x')
        ttk.Button(body, text='取消任务', command=self.cancel_current_task).pack(anchor='e', pady=(8, 0))

        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
            dlg.geometry(f'+{max(0, x)}+{max(0, y)}')
        except Exception:
            pass

        self.pack_dialog = dlg
        dlg.grab_set()

    def _close_pack_progress(self):
        try:
            if self.pack_dialog is not None and self.pack_dialog.winfo_exists():
                self.pack_dialog.grab_release()
                self.pack_dialog.destroy()
        except Exception:
            pass
        self.pack_dialog = None
        self.pack_progress = None
        self.pack_status_label = None


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
        options = self._protection_options()
        files = [dict(meta) for meta in files]
        self.pack_context = {
            'kind': 'server',
            'group_name': self.current_group_name.get().strip(),
            'files': files,
            'update_baseline': update_baseline,
            'export_dir': export_dir,
            'export_kind': export_kind,
            'options': options,
            'open_after': bool(self.open_export_dir_after_pack.get()),
            'dual_mode': bool(self.dual_export_dir_mode.get()),
            'import_dir': self.import_dir_var.get().strip(),
        }
        self._task_cancel = threading.Event()
        total_steps = max(1, len(files) * 2 + 4)
        self._set_pack_busy(True)
        self._show_pack_progress('正在打包', total_steps)
        self.log(f'开始后台打包，共 {len(files)} 个项目；加密={options.encrypt}，混淆={options.obfuscate}')

        t = threading.Thread(
            target=self._pack_worker,
            args=(export_dir, import_root_name, files, options, self._task_cancel),
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
                    self._finish_task(item[2])
                elif kind == 'error':
                    _, err = item
                    self._set_pack_busy(False)
                    self._close_pack_progress()
                    self.log(f'任务未完成：{err}')
                    if getattr(self, '_task_cancel', None) is not None and self._task_cancel.is_set():
                        messagebox.showinfo('已取消', str(err))
                    else:
                        messagebox.showerror('错误', f'任务失败：{err}')
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

