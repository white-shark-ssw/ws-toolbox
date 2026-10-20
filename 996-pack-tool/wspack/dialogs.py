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
            '   “×”只移除当前历史记录并清空输入框，不删除真实文件夹。\n\n'
            '10. Lua加密/混淆\n'
            '   文件区域右侧两个公共开关同时用于服务端和客户端，仅处理Lua。\n'
            '   两项均关闭保持原样；任一开启均删除业务注释、保留原始行号。\n'
            '   混淆仅修改局部名称，保留全局、字段和字符串；加密加载壳随文件提供。\n\n'
            '11. 客户端独立处理\n'
            '   客户端导入可填Lua文件或目录，导出必须填写文件夹。\n'
            '   点击“加密/混淆”递归输出Lua，保持相对结构，不额外套一层文件夹。\n'
            '   非Lua不输出；原文件不改，导入和导出不可重叠。\n'
        )
        txt.insert('1.0', content)
        txt.configure(state='disabled')

        bottom = ttk.Frame(body)
        bottom.pack(fill='x', pady=(10, 0))
        ttk.Label(bottom, text='QQ：853169679', foreground='#d9534f').pack(side='left')
        ttk.Button(bottom, text='关闭', command=self.destroy).pack(side='right')

