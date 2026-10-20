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

