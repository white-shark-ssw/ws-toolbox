"""Independent client UI/task adapter; it shares only protection and task output."""
import threading
from tkinter import ttk, messagebox

from .pipeline import plan_client, export_client


class ClientActions:
    def _build_client_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=2)
        row.columnconfigure(1, weight=1, uniform='client_paths')
        row.columnconfigure(3, weight=1, uniform='client_paths')
        ttk.Label(row, text='客户端导入:').grid(row=0, column=0, sticky='w')
        ttk.Entry(row, textvariable=self.client_import_var, width=1).grid(row=0, column=1, sticky='ew', padx=(6, 8))
        ttk.Label(row, text='客户端导出:').grid(row=0, column=2, sticky='w')
        ttk.Entry(row, textvariable=self.client_export_var, width=1).grid(row=0, column=3, sticky='ew', padx=(6, 8))
        self.btn_client_export = self._make_pack_button(row, '加密/混淆', self.start_client_export, '#0e7490', '#155e75', width=10)
        self.btn_client_export.grid(row=0, column=4, sticky='e')
        self.client_row = row



    def start_client_export(self):
        if self.packing or self.scanning:
            return
        group = self.current_group_name.get().strip()
        if not group:
            messagebox.showwarning('提示', '请先新建或选择一个打包组')
            return
        source, output = self.client_import_var.get().strip(), self.client_export_var.get().strip()
        try:
            plan = plan_client(source, output)
        except Exception as error:
            messagebox.showwarning('客户端路径检查', str(error))
            return
        options = self._protection_options()
        self.pack_context = {'kind': 'client', 'group_name': group, 'client_import': source,
                             'client_export': output, 'export_dir': str(plan.output), 'options': options,
                             'open_after': bool(self.open_export_dir_after_pack.get())}
        self._task_cancel = threading.Event()
        self._set_pack_busy(True)
        self._show_pack_progress('客户端加密/混淆', len(plan.files))
        self.log(f'客户端任务开始：{len(plan.files)} 个Lua，加密={options.encrypt}，混淆={options.obfuscate}')
        threading.Thread(target=self._client_worker, args=(plan, options, self._task_cancel), daemon=True).start()

    def _client_worker(self, plan, options, cancelled):
        try:
            progress = lambda current, total, message: self.pack_queue.put(('progress', current, total, message))
            result = export_client(plan, options, progress, cancelled)
            self.pack_queue.put(('done', result.output, result))
        except Exception as error:
            self.pack_queue.put(('error', str(error)))
