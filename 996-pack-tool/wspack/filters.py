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

