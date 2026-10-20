-- Self-contained ChaCha20 module adapted for the Lua 5.1 source loader.
-- Based on BernhardZat/pure-lua-5.1-crypto, MIT, copyright 2022-2026 BernhardZat.
-- Upstream revision: 62d20e1fc5126ecd0dbb9a805e3ae8057ba8c025
-- ChaCha20 modules and misc helpers are bundled; bit helpers are replaced
-- by the minimal arithmetic-only operations below. No C module is required.
-- This module supplies decryption; it does not authenticate the payload.
--[[
MIT License

Copyright (c) 2022-2026 BernhardZat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

]]
local factories, loaded = {}, {}
local function require(name)
    if loaded[name] ~= nil then return loaded[name] end
    local factory = assert(factories[name], "Missing bundled module: " .. name)
    local result = factory()
    loaded[name] = result
    return result
end

factories["util.bitops.bitops"] = function()
-- Minimal unsigned operations needed by the bundled ChaCha20 implementation.
local floor = math.floor
local MOD = 4294967296
-- Reuse an engine-provided bit library when available; never require a DLL.
-- The arithmetic fallback remains available in a plain Lua 5.1 environment.
local native = bit or bit32
if type(native) ~= "table" then native = nil end
local native_xor = native and native.bxor
local native_rot = native and (native.rol or native.lrotate)
local fast = type(native_xor) == "function" and type(native_rot) == "function"
local nibble_xor = {}
if not fast then
    for a = 0, 15 do
        local row = {}
        nibble_xor[a] = row
        for b = 0, 15 do
            local x, y, result, place = a, b, 0, 1
            for _ = 1, 4 do
                if x % 2 ~= y % 2 then result = result + place end
                x, y, place = floor(x / 2), floor(y / 2), place * 2
            end
            row[b] = result
        end
    end
end
local function u32_xor(a, b)
    if fast then return native_xor(a, b) % MOD end
    a, b = a % MOD, b % MOD
    local result, place = 0, 1
    for _ = 1, 8 do
        result = result + nibble_xor[a % 16][b % 16] * place
        a, b, place = floor(a / 16), floor(b / 16), place * 16
    end
    return result
end
local function u32_lrot(a, n)
    if fast then return native_rot(a, n) % MOD end
    n = n % 32
    return (a % 2 ^ (32 - n)) * 2 ^ n + floor(a / 2 ^ (32 - n))
end
return {
    u32_xor = u32_xor,
    u32_lrot = u32_lrot,
    u32_add = function(a, b) return (a + b) % MOD end,
}

end

factories["util.misc"] = function()
-- Miscellaneous helper functions

local function u32_to_le_bytes(n)
    return string.char(
        n % 256,
        math.floor(n / 256) % 256,
        math.floor(n / 65536) % 256,
        math.floor(n / 16777216) % 256
    );
end

local function u32_from_le_bytes(s)
    local b1, b2, b3, b4 = s:byte(1, 4);
    return b1 + b2 * 0x100 + b3 * 0x10000 + b4 * 0x1000000;
end

local function hex_from_bytestring(s)
    return (s:gsub(".", function(c)
        return string.format("%02x", c:byte());
    end));
end

local function hex_to_bytestring(hex)
    return (hex:gsub("%x%x", function(cc)
        return string.char(tonumber(cc, 16));
    end));
end

local function assert_equal(label, input, expect)
    if input ~= expect then
        error(string.format("Test failed: %s\nExpected: %s\nGot:      %s", label, expect, input));
    else
        print("OK:", label);
    end
end

local function assert_not_equal(label, a, b)
    if a == b then
        error(string.format("Test failed: %s\nExpected not equal: %s", label, a));
    else
        print("OK:", label);
    end
end

local function assert_error(label, fn)
    local ok = pcall(fn);
    if ok then
        error(string.format("Test failed: %s\nExpected error.", label));
    else
        print("OK:", label);
    end
end

local misc = {
    u32_to_le_bytes = u32_to_le_bytes,
    u32_from_le_bytes = u32_from_le_bytes,
    hex_from_bytestring = hex_from_bytestring,
    hex_to_bytestring = hex_to_bytestring,
    assert_equal = assert_equal,
    assert_not_equal = assert_not_equal,
    assert_error = assert_error,
};

return misc;

end

factories["crypto.chacha20.chacha20core"] = function()
local bitops = require("util.bitops.bitops")
local u32_xor  = bitops.u32_xor
local u32_lrot = bitops.u32_lrot
local u32_add  = bitops.u32_add

local misc = require("util.misc");
local u32_to_le_bytes = misc.u32_to_le_bytes;
local u32_from_le_bytes = misc.u32_from_le_bytes;

