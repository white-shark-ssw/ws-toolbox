"""Filesystem-only build pipelines. UI state and baseline persistence stay outside.

Every task receives a frozen options object. All Lua transformations happen in
temporary copies; outputs are committed only after processing has succeeded.
"""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
import zipfile

from .protection.processor import ProtectionOptions, protect


class BuildError(ValueError):
    pass


class TaskCancelled(BuildError):
    pass


@dataclass
class BuildResult:
    output: str
    files: int
    lua_files: int
    reports: list = field(default_factory=list)
    source_records: list = field(default_factory=list)
    elapsed_seconds: float = 0


@dataclass(frozen=True)
class ClientPlan:
    source: Path
    output: Path
    files: tuple


def check_cancel(event):
    if event is not None and event.is_set():
        raise TaskCancelled('任务已取消，未提交本次输出')


def relative_path(value):
    value = str(value).replace('\\', '/')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ':' in value or any(part in ('', '.', '..') for part in value.split('/')):
        raise BuildError('不安全的相对路径：' + value)
    return path


def _read_snapshot(source):
    before = source.stat()
    raw = source.read_bytes()
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) != after.st_size:
        raise BuildError('读取期间源文件发生变化，请停止修改后重试')
    return raw, after


def _processed(raw, rel, options, encoding, logical_name=None):
    if Path(rel).suffix.lower() != '.lua':
        return raw, None
    # Detect supported input encodings strictly, never transcode source bytes.
    # ASCII is valid UTF-8; GBK source is retained as GBK when UTF-8 is invalid.
    if options.enabled:
        try:
            raw.decode('utf-8', errors='strict')
            encoding = 'utf-8'
        except UnicodeError:
            raw.decode('gbk', errors='strict')
            encoding = 'gbk'
    # No extra runtime is imported/required when both options are off.
    result = protect(raw, options, encoding, logical_name or ('@' + rel))
    return result.output, dict(result.report, path=rel)


def write_windows_zip(path, root_name, entries, progress, cancelled):
    written = set()
    total = max(1, len(entries))

    def info(name, source, directory):
        timestamp = datetime.fromtimestamp(source.stat().st_mtime) if source else datetime.now()
        value = zipfile.ZipInfo(name, (max(1980, min(2107, timestamp.year)), timestamp.month,
                                      timestamp.day, timestamp.hour, timestamp.minute, timestamp.second))
        value.create_system = 0
        value.external_attr = 0x10 if directory else 0x80
        value.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
        return value

    def directory(archive, name, source=None):
        name = name.rstrip('/') + '/'
        if name not in written:
            archive.writestr(info(name, source, True), b'')
            written.add(name)

    with zipfile.ZipFile(path, 'w') as archive:
        for index, (rel, source, is_dir) in enumerate(sorted(entries, key=lambda x: (not x[2], x[0])), 1):
            check_cancel(cancelled)
            name = root_name + '/' + rel
            parts = name.split('/')
            for depth in range(1, len(parts)):
                directory(archive, '/'.join(parts[:depth]))
            if is_dir:
                directory(archive, name, source)
            else:
                archive.writestr(info(name, source, False), source.read_bytes())
            progress(index, total, '压缩：' + rel)


