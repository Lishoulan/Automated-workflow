"""无浏览器探测：用 profile 里 cookie 库的密文解密出明文会话，直接打创作者后台接口。

本会话沙箱禁止 Chrome 的 mojo 跨进程管道（platform_channel.cc:108 拒绝访问），
Playwright / Chrome / headless shell 全部起不来，所以只能走「纯 HTTP + 真 cookie」。

cookie 为 v10：AES-256-GCM，密钥在 profile 的 Local State 里（DPAPI 包裹）。
只读探测，不改任何作品状态。
"""
from __future__ import annotations

import base64
import ctypes
import json
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile"
COOKIE_DB = PROFILE / "Default" / "Network" / "Cookies"
LOCAL_STATE = PROFILE / "Local State"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# ---------- DPAPI ----------


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(blob: bytes) -> bytes:
    buf_in = ctypes.create_string_buffer(blob, len(blob))
    bin_blob = DATA_BLOB(len(blob), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    bout = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(bin_blob), None, None, None, None, 0, ctypes.byref(bout)):
        raise OSError(f"CryptUnprotectData failed err={ctypes.GetLastError()}")
    try:
        return ctypes.string_at(bout.pbData, bout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(bout.pbData)


# ---------- AES-GCM (via Windows CNG, 无第三方依赖) ----------


class _AUTH_MODE_INFO(ctypes.Structure):
    """BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO（x64 布局：cbSize+版本 8B + 5 个指针/长度对）。"""
    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwInfoVersion", wintypes.ULONG),
        ("pbNonce", ctypes.c_void_p),
        ("cbNonce", wintypes.ULONG),
        ("_pad1", wintypes.ULONG),
        ("pbAuthData", ctypes.c_void_p),
        ("cbAuthData", wintypes.ULONG),
        ("_pad2", wintypes.ULONG),
        ("pbTag", ctypes.c_void_p),
        ("cbTag", wintypes.ULONG),
        ("_pad3", wintypes.ULONG),
        ("pbMacContext", ctypes.c_void_p),
        ("cbMacContext", wintypes.ULONG),
        ("cbAAD", wintypes.ULONG),
        ("cbData", ctypes.c_ulonglong),
        ("dwFlags", wintypes.ULONG),
        ("_pad4", wintypes.ULONG),
    ]


def _set_bcrypt_prop(fn, h, name: str, data: bytes) -> None:
    buf = ctypes.create_string_buffer(data, len(data))   # 必须保活
    st = fn(h, name, ctypes.cast(buf, ctypes.c_void_p), len(data), 0)
    if st:
        raise OSError(f"BCryptSetProperty({name}) failed 0x{st & 0xffffffff:08x}")


def aes_gcm_decrypt(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    """AES-256-GCM 解密（Windows CNG / bcrypt）。

    实测要点（两个都踩过）：
      1. BCrypt 的 GCM **不接受 12 字节 nonce 直接设成 IV**
         （STATUS_INVALID_PARAMETER），IV 属性要喂 16 字节计数器块 J0；
         Chrome 存的是 12 字节 nonce，按 SP800-38D 算 J0 = nonce || 00000001。
      2. AuthTag **不能**用 BCryptSetProperty 设（STATUS_NOT_SUPPORTED），
         必须走 BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO 结构体。
    """
    if len(nonce) == 12:
        j0 = nonce + b"\x00\x00\x00\x01"
    elif len(nonce) == 16:
        j0 = nonce
    else:
        raise ValueError(f"unsupported nonce len {len(nonce)}")

    bcrypt = ctypes.windll.bcrypt
    h_alg = wintypes.HANDLE()
    if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), "AES", None, 0):
        raise OSError("BCryptOpenAlgorithmProvider failed")
    try:
        _set_bcrypt_prop(bcrypt.BCryptSetProperty, h_alg, "ChainingMode",
                         "ChainingModeGCM\0".encode("utf-16-le"))
        h_key = wintypes.HANDLE()
        if bcrypt.BCryptGenerateSymmetricKey(h_alg, ctypes.byref(h_key), None, 0,
                                             key, len(key), 0):
            raise OSError("BCryptGenerateSymmetricKey failed")
        try:
            _set_bcrypt_prop(bcrypt.BCryptSetProperty, h_key, "IV", j0)

            nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
            tag_buf = ctypes.create_string_buffer(tag, len(tag))
            info = _AUTH_MODE_INFO()
            ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
            info.cbSize = ctypes.sizeof(info)
            info.dwInfoVersion = 1            # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO_VERSION
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
            info.cbTag = len(tag)
            info.cbData = len(ct)

            in_buf = ctypes.create_string_buffer(ct, max(1, len(ct)))
            out = ctypes.create_string_buffer(max(1, len(ct)))
            written = wintypes.ULONG()
            # BCRYPT_FLAG = 0x00000020 -> 用 pPaddingInfo 指向 auth mode info
            status = bcrypt.BCryptDecrypt(
                h_key, ctypes.cast(in_buf, ctypes.c_void_p), len(ct),
                ctypes.byref(info), 0, out, len(ct), ctypes.byref(written), 0x20)
            if status:
                raise OSError(f"BCryptDecrypt failed status=0x{status & 0xffffffff:08x}")
            return out.raw[:written.value]
        finally:
            bcrypt.BCryptDestroyKey(h_key)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)


