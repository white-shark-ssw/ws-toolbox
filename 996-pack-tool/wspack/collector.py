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


class FileCollector:
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