-- Convert a byte string into an array of 32?bit little?endian integers.
local function unpack_u32_le(s)
    local out = {};
    for i = 1, #s, 4 do
        out[#out+1] = u32_from_le_bytes(s:sub(i, i+3));
    end
    return out;
end

-- Convert an array of 32?bit integers into a litte-endian byte string.
local function pack_u32_le(a)
    local out = {};
    for i = 1, #a do
        out[i] = u32_to_le_bytes(a[i]);
    end
    return table.concat(out);
end

-- Perform one ChaCha20 quarter?round on state indices (a, b, c, d).
local function quarter_round(s, a, b, c, d)
    s[a] = u32_add(s[a], s[b]);
    s[d] = u32_lrot(u32_xor(s[d], s[a]), 16);
    s[c] = u32_add(s[c], s[d]);
    s[b] = u32_lrot(u32_xor(s[b], s[c]), 12);
    s[a] = u32_add(s[a], s[b]);
    s[d] = u32_lrot(u32_xor(s[d], s[a]),  8);
    s[c] = u32_add(s[c], s[d]);
    s[b] = u32_lrot(u32_xor(s[b], s[c]),  7);
end

-- Perform 20 rounds = 10 column rounds + 10 diagonal rounds
local function chacha20_rounds(state)
    for _ = 1, 10 do
        -- Column round
        quarter_round(state, 1, 5,  9, 13);
        quarter_round(state, 2, 6, 10, 14);
        quarter_round(state, 3, 7, 11, 15);
        quarter_round(state, 4, 8, 12, 16);
        -- Diagonal round
        quarter_round(state, 1, 6, 11, 16);
        quarter_round(state, 2, 7, 12, 13);
        quarter_round(state, 3, 8,  9, 14);
        quarter_round(state, 4, 5, 10, 15);
    end
end

-- Produce one 64?byte ChaCha20 block.
local function chacha20_block(key, nonce, counter)
    -- Initial ChaCha20 state
    local state = {
        0x61707865, 0x3320646e, 0x79622d32, 0x6b206574,
        key[1],     key[2],     key[3],     key[4],
        key[5],     key[6],     key[7],     key[8],
        counter,    nonce[1],   nonce[2],   nonce[3],
    };

    -- Working copy for the 20 rounds
    local working = {};
    for i = 1, 16 do working[i] = state[i]; end

    -- 20 rounds
    chacha20_rounds(working);

    -- Add original state
    for i = 1, 16 do
        working[i] = u32_add(working[i], state[i]);
    end

    -- Return 64?byte keystream block
    return pack_u32_le(working);
end

-- Produce a 32?byte HChaCha20 block.
local function hchacha20(key, nonce)
    -- Initial HChaCha20 state with 4 nonce words and no counter
    local state = {
        0x61707865, 0x3320646e, 0x79622d32, 0x6b206574,
        key[1],     key[2],     key[3],     key[4],
        key[5],     key[6],     key[7],     key[8],
        nonce[1],   nonce[2],   nonce[3],   nonce[4],
    };

    -- Working copy for the 20 rounds
    local working = {};
    for i = 1, 16 do working[i] = state[i]; end

    -- 20 rounds
    chacha20_rounds(working);

    -- 32-byte subkey = state[0..3] and state[12..15]
    local out_words = {
        working[1], working[2], working[3], working[4],
        working[13], working[14], working[15], working[16],
    };

    -- Return 32?byte subkey
    return pack_u32_le(out_words);
end

-- Generate the next 64?byte keystream block and increment counter.
local function generate_block(self)
    self.keystream = chacha20_block(self.key, self.nonce, self.counter);
    self.counter = (self.counter + 1) % 0x100000000;
end

local chacha20core = {
    unpack_u32_le = unpack_u32_le,
    pack_u32_le = pack_u32_le,
    chacha20_block = chacha20_block,
    hchacha20 = hchacha20;
    generate_block = generate_block,
};

return chacha20core;
end

factories["crypto.chacha20.chacha20"] = function()
local bitops = require("util.bitops.bitops")
local chacha20core = require("crypto.chacha20.chacha20core");
local floor = math.floor;

-- ChaCha20 stream cipher object
local ChaCha20 = {};
ChaCha20.__index = ChaCha20;

-- Create a new ChaCha20 stream cipher instance.
function ChaCha20.new(key, nonce, counter)
    assert(#key == 32, "ChaCha20 key must be 32 bytes");
    assert(#nonce == 12, "ChaCha20 nonce must be 12 bytes");

    local self = setmetatable({}, ChaCha20);

    -- Convert key and nonce into u32 arrays
    self.key   = chacha20core.unpack_u32_le(key);
    self.nonce = chacha20core.unpack_u32_le(nonce);

    -- Stream position
    self.counter = counter or 0;    -- block index
    self.offset  = 0;               -- byte offset within block (0?63)
    self.keystream = "";            -- cached 64?byte block

    return self;
end

-- XOR the keystream with the given data and return the result.
function ChaCha20:apply_keystream(data)
    local out = {};
    local out_len = 0;

    -- Generate a new block if we are at the start of a block or the keystream is empty
    for i = 1, #data do
        if self.offset == 0 or self.keystream == "" then
            chacha20core.generate_block(self);
        end

         -- XOR plaintext byte with keystream byte
        local ks_byte = self.keystream:byte(self.offset + 1);
        local pt_byte = data:byte(i);
        out_len = out_len + 1;
        out[out_len] = string.char(bitops.u32_xor(pt_byte, ks_byte));

        -- Advance within the 64?byte block
        self.offset = (self.offset + 1) % 64;
    end

    return table.concat(out);
end

-- Seek to an absolute byte offset in the ChaCha20 stream.
function ChaCha20:seek(byte_offset)
    self.counter = floor(byte_offset / 64);     -- block index
    self.offset  = byte_offset % 64;            -- byte within block
    self.keystream = "";                        -- force regeneration
    return self;
end

return ChaCha20;

end

return require("crypto.chacha20.chacha20")

