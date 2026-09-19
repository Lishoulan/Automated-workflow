"""CNG AES-GCM 解密最小验证 + 头条 sessionid 解密。

验证方式：用 CNG 自己加密再自己解密，确认调用序列正确，
再应用到 Chrome 的 v10 cookie 密文。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

bcrypt = ctypes.windll.bcrypt


class AUTH(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.ULONG), ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.c_void_p), ("cbNonce", wintypes.ULONG), ("_p1", wintypes.ULONG),
        ("pbAuthData", ctypes.c_void_p), ("cbAuthData", wintypes.ULONG), ("_p2", wintypes.ULONG),
        ("pbTag", ctypes.c_void_p), ("cbTag", wintypes.ULONG), ("_p3", wintypes.ULONG),
        ("pbMacContext", ctypes.c_void_p), ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG), ("cbData", ctypes.c_ulonglong),
        ("dwFlags", wintypes.ULONG), ("_p4", wintypes.ULONG),
    ]


def setprop(h, name, data):
    buf = ctypes.create_string_buffer(data, len(data))
    return bcrypt.BCryptSetProperty(h, name, ctypes.cast(buf, ctypes.c_void_p), len(data), 0)


def open_gcm_key(key: bytes, mode_where: str):
    h_alg = wintypes.HANDLE()
    assert bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0) == 0
    if mode_where == "alg":
        assert setprop(h_alg, "ChainingMode", "ChainingModeGCM\0".encode("utf-16-le")) == 0
    h_key = wintypes.HANDLE()
    st = bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0, key, len(key), 0)
    assert st == 0, f"genkey 0x{st & 0xffffffff:08x}"
    if mode_where == "key":
        st = setprop(h_key, "ChainingMode", "ChainingModeGCM\0".encode("utf-16-le"))
        print(f"    set ChainingMode on KEY -> 0x{st & 0xffffffff:08x}")
    return h_alg, h_key


def roundtrip(mode_where: str, iv_bytes: bytes) -> None:
    key = bytes(range(32))
    nonce = bytes(range(12))
    pt = b"hello-chrome-cookie-value"
    h_alg, h_key = open_gcm_key(key, mode_where)

    tag = ctypes.create_string_buffer(16)
    info = AUTH()
    ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
    info.cbSize = ctypes.sizeof(info)
    info.dwInfoVersion = 1
    nb = ctypes.create_string_buffer(nonce, 12)
    info.pbNonce = ctypes.cast(nb, ctypes.c_void_p)
    info.cbNonce = 12
    info.pbTag = ctypes.cast(tag, ctypes.c_void_p)
    info.cbTag = 16
    info.cbData = len(pt)

    st_iv = setprop(h_key, "IV", iv_bytes)
    inb = ctypes.create_string_buffer(pt, len(pt))
    ct = ctypes.create_string_buffer(len(pt))
    wn = wintypes.ULONG()
    st = bcrypt.BCryptEncrypt(h_key, ctypes.cast(inb, ctypes.c_void_p), len(pt),
                              ctypes.byref(info), None, 0, ct, len(pt), ctypes.byref(wn), 0x20)
    print(f"  [{mode_where}/{len(iv_bytes)}B-IV] setIV=0x{st_iv & 0xffffffff:08x} "
          f"encrypt=0x{st & 0xffffffff:08x} written={wn.value}")
    if st == 0:
        print(f"     tag={tag.raw.hex()[:24]}... ct={ct.raw[:12].hex()}...")
    bcrypt.BCryptDestroyKey(h_key)
    bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def main() -> int:
    print("=== CNG GCM 自检 ===")
    roundtrip("alg", bytes(range(12)) + b"\x00\x00\x00\x01")
    roundtrip("key", bytes(range(12)) + b"\x00\x00\x00\x01")
    roundtrip("alg", bytes(range(16)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
