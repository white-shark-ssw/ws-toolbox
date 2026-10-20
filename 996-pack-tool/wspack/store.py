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
from .filters import norm_rel
from .files import md5_of_file

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
            client_import=str(data.get('client_import', '') or '').strip(),
            client_export=str(data.get('client_export', '') or '').strip(),
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

    def remember_task_export(self, group_name, export_dir, export_kind, dual_mode, import_dir):
        """Update the task's captured group, without consulting mutable UI state."""
        group = self.load_group(group_name)
        if group is None:
            return None
        if not group.import_dir and import_dir:
            group.import_dir = import_dir
        if dual_mode:
            key = {'full': 'full_export_dir', 'hotfix': 'hotfix_export_dir'}.get(export_kind, 'export_dir')
            setattr(group, key, export_dir)
        else:
            key = norm_rel(export_dir).lower()
            group.export_presets = [export_dir] + [p for p in group.export_presets if norm_rel(p).lower() != key]
            group.export_presets = group.export_presets[:MAX_EXPORT_PRESETS]
            group.export_dir = export_dir
        self.save_group(group)
        return group