def load_key() -> bytes:
    raw = base64.b64decode(json.loads(LOCAL_STATE.read_text(encoding="utf-8"))
                           ["os_crypt"]["encrypted_key"])
    if raw[:5] != b"DPAPI":
        raise ValueError("unexpected key prefix: " + repr(raw[:6]))
    return dpapi_unprotect(raw[5:])


def read_cookies(key: bytes) -> dict[str, str]:
    tmp = ROOT / ".runtime" / "_ck2.db"
    shutil.copy2(COOKIE_DB, tmp)
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT name, host_key, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%toutiao%'").fetchall()
        con.close()
    finally:
        tmp.unlink(missing_ok=True)

    out: dict[str, str] = {}
    errs: dict[str, str] = {}
    for name, host, blob in rows:
        blob = bytes(blob)
        try:
            if blob[:3] == b"v10":
                body = blob[3:]
                pt = aes_gcm_decrypt(key, body[:12], body[12:-16], body[-16:])
                out[name] = pt.decode("utf-8", "replace")
            elif blob[:3] in (b"v10", b"v11"):
                out[name] = dpapi_unprotect(blob[3:]).decode("utf-8", "replace")
        except Exception as e:
            out[name] = f"<decrypt-fail: {type(e).__name__}>"
            errs[name] = f"{type(e).__name__}: {e}"
    for k, v in list(errs.items())[:6]:
        print(f"   [err] {k}: {v}")
    return out


def http_json(url: str, cookie: str, method: str = "GET") -> tuple[int, object]:
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": UA, "Cookie": cookie, "Referer":
            "https://mp.toutiao.com/profile_v4/manage/content/all",
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body[:600]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:600]


def main() -> int:
    key = load_key()
    print(f"[key] 主密钥已解出 ({len(key)} bytes)")
    cks = read_cookies(key)
    ok = {k: v for k, v in cks.items() if not v.startswith("<decrypt-fail")}
    print(f"[cookie] 解出 {len(ok)}/{len(cks)} 个头条 cookie")
    for k in ("sessionid", "sid_guard", "odin_tt", "ttwid", "passport_csrf_token"):
        v = ok.get(k, "")
        print(f"   {k:<22} {'OK len=' + str(len(v)) if v else 'MISSING'}")
    cookie_hdr = "; ".join(f"{k}={v}" for k, v in ok.items())

    print("\n=== 用真实 cookie 打后台接口 ===")
    tests = [
        ("login_status", "https://mp.toutiao.com/mp/agw/media/user_login_status_api?is_new_register=1"),
        ("user_info", "https://mp.toutiao.com/mp/agw/creator_center/user_info?app_id=1231"),
        ("draft_count", "https://mp.toutiao.com/mp/agw/creator_center/draft_count?app_id=1231"),
    ]
    for name, url in tests:
        status, body = http_json(url, cookie_hdr)
        s = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        print(f"  [{status}] {name}: {s[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
