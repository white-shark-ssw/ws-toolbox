"""Native Tk checks run automatically on Windows/available displays, otherwise skip."""
import tkinter as tk
from tkinter import ttk

import pytest

from wspack import app as module
from wspack import client
from wspack.models import PackGroup


@pytest.fixture
def ui(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip('Native Tk display unavailable: ' + str(error))
    style = ttk.Style(root)
    if 'vista' in style.theme_names():
        style.theme_use('vista')
    monkeypatch.setattr(module, 'config_base_dir', lambda: tmp_path)
    obj = module.App(root)
    root.geometry('1080x750')
    root.update_idletasks()
    yield obj
    root.destroy()


def test_minimum_layout_and_colors(ui):
    root = ui.root
    assert root.minsize() == (1080, 750)
    for widget in (ui.btn_client_export, ui.encrypt_checkbox, ui.obfuscate_checkbox):
        right = widget.winfo_rootx() + widget.winfo_width()
        assert widget.winfo_ismapped()
        assert right <= root.winfo_rootx() + root.winfo_width()
    assert ui.btn_client_export.cget('text') == '加密/混淆'
    assert ui.encrypt_checkbox.cget('foreground') == '#c43b35'
    assert ui.obfuscate_checkbox.cget('foreground') == '#7c3aed'
    assert ui.export_row.winfo_rooty() < ui.client_row.winfo_rooty() < ui.btn_pack_full.winfo_rooty()


def test_options_persist_and_controls_restore(ui):
    assert not ui.encrypt_enabled.get() and not ui.obfuscate_enabled.get()
    ui.encrypt_checkbox.invoke()
    ui.obfuscate_checkbox.invoke()
    saved = ui.store.load_ui_config()
    assert saved['encrypt_enabled'] and saved['obfuscate_enabled']
    ui._set_pack_busy(True)
    assert str(ui.group_combo.cget('state')) == 'disabled'
    assert str(ui.btn_client_export.cget('state')) == 'disabled'
    ui._set_pack_busy(False)
    assert str(ui.group_combo.cget('state')) == 'readonly'
    assert str(ui.btn_client_export.cget('state')) == 'normal'


def test_client_paths_switch_groups_without_leaking(ui, monkeypatch):
    ui.store.save_group(PackGroup('A', client_import='D:/A/input', client_export='D:/A/output'))
    ui.store.save_group(PackGroup('B'))
    ui.current_group_name.set('A')
    ui.on_group_changed()
    assert ui.client_import_var.get() == 'D:/A/input'
    ui.current_group_name.set('B')
    ui.on_group_changed()
    assert ui.client_import_var.get() == ui.client_export_var.get() == ''
    monkeypatch.setattr(module.simpledialog, 'askstring', lambda *a, **kw: 'C')
    ui.new_group()
    assert ui.client_import_var.get() == ui.client_export_var.get() == ''


def test_new_group_invalidates_previous_scan(ui, monkeypatch):
    generation = ui.scan_generation
    ui.scanning = True
    monkeypatch.setattr(module.simpledialog, 'askstring', lambda *a, **kw: 'fresh')
    ui.new_group()
    assert ui.scan_generation > generation and not ui.scanning