def build_archive(files, export_dir, root_name, options=ProtectionOptions(), progress=lambda *_: None, cancelled=None):
    """Build in a sibling temporary directory, then atomically replace the ZIP."""
    started = time.perf_counter()
    if relative_path(root_name).parts != (root_name,):
        raise BuildError('压缩包根目录名称异常')
    export = Path(export_dir)
    export.mkdir(parents=True, exist_ok=True)
    result = BuildResult(str(export / (root_name + '.zip')), 0, 0)
    with tempfile.TemporaryDirectory(prefix='_temp_build_', dir=export) as temporary:
        stage = Path(temporary) / 'contents'
        stage.mkdir()
        entries, seen = [], set()
        for index, meta in enumerate(files, 1):
            check_cancel(cancelled)
            rel = relative_path(meta['rel_path']).as_posix()
            if rel.casefold() in seen:
                raise BuildError('重复或大小写冲突的发布路径：' + rel)
            seen.add(rel.casefold())
            dest = stage / rel
            try:
                if meta.get('type') == 'dir':
                    dest.mkdir(parents=True, exist_ok=True)
                    entries.append((rel, dest, True))
                    result.source_records.append(dict(meta, size=0, mtime=0, md5=''))
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if meta.get('virtual_content') is not None:
                    content = meta['virtual_content']
                    raw = content if isinstance(content, bytes) else str(content).encode('utf-8')
                    stamp = None
                else:
                    raw, stamp = _read_snapshot(Path(meta['abs_path']))
                source_record = dict(meta, size=len(raw), mtime=int(stamp.st_mtime) if stamp else 0,
                                     md5=hashlib.md5(raw).hexdigest())
                # This record refers to plaintext source, not randomized release bytes.
                result.source_records.append(source_record)
                logical = rel[len('Mir200/Envir/'):] if rel.startswith('Mir200/Envir/') else rel
                output, report = _processed(raw, rel, options, 'gbk', '@' + logical)
                dest.write_bytes(output)
                if stamp:
                    os.utime(dest, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                if report:
                    result.lua_files += 1
                    result.reports.append(report)
                result.files += 1
                entries.append((rel, dest, False))
                del raw, output
                progress(index, len(files), '暂存/处理：' + rel)
            except Exception as error:
                raise BuildError(f'{rel}：暂存/保护阶段失败：{error}') from error
        check_cancel(cancelled)
        temporary_zip = Path(temporary) / 'result.zip'
        write_windows_zip(temporary_zip, root_name, entries, progress, cancelled)
        with zipfile.ZipFile(temporary_zip) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise BuildError('ZIP完整性检查失败：' + bad)
        check_cancel(cancelled)
        os.replace(temporary_zip, result.output)
    result.elapsed_seconds = time.perf_counter() - started
    return result


def plan_client(source, output):
    """Map the import root to the export root without adding a wrapper folder."""
    source_text = str(source).strip().strip('"')
    output_text = str(output).strip().strip('"')
    if not source_text or not output_text:
        raise BuildError('请填写客户端导入地址和导出目录')
    source_path = Path(source_text)
    if source_path.is_symlink():
        raise BuildError('客户端输入不支持符号链接，请填写原始文件或目录地址')
    src, dst = source_path.resolve(), Path(output_text).resolve()
    if not src.exists():
        raise BuildError('客户端导入文件或目录不存在')
    if dst.exists() and not dst.is_dir():
        raise BuildError('客户端导出必须是文件夹地址')
    files = []
    if src.is_file():
        if src.suffix.lower() != '.lua':
            raise BuildError('客户端单文件输入必须为Lua文件')
        files.append((src.name, src))
    elif src.is_dir():
        if src == dst or src.is_relative_to(dst) or dst.is_relative_to(src):
            raise BuildError('客户端导入/导出目录不可相同或相互包含，避免覆盖源码或重复处理')
        def walk_error(error):
            raise BuildError('客户端目录无法完整遍历：' + str(error)) from error

        for parent, dirs, names in os.walk(src, followlinks=False, onerror=walk_error):
            for directory in dirs:
                candidate = Path(parent) / directory
                if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                    raise BuildError('客户端输入含链接目录，请选择普通源码目录：' + str(candidate))
            dirs[:] = sorted(dirs)
            for name in sorted(names):
                file = Path(parent) / name
                if file.suffix.lower() == '.lua':
                    if file.is_symlink():
                        raise BuildError('客户端Lua文件不支持符号链接：' + str(file))
                    files.append((file.relative_to(src).as_posix(), file))
    else:
        raise BuildError('客户端输入不是普通文件或目录')
    if not files:
        raise BuildError('客户端导入范围内没有Lua文件')
    seen = set()
    for rel, file in files:
        relative_path(rel)
        if rel.casefold() in seen:
            raise BuildError('客户端存在大小写冲突的输出路径：' + rel)
        seen.add(rel.casefold())
        target = dst / rel
        if not target.resolve().is_relative_to(dst):
            raise BuildError('客户端输出路径经过符号链接离开目标目录：' + rel)
        if target.exists() and target.is_dir():
            raise BuildError('客户端输出文件位置已存在目录：' + rel)
        if target.resolve() == file.resolve() or (target.exists() and os.path.samefile(target, file)):
            raise BuildError('客户端导出不能覆盖原始Lua文件：' + rel)
    return ClientPlan(src, dst, tuple(files))


def _promote_client(stage, backup, plan, cancelled):
    committed, backups, created_dirs = [], [], []
    try:
        # Validate every destination before touching any existing output file.
        for rel, _ in plan.files:
            target = plan.output / rel
            if not target.resolve().is_relative_to(plan.output) or (target.exists() and not target.is_file()):
                raise BuildError('客户端输出位置发生变化：' + rel)
            for parent in target.parents:
                if parent == plan.output:
                    break
                if parent.exists() and not parent.is_dir():
                    raise BuildError('客户端输出父路径不是目录：' + str(parent))
        for rel, _ in plan.files:
            check_cancel(cancelled)
            target = plan.output / rel
            missing = []
            parent = target.parent
            while parent != plan.output and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(reversed(missing))
            if target.exists():
                saved = backup / rel
                saved.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, saved)
                backups.append((target, saved))
            os.replace(stage / rel, target)
            committed.append(target)
    except Exception as error:
        rollback_errors = []
        for target in reversed(committed):
            try:
                target.unlink(missing_ok=True)
            except OSError as failure:
                rollback_errors.append(str(failure))
        for target, saved in reversed(backups):
            try:
                os.replace(saved, target)
            except OSError as failure:
                rollback_errors.append(str(failure))
        for folder in reversed(created_dirs):
            try:
                folder.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise BuildError(f'客户端提交失败，回滚不完整；请保留恢复目录 {backup.parent}。{rollback_errors}') from error
        raise


