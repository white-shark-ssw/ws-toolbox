"""One shared bytes-in/bytes-out protection service for both application paths."""
from dataclasses import dataclass
import hashlib
import threading

from ..lua_syntax import RULE_VERSION, transform

LOADER_VERSION = 'source-chacha20-v1'
_runtime = threading.local()


class ProtectionError(ValueError):
    pass


@dataclass(frozen=True)
class ProtectionOptions:
    encrypt: bool = False
    obfuscate: bool = False

    @property
    def enabled(self):
        return self.encrypt or self.obfuscate


@dataclass
class ProtectionResult:
    output: bytes
    processed: bytes
    report: dict
    private_mapping: list


def compile_check(raw, name):
    """Compile only, never run untrusted/user business code during a build."""
    if not hasattr(_runtime, 'compiler'):
        try:
            from lupa.lua51 import LuaRuntime
        except ImportError as error:
            raise ProtectionError('Lua保护校验需要 Lupa：Windows 请先运行“安装保护依赖.bat”再用“启动工具.bat”启动；其他环境请执行 python -m pip install -r requirements.txt') from error
        _runtime.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        _runtime.compiler = _runtime.lua.eval(b'function(s,n) local f,e=loadstring(s,n); return f~=nil,e end')
    valid, error = _runtime.compiler(raw, name)
    if not valid:
        raise ProtectionError('Lua 5.1语法检查失败：' + error.decode('utf-8', errors='backslashreplace'))


def protect(raw, options, encoding, chunk_name):
    """Preserve source bytes when off; otherwise strip, bind-rename and encrypt.

    The caller supplies the source-side encoding (server GBK/client UTF-8) and
    logical source name. No source files, caches or logs are written here.
    """
    if not isinstance(raw, bytes):
        raise TypeError('Lua input must be bytes')
    if not options.enabled:
        return ProtectionResult(raw, raw, {'status': 'unchanged'}, [])
    if raw.startswith((b'\x1bLua', b'\x1bLJ', b'\xef\xbb\xbf', b'#!')):
        raise ProtectionError('不支持的Lua输入类型：字节码、BOM或shebang；请使用原始无BOM源码')
    if raw.startswith((b'-- WS-LUA-PROTECTED', b'-- Encrypted source compatibility test.')):
        raise ProtectionError('输入已经是保护后的Lua，请使用原始源码')
    try:
        source = raw.decode(encoding, errors='strict')
        name = chunk_name.encode(encoding, errors='strict')
        if source.encode(encoding, errors='strict') != raw:
            raise ProtectionError('源文件编码往返检查失败')
        compile_check(raw, name)
        processed, mapping = transform(source, strip_comments=True, obfuscate=options.obfuscate)
        payload = processed.encode(encoding, errors='strict')
        compile_check(payload, name)
        output = payload
        loader_mapping = []
        if options.encrypt:
            from .crypto import encrypt_source
            output = encrypt_source(payload, name)
            if options.obfuscate:
                # Keep the third-party license in the loader; input comments are
                # already removed from the separate business chunk.
                wrapper, loader_mapping = transform(output.decode('ascii'), strip_comments=False, obfuscate=True)
                output = wrapper.encode('ascii')
            compile_check(output, name)
        report = {
            'status': 'protected', 'encrypt': options.encrypt, 'obfuscate': options.obfuscate,
            'encoding': encoding, 'source_bytes': len(raw), 'processed_bytes': len(payload),
            'output_bytes': len(output), 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'processed_sha256': hashlib.sha256(payload).hexdigest(),
            'output_sha256': hashlib.sha256(output).hexdigest(),
            'renamed_bindings': len(mapping), 'rule_version': RULE_VERSION,
            'loader_renamed_bindings': len(loader_mapping),
            'loader_version': LOADER_VERSION if options.encrypt else None,
        }
        return ProtectionResult(output, payload, report, mapping)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ProtectionError):
            raise
        raise ProtectionError(str(error)) from error
