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
    client_import: str = ''
    client_export: str = ''


@dataclass
class PackRule:
    name: str
    checked_dirs: List[str]
    checked_files: List[str]
    excluded_files: List[str]

