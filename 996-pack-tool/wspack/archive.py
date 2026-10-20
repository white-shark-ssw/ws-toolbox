"""Background adapter around the filesystem-only ZIP pipeline."""
from .pipeline import build_archive
from .protection.processor import ProtectionOptions


class ZipBuilder:
    def _pack_worker(self, export_dir, import_root_name, files, options=ProtectionOptions(), cancelled=None):
        try:
            progress = lambda current, total, message: self.pack_queue.put(('progress', current, total, message))
            result = build_archive(files, export_dir, import_root_name, options, progress, cancelled)
            self.pack_queue.put(('done', result.output, result))
        except Exception as error:
            self.pack_queue.put(('error', str(error)))

