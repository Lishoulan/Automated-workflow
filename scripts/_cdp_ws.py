"""极简 CDP 客户端（纯 socket，不用 Playwright）。

为什么需要它：本会话的沙箱禁止创建/打开**命名管道**，
而 Playwright 的 driver 是子进程 + stdio 管道通信（windows_utils.pipe -> CreateFile
被拒：[WinError 5] 拒绝访问），所以 Playwright 在此环境下**永远无法启动**。
CDP over TCP/WebSocket 只用 socket，不受该限制。

用法：
    from _cdp_ws import CDP, https_get_json
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.request

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def http_get_json(url: str, timeout: float = 15.0) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    """一条 CDP WebSocket 连接。线程不安全，单线程顺序调用。"""

    def __init__(self, ws_url: str, timeout: float = 30.0):
        from urllib.parse import urlparse

        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        self.sock.settimeout(0.5)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {u.path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        # 读完 HTTP 响应头（WS 帧可能紧跟其后，剩余字节要留着）
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WS handshake closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise ConnectionError("WS handshake failed: " + head.decode("latin1", "replace"))
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if expect.lower().encode() not in head.lower():
            raise ConnectionError("WS accept mismatch")
        self._left = rest
        self._id = 0
        self._buf = b""          # 分片累积
        self._events: list[dict] = []
        self._stash: list[bytes] = []

    # ---------- 帧 ----------
    def _recv_more(self) -> None:
        try:
            data = self.sock.recv(65536)
        except socket.timeout:
            return
        except OSError:
            return
        if data:
            self._left += data

    def _write_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _next_frame(self, timeout: float):
        """返回 (opcode, payload) 或 None（超时）。"""
        deadline = time.time() + timeout
        while True:
            if len(self._left) >= 2:
                b1, b2 = self._left[0], self._left[1]
                ln = b2 & 0x7F
                off = 2
                if ln == 126:
                    if len(self._left) < 4:
                        pass
                    else:
                        ln = struct.unpack(">H", self._left[2:4])[0]
                        off = 4
                elif ln == 127:
                    if len(self._left) < 10:
                        pass
                    else:
                        ln = struct.unpack(">Q", self._left[2:10])[0]
                        off = 10
                if len(self._left) >= off + ln and not (ln == 126 and len(self._left) < 4) \
                        and not (ln == 127 and len(self._left) < 10):
                    op = b1 & 0x0F
                    payload = self._left[off:off + ln]
                    self._left = self._left[off + ln:]
                    return op, payload
            if time.time() > deadline:
                return None
            self._recv_more()

    # ---------- 调用 ----------
    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        self._write_frame(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}).encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            fr = self._next_frame(max(0.05, deadline - time.time()))
            if fr is None:
                break
            op, payload = fr
            if op == 0x8:                       # close
                raise ConnectionError("CDP socket closed by peer")
            if op == 0x9:                       # ping -> pong
                self._write_frame(payload, opcode=0xA)
                continue
            if op != 0x1:
                continue
            msg = json.loads(payload.decode("utf-8"))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self._events.append(msg)
        raise TimeoutError(f"CDP timeout: {method}")

    def eval(self, expr: str, timeout: float = 30.0, by_value: bool = True):
        r = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": by_value,
            "awaitPromise": True,
            "userGesture": True,
        }, timeout=timeout)
        if "exceptionDetails" in r:
            raise RuntimeError("JS error: " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r.get("result", {}).get("value")

    def pump(self, seconds: float) -> None:
        """把等待期间到达的事件收进 self._events。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            fr = self._next_frame(max(0.02, deadline - time.time()))
            if fr is None:
                continue
            op, payload = fr
            if op == 0x1:
                msg = json.loads(payload.decode("utf-8"))
                if "method" in msg:
                    self._events.append(msg)
            elif op == 0x9:
                self._write_frame(payload, opcode=0xA)

    def wait_event(self, method: str, timeout: float = 60.0) -> dict | None:
        deadline = time.time() + timeout
        for e in list(self._events):
            if e.get("method") == method:
                self._events.remove(e)
                return e
        while time.time() < deadline:
            fr = self._next_frame(max(0.05, deadline - time.time()))
            if fr is None:
                continue
            op, payload = fr
            if op == 0x1:
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("method") == method:
                    return msg
                if "method" in msg:
                    self._events.append(msg)
            elif op == 0x9:
                self._write_frame(payload, opcode=0xA)
        return None

    def close(self) -> None:
        try:
            self._write_frame(b"", opcode=0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def mouse_click(cdp: CDP, x: float, y: float, button: str = "left") -> None:
    """真实鼠标事件序列。实测该下拉组件只响应真实鼠标事件，不吃 JS element.click()。"""
    common = {"x": x, "y": y, "button": button, "clickCount": 1,
              "buttons": 1 if button == "left" else 2,
              "pointerType": "mouse"}
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mouseMoved"))
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mousePressed"))
    cdp.call("Input.dispatchMouseEvent", dict(common, type="mouseReleased"))
