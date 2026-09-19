"""定位 CNG(BCrypt) AES-GCM 调用失败点：逐个属性设置做诊断。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

bcrypt = ctypes.windll.bcrypt
bcrypt.BCryptOpenAlgorithmProvider.argtypes = [
    ctypes.POINTER(wintypes.HANDLE), ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.ULONG]
bcrypt.BCryptSetProperty.argtypes = [
    wintypes.HANDLE, ctypes.c_wchar_p, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]
bcrypt.BCryptSetProperty.restype = ctypes.c_long


def try_set(h, name, data: bytes, label):
    buf = ctypes.create_string_buffer(data, len(data))   # 保活
    st = bcrypt.BCryptSetProperty(h, name, ctypes.cast(buf, ctypes.c_void_p),
                                  len(data), 0)
    print(f"   {label:<28} status=0x{st & 0xffffffff:08x} ({'ok' if st == 0 else 'FAIL'})")
    return st


def main() -> int:
    h = wintypes.HANDLE()
    st = bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h), "AES", None, 0)
    print(f"OpenAlgorithmProvider AES: 0x{st & 0xffffffff:08x}")
    if st:
        return 1

    print("\n-- 链模式 --")
    for mode in ("ChainingModeGCM", "ChainingModeCBC"):
        try_set(h, "ChainingMode", (mode + "\0").encode("utf-16-le"), mode)

    print("\n-- AuthTag 长度（GCM 需要在 alg 句柄上设）--")
    for size in (16,):
        try_set(h, "AuthTagLength", (size).to_bytes(4, "little"), f"AuthTagLength={size}")

    print("\n-- 生成密钥 --")
    key = b"0123456789abcdef0123456789abcdef"
    hk = wintypes.HANDLE()
    st = bcrypt.BCryptGenerateSymmetricKey(h, ctypes.byref(hk), None, 0, key, len(key), 0)
    print(f"   GenerateSymmetricKey: 0x{st & 0xffffffff:08x}")

    print("\n-- 在密钥句柄上设 IV / AuthTag --")
    nonce = bytes(range(12))
    tag = bytes(range(16))
    ctypes.windll.bcrypt.BCryptSetProperty.restype = ctypes.c_long
    for size in (12, 16):
        try_set(hk, "IV", nonce[:size] if size == 12 else tag, f"IV len={size}")
    try_set(hk, "AuthTag", tag, "AuthTag len=16")

    print("\n-- 用 BCryptEncrypt/Decrypt + GCM 参数结构 的真实调用（对照）--")
    bcrypt.BCryptDestroyKey(hk)
    bcrypt.BCryptCloseAlgorithmProvider(h, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
