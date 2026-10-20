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


class FileScanner:
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

