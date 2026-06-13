#!/usr/bin/env python3
"""
Serv00 微信公众号 API 反向代理
=================================
部署在 Serv00 免费主机上，作为微信公众号 API 的反向代理。
Serv00 拥有固定公网 IP，将该 IP 加入微信公众号白名单后，
本地脚本通过此代理转发 API 请求，即可解决 IP 白名单频繁变化的问题。

部署步骤：
1. 注册 Serv00 账号，获取 SSH 登录信息
2. 通过 SSH 上传此脚本到 Serv00（如 ~/wechat_proxy.py）
3. 修改下方的 PROXY_SECRET 为你自己的密钥
4. 运行: python3 wechat_proxy.py
5. 在 Serv00 的 DevilWEB 后台设置 Cron 定时任务保持运行
6. 将 Serv00 的公网 IP 加入微信公众号白名单
7. 在本地项目 .env 中设置:
   WECHAT_API_BASE_URL=https://yourname.serv00.net:8765
   WECHAT_SERV00_PROXY_SECRET=你的密钥

仅使用 Python 标准库，无需安装第三方依赖。
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import urllib.error
import http.server
import socketserver
import logging
import signal
import time

# ============================================================
# 配置区域 - 请根据实际情况修改
# ============================================================

# 代理服务监听端口（Serv00 默认只开放特定端口，常用范围 10000-65535）
LISTEN_PORT = int(os.getenv("WECHAT_PROXY_PORT", "8765"))

# 代理密钥，用于防止未授权访问。本地脚本请求时需携带此密钥
PROXY_SECRET = os.getenv("WECHAT_PROXY_SECRET", "change-me-to-a-strong-secret")

# 微信公众号 API 真实地址
WECHAT_API_BASE = "https://api.weixin.qq.com"

# 请求超时（秒）
REQUEST_TIMEOUT = 60

# 允许代理的 API 路径前缀（白名单机制，防止被滥用为开放代理）
ALLOWED_PATH_PREFIXES = (
    "/cgi-bin/token",
    "/cgi-bin/material/add_material",
    "/cgi-bin/media/uploadimg",
    "/cgi-bin/draft/add",
    "/cgi-bin/draft/batchget",
    "/cgi-bin/freepublish",
    "/cgi-bin/material/batchget_material",
    "/cgi-bin/media/upload",
    "/cgi-bin/message/custom/send",
    "/cgi-bin/user/info",
    "/cgi-bin/tags",
    "/cgi-bin/menu",
    "/cgi-bin/qrcode",
    "/cgi-bin/shorturl",
    "/cgi-bin/comment",
    "/cgi-bin/template",
)

# ============================================================
# 日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wechat_proxy")


class WeChatProxyHandler(http.server.BaseHTTPRequestHandler):
    """微信公众号 API 反向代理请求处理器"""

    def log_message(self, format, *args):
        logger.info("%s - %s", self.client_address[0], format % args)

    def _verify_secret(self) -> bool:
        """验证代理密钥"""
        auth_header = self.headers.get("X-Proxy-Secret", "")
        query_secret = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query
        ).get("proxy_secret", [""])
        return auth_header == PROXY_SECRET or query_secret[0] == PROXY_SECRET

    def _check_path_allowed(self, path: str) -> bool:
        """检查请求路径是否在允许列表中"""
        # 去掉 query string 后检查路径
        clean_path = urllib.parse.urlparse(path).path
        for prefix in ALLOWED_PATH_PREFIXES:
            if clean_path.startswith(prefix):
                return True
        return False

    def _forward_request(self, method: str) -> None:
        """转发请求到微信公众号 API"""
        # 验证密钥
        if not self._verify_secret():
            logger.warning("Unauthorized request from %s: secret mismatch", self.client_address[0])
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "errcode": -1,
                "errmsg": "proxy: unauthorized - invalid or missing proxy secret",
            }).encode("utf-8"))
            return

        # 解析路径，去掉 proxy_secret 参数
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        params.pop("proxy_secret", None)
        clean_query = urllib.parse.urlencode(params, doseq=True)
        target_path = parsed.path
        if clean_query:
            target_path = f"{target_path}?{clean_query}"

        # 检查路径白名单
        if not self._check_path_allowed(target_path):
            logger.warning("Blocked path from %s: %s", self.client_address[0], target_path)
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "errcode": -1,
                "errmsg": f"proxy: path not allowed - {parsed.path}",
            }).encode("utf-8"))
            return

        # 构建目标 URL
        target_url = f"{WECHAT_API_BASE}{target_path}"
        logger.info("Forwarding %s %s -> %s", method, parsed.path, target_url[:120])

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        request_body = self.rfile.read(content_length) if content_length > 0 else None

        # 构建转发请求的 headers
        forward_headers = {}
        content_type = self.headers.get("Content-Type", "")
        if content_type:
            forward_headers["Content-Type"] = content_type

        # 创建 SSL 上下文
        ssl_context = ssl.create_default_context()

        try:
            req = urllib.request.Request(
                target_url,
                data=request_body,
                headers=forward_headers,
                method=method,
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ssl_context) as resp:
                response_body = resp.read()
                response_status = resp.status
                response_headers = dict(resp.headers)

            # 返回响应
            self.send_response(response_status)

            # 透传关键响应头
            for header_name in ("Content-Type", "Content-Length"):
                if header_name in response_headers:
                    self.send_header(header_name, response_headers[header_name])

            # 添加代理标识头
            self.send_header("X-Proxy-By", "serv00-wechat-proxy")
            self.end_headers()
            self.wfile.write(response_body)

            # 记录响应摘要
            try:
                resp_json = json.loads(response_body)
                errcode = resp_json.get("errcode", 0)
                if errcode:
                    logger.warning("WeChat API error: errcode=%s errmsg=%s", errcode, resp_json.get("errmsg", "")[:100])
                else:
                    logger.info("WeChat API success: %s", target_url[:80])
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.info("WeChat API response: %d bytes, status=%d", len(response_body), response_status)

        except urllib.error.HTTPError as exc:
            error_body = exc.read() if hasattr(exc, "read") else b""
            logger.error("WeChat API HTTP error: %d %s", exc.code, str(exc.reason)[:200])
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(error_body)

        except urllib.error.URLError as exc:
            logger.error("WeChat API URL error: %s", str(exc.reason)[:200])
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "errcode": -1,
                "errmsg": f"proxy: upstream error - {exc.reason}",
            }).encode("utf-8"))

        except Exception as exc:
            logger.error("Proxy error: %s", str(exc)[:200])
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "errcode": -1,
                "errmsg": f"proxy: internal error - {exc}",
            }).encode("utf-8"))

    def do_GET(self):
        """处理 GET 请求"""
        self._forward_request("GET")

    def do_POST(self):
        """处理 POST 请求"""
        self._forward_request("POST")

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Proxy-Secret")
        self.end_headers()


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def health_check_handler(handler: WeChatProxyHandler):
    """健康检查端点"""
    parsed = urllib.parse.urlparse(handler.path)
    if parsed.path == "/health" or parsed.path == "/ping":
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps({
            "status": "ok",
            "service": "serv00-wechat-proxy",
            "uptime_hint": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }).encode("utf-8"))
        return True
    return False


# 扩展原始 handler，添加健康检查
class ProxyHandlerWithHealthCheck(WeChatProxyHandler):
    def do_GET(self):
        if health_check_handler(self):
            return
        super().do_GET()


def run_server(port: int):
    """启动代理服务器"""
    server = ReusableTCPServer(("0.0.0.0", port), ProxyHandlerWithHealthCheck)
    logger.info("WeChat API proxy starting on port %d", port)
    logger.info("Proxy secret configured: %s", "***" if PROXY_SECRET != "change-me-to-a-strong-secret" else "DEFAULT - PLEASE CHANGE!")
    logger.info("Upstream API base: %s", WECHAT_API_BASE)
    logger.info("Allowed paths: %d prefixes", len(ALLOWED_PATH_PREFIXES))

    # 优雅关闭
    def shutdown_handler(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Proxy server stopped.")


def check_connectivity():
    """检查 Serv00 到微信 API 的连通性"""
    logger.info("Checking connectivity to WeChat API...")
    try:
        ssl_context = ssl.create_default_context()
        req = urllib.request.Request(
            f"{WECHAT_API_BASE}/cgi-bin/token?grant_type=client_credential&appid=test&secret=test",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as resp:
            body = json.loads(resp.read())
            errcode = body.get("errcode", 0)
            # errcode=40125 表示 AppSecret 错误，但说明网络是通的
            if errcode in (40125, 40132, 40013):
                logger.info("Connectivity OK: WeChat API is reachable (test request returned expected error)")
            else:
                logger.info("Connectivity OK: WeChat API responded with errcode=%s", errcode)
    except Exception as exc:
        logger.error("Connectivity check FAILED: %s", exc)
        logger.error("Serv00 may not be able to reach api.weixin.qq.com. Check network settings.")
        return False
    return True


def get_public_ip():
    """获取 Serv00 的公网 IP"""
    logger.info("Detecting public IP...")
    try:
        ssl_context = ssl.create_default_context()
        req = urllib.request.Request("https://api.ipify.org?format=json")
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as resp:
            data = json.loads(resp.read())
            public_ip = data.get("ip", "unknown")
            logger.info("Public IP: %s", public_ip)
            logger.info("Add this IP to your WeChat Official Account whitelist!")
            return public_ip
    except Exception:
        # 备用方案
        try:
            req = urllib.request.Request("https://ifconfig.me")
            req.add_header("User-Agent", "curl/7.64.1")
            with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as resp:
                public_ip = resp.read().decode().strip()
                logger.info("Public IP: %s", public_ip)
                logger.info("Add this IP to your WeChat Official Account whitelist!")
                return public_ip
        except Exception as exc:
            logger.warning("Could not detect public IP: %s", exc)
            return "unknown"


if __name__ == "__main__":
    print("=" * 60)
    print("  Serv00 微信公众号 API 反向代理")
    print("=" * 60)
    print()

    # 解析命令行参数
    action = sys.argv[1] if len(sys.argv) > 1 else "start"

    if action == "check":
        # 连通性检查模式
        check_connectivity()
        get_public_ip()
    elif action == "ip":
        # 仅获取公网 IP
        get_public_ip()
    elif action == "start":
        # 启动代理服务器
        if PROXY_SECRET == "change-me-to-a-strong-secret":
            logger.warning("WARNING: Using default proxy secret! Set WECHAT_PROXY_SECRET env var!")
        check_connectivity()
        get_public_ip()
        print()
        run_server(LISTEN_PORT)
    else:
        print(f"Usage: python3 {sys.argv[0]} [start|check|ip]")
        print()
        print("  start  - Start the proxy server (default)")
        print("  check  - Check connectivity to WeChat API")
        print("  ip     - Show public IP address")
        sys.exit(1)
