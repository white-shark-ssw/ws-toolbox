"""Shared frozen task options, local build records and UI completion handling."""
import json
from datetime import datetime
import uuid
from tkinter import messagebox
from .protection.processor import ProtectionOptions


class TaskActions:
    def _protection_options(self):
        return ProtectionOptions(bool(self.encrypt_enabled.get()), bool(self.obfuscate_enabled.get()))


    def on_protection_toggle(self):
        self._save_ui_config()
        options = self._protection_options()
        self.log(f'公共Lua选项：加密={options.encrypt}，混淆={options.obfuscate}；任一开启时删除业务注释并保留行号')


    def cancel_current_task(self):
        event = getattr(self, '_task_cancel', None)
        if event is not None:
            event.set()
            if self.pack_status_label is not None:
                self.pack_status_label.configure(text='正在取消，等待当前文件完成后清理暂存数据…')


    def _record_build(self, result, context):
        """Keep public hashes/status local to the tool, never in the release tree."""
        directory = self.store.base_dir / '.build_reports'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.json'
            options = context.get('options', ProtectionOptions())
            record = {'kind': context.get('kind', 'server'), 'group': context['group_name'],
                      'encrypt': options.encrypt, 'obfuscate': options.obfuscate,
                      'files': result.files, 'lua_files': result.lua_files,
                      'elapsed_seconds': result.elapsed_seconds, 'files_report': result.reports}
            (directory / name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as error:
            self.log(f'输出已生成，但本地构建记录未保存：{error}')


    def _finish_task(self, result):
        context = self.pack_context or {}
        problem = None
        try:
            if context.get('kind') == 'client':
                group = self.store.load_group(context['group_name'])
                if group is not None:
                    group.client_import = context['client_import']
                    group.client_export = context['client_export']
                    self.store.save_group(group)
            else:
                group = self.store.remember_task_export(context['group_name'], context['export_dir'],
                                                        context.get('export_kind', 'single'), context['dual_mode'],
                                                        context['import_dir'])
                if group is not None and self.current_group_name.get().strip() == context['group_name']:
                    if context['dual_mode']:
                        target = {'full': self.full_export_dir_var, 'hotfix': self.hotfix_export_dir_var}.get(context.get('export_kind'), self.export_dir_var)
                        target.set(context['export_dir'])
                    else:
                        self._refresh_export_preset_combo(group, keep_current=context['export_dir'])
                if context.get('update_baseline'):
                    self._update_baseline_for_files(context['group_name'], result.source_records)
                    self.log('已根据本次原始源文件快照更新基线')
            self._record_build(result, context)
        except Exception as error:
            problem = str(error)
        finally:
            self._close_pack_progress()
            self._set_pack_busy(False)
            self.pack_context = None
        self.log(f'任务完成：{result.files} 个文件，其中 {result.lua_files} 个Lua，耗时 {result.elapsed_seconds:.2f} 秒')
        self.log('输出：' + result.output)
        if problem:
            messagebox.showwarning('输出已生成', '文件已生成，但配置或基线更新失败：\n' + problem)
        else:
            messagebox.showinfo('完成', '输出生成成功：\n' + result.output)
        if context.get('open_after'):
            self._open_directory(context.get('export_dir', ''))
