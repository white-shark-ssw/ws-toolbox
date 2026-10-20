"""ChaCha20 and standalone loader builder adapted from the tested handoff.

Comment stripping/obfuscation are owned by processor.py, not these primitives.
The generated Lua includes its key; Adler-32 is only a corruption check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import tempfile
import textwrap

CRYPTO_PATH = Path(__file__).resolve().parent / "chacha20_lua51.lua"

def chacha20(data, key, nonce, counter=1):
    if len(key) != 32 or len(nonce) != 12:
        raise ValueError("ChaCha20 requires a 32-byte key and 12-byte nonce")
    if not isinstance(counter, int) or counter < 0 or counter >= 2 ** 32:
        raise ValueError("invalid counter")
    if (len(data) + 63) // 64 > 2 ** 32 - counter:
        raise ValueError("counter would wrap")
    def rot(n, shift):
        return ((n << shift) | (n >> (32 - shift))) & 0xffffffff

    def quarter(s, a, b, c, d):
        s[a] = (s[a] + s[b]) & 0xffffffff
        s[d] = rot(s[d] ^ s[a], 16)
        s[c] = (s[c] + s[d]) & 0xffffffff
        s[b] = rot(s[b] ^ s[c], 12)
        s[a] = (s[a] + s[b]) & 0xffffffff
        s[d] = rot(s[d] ^ s[a], 8)
        s[c] = (s[c] + s[d]) & 0xffffffff
        s[b] = rot(s[b] ^ s[c], 7)

    result = bytearray()
    for offset in range(0, len(data), 64):
        state = list(struct.unpack('<4I', b'expand 32-byte k'))
        state += list(struct.unpack('<8I', key))
        state += [counter] + list(struct.unpack('<3I', nonce))
        working = state[:]
        for _ in range(10):
            for indices in ((0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14),
                            (3, 7, 11, 15), (0, 5, 10, 15), (1, 6, 11, 12),
                            (2, 7, 8, 13), (3, 4, 9, 14)):
                quarter(working, *indices)
        stream = struct.pack('<16I', *[(x + y) & 0xffffffff for x, y in zip(working, state)])
        result.extend(a ^ b for a, b in zip(data[offset:offset + 64], stream))
        counter = (counter + 1) & 0xffffffff
    return bytes(result)

def adler32(data):
    a, b = 1, 0
    for byte in data:
        a = (a + byte) % 65521
        b = (b + a) % 65521
    return b * 65536 + a

def lua_bytes(data):
    return '"' + ''.join('\\%03d' % byte for byte in data) + '"'

def make_wrapper(source, fallback, key, nonce):
    """Build a source loader; caller-owned transformations happen separately."""
    crypto = CRYPTO_PATH.read_text("ascii")
    ciphertext = chacha20(source, key, nonce)
    if chacha20(ciphertext, key, nonce) != source:
        raise ValueError("Encryption roundtrip verification failed")
    header = '''-- WS-LUA-PROTECTED source-chacha20-v1. Requires Lua 5.1 or LuaJIT.
-- Decryption happens once per script load. The key is bundled in this file.
-- This layer raises copying effort; it does not keep code secret from the host.
local __env = getfenv(1)
local __source_name = FALLBACK
if debug and type(debug.getinfo) == "function" then
    local info = debug.getinfo(1, "S")
    if info and type(info.source) == "string" and info.source:sub(1, 1) == "@" then
        __source_name = info.source
    end
end
local __chunk = (function()
local ChaCha20 = (function()
'''.replace('FALLBACK', lua_bytes(fallback))
    suffix = '''
end)()
local function from_hex(hex)
    hex = hex:gsub("%s", "")
    if #hex % 2 ~= 0 or hex:find("[^0-9a-fA-F]") then
        error("Encrypted script: invalid payload encoding", 0)
    end
    return (hex:gsub("%x%x", function(pair) return string.char(tonumber(pair, 16)) end))
end
local key = from_hex("KEY")
local nonce = from_hex("NONCE")
local ciphertext = from_hex([=[
PAYLOAD
]=])
local source = ChaCha20.new(key, nonce, 1):apply_keystream(ciphertext)
-- Accidental-corruption check only; this is not cryptographic authentication.
local a, b = 1, 0
for i = 1, #source do
    a = (a + source:byte(i)) % 65521
    b = (b + a) % 65521
end
if #source ~= LENGTH or b * 65536 + a ~= CHECKSUM then
    error("Encrypted script: payload checksum mismatch", 0)
end
local chunk, err = loadstring(source, __source_name)
if not chunk then error(err, 0) end
setfenv(chunk, __env)
return chunk
end)()
return __chunk(...)
'''
    suffix = suffix.replace('KEY', key.hex()).replace('NONCE', nonce.hex())
    suffix = suffix.replace('PAYLOAD', '\n'.join(textwrap.wrap(ciphertext.hex(), 96)))
    suffix = suffix.replace('LENGTH', str(len(source))).replace('CHECKSUM', str(adler32(source)))
    return (header + crypto + suffix).encode('ascii')

def validate_source(source, encoding):
    source.decode(encoding, errors="strict")
    if source.startswith((b"\x1bLua", b"\x1bLJ")):
        raise ValueError("This reference accepts text source, not precompiled bytecode")
    if source.startswith(b"\xef\xbb\xbf") or source.startswith(b"#!"):
        raise ValueError("BOM/shebang input needs explicit target-loader handling; not implemented in this reference")
    if source.startswith((b"-- Encrypted source compatibility test.", b"-- WS-LUA-PROTECTED")):
        raise ValueError("Input is already a known encrypted reference wrapper")


def encrypt_source(source, chunk_name):
    """Encrypt bytes already processed by the caller; return one self-contained ASCII Lua file."""
    if not isinstance(source, bytes) or not isinstance(chunk_name, bytes):
        raise TypeError("source and chunk_name must be bytes")
    if not chunk_name.startswith(b"@") or b"\0" in chunk_name:
        raise ValueError("chunk_name must be an @-prefixed logical filename without NUL")
    return make_wrapper(source, chunk_name, secrets.token_bytes(32), secrets.token_bytes(12))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".ws-lua-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def same_file(left, right):
    if Path(left).resolve() == Path(right).resolve():
        return True
    if Path(left).exists() and Path(right).exists():
        return os.path.samefile(left, right)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-encoding", required=True, choices=("gbk", "utf-8"))
    parser.add_argument("--chunk-name", required=True)
    parser.add_argument("--manifest", type=Path, help="Optional local metadata file; contains no keys")
    arguments = parser.parse_args()
    try:
        if same_file(arguments.input, arguments.output):
            raise ValueError("Output must not overwrite the input source")
        if arguments.manifest and (same_file(arguments.manifest, arguments.input) or same_file(arguments.manifest, arguments.output)):
            raise ValueError("Manifest path must differ from source and output")
        source = arguments.input.read_bytes()
        validate_source(source, arguments.source_encoding)
        chunk_name = arguments.chunk_name.encode(arguments.source_encoding, errors="strict")
        wrapper = encrypt_source(source, chunk_name)
        metadata = {
            "reference_format": "source-chacha20-v1",
            "payload_kind": "source", "source_encoding": arguments.source_encoding,
            "chunk_name": arguments.chunk_name, "source_size": len(source),
            "release_size": len(wrapper),
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "release_sha256": hashlib.sha256(wrapper).hexdigest(),
            "comment_stripping": False, "obfuscation": False,
        }
        atomic_write(arguments.output, wrapper)
        if arguments.manifest:
            atomic_write(arguments.manifest, (json.dumps(metadata, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, "error: "+str(error)+"\n")


if __name__ == "__main__":
    main()