def export_client(plan, options=ProtectionOptions(), progress=lambda *_: None, cancelled=None):
    """Stage every Lua first; rollback existing files if promotion fails."""
    started = time.perf_counter()
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.ws-client-build-', dir=plan.output.parent))
    stage, backup = temporary / 'contents', temporary / 'backup'
    result = BuildResult(str(plan.output), len(plan.files), len(plan.files))
    keep_recovery = False
    try:
        for index, (rel, source) in enumerate(plan.files, 1):
            check_cancel(cancelled)
            try:
                raw, stamp = _read_snapshot(source)
                anchor = next((p for p in source.parents if p.name.casefold() == 'guilayout'), None)
                if anchor is not None:
                    logical = source.relative_to(anchor).as_posix()
                else:
                    logical = (plan.source.name + '/' + rel) if plan.source.is_dir() else (source.parent.name + '/' + rel)
                output, report = _processed(raw, rel, options, 'utf-8', '@' + logical)
                target = stage / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(output)
                os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                result.reports.append(report)
                progress(index, len(plan.files), '客户端处理：' + rel)
            except Exception as error:
                raise BuildError(f'{rel}：客户端保护阶段失败：{error}') from error
        check_cancel(cancelled)
        plan.output.mkdir(parents=True, exist_ok=True)
        try:
            _promote_client(stage, backup, plan, cancelled)
        except BuildError as error:
            keep_recovery = '回滚不完整' in str(error)
            raise
        result.elapsed_seconds = time.perf_counter() - started
        return result
    finally:
        if not keep_recovery:
            shutil.rmtree(temporary, ignore_errors=True)
