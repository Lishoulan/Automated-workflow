#!/usr/bin/env bash
# ============================================================
# Serv00 微信代理一键部署脚本
# ============================================================
# 使用方法：
#   1. 先注册 Serv00 账号：https://www.serv00.com
#   2. 在 Windows PowerShell 中运行：
#      bash serv00_setup.sh <用户名> <服务器地址> <代理密钥>
#
# 示例：
#   bash serv00_setup.sh myuser s1.serv00.com "MyS3cret!2026"
#
# 或者手动逐步执行，参见下方注释
# ============================================================

set -e

USERNAME="${1:?用法: bash serv00_setup.sh <用户名> <服务器地址> <代理密钥>}"
SERVER="${2:?请提供服务器地址，如 s1.serv00.com}"
SECRET="${3:?请提供代理密钥}"

REMOTE_SCRIPT="~/wechat_proxy.py"
LOCAL_SCRIPT="scripts/serv00_wechat_proxy.py"

echo "=========================================="
echo "  Serv00 微信代理一键部署"
echo "=========================================="
echo ""
echo "服务器: ${USERNAME}@${SERVER}"
echo "密钥: ${SECRET}"
echo ""

# 第1步：上传代理脚本
echo "[1/5] 上传代理脚本..."
scp "${LOCAL_SCRIPT}" "${USERNAME}@${SERVER}:${REMOTE_SCRIPT}"
echo "  ✓ 上传完成"

# 第2步：SSH 登录并配置
echo "[2/5] 配置代理并检查连通性..."
ssh "${USERNAME}@${SERVER}" <<REMOTE_EOF
  export WECHAT_PROXY_SECRET="${SECRET}"

  echo "  检查 Python3..."
  python3 --version || { echo "  ✗ Python3 不可用"; exit 1; }

  echo "  检查微信 API 连通性..."
  python3 ${REMOTE_SCRIPT} check

  echo "  获取公网 IP..."
  PUBLIC_IP=\$(python3 -c "
import urllib.request, json
try:
    req = urllib.request.Request('https://api.ipify.org?format=json')
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(json.loads(resp.read()).get('ip', 'unknown'))
except:
    print('unknown')
")
  echo ""
  echo "  =========================================="
  echo "  重要：将以下 IP 加入微信公众号白名单！"
  echo "  公网 IP: \${PUBLIC_IP}"
  echo "  =========================================="
  echo ""

  echo "  停止旧代理进程..."
  pkill -f "wechat_proxy.py" 2>/dev/null || true
  sleep 1

  echo "  启动代理服务..."
  nohup python3 ${REMOTE_SCRIPT} start > ~/proxy.log 2>&1 &
  sleep 3

  echo "  验证代理是否运行..."
  if curl -sf http://localhost:8765/health > /dev/null 2>&1; then
    echo "  ✓ 代理服务已启动"
  else
    echo "  ⚠ 代理可能未成功启动，请检查 ~/proxy.log"
  fi

  echo "  配置 Cron 保活..."
  (crontab -l 2>/dev/null | grep -v "wechat_proxy.py"; echo "*/5 * * * * pgrep -f 'wechat_proxy.py' > /dev/null || (export WECHAT_PROXY_SECRET='${SECRET}'; nohup python3 ${REMOTE_SCRIPT} start >> ~/proxy.log 2>&1 &)") | crontab -
  echo "  ✓ Cron 保活已配置"

  echo "  当前 Crontab："
  crontab -l
REMOTE_EOF

echo ""
echo "[3/5] ✓ Serv00 代理部署完成"
echo ""
echo "=========================================="
echo "  接下来你需要手动完成："
echo "=========================================="
echo ""
echo "  1. 登录微信公众平台 https://mp.weixin.qq.com"
echo "     → 设置与开发 → 基本配置 → IP白名单"
echo "     → 添加上面显示的 Serv00 公网 IP"
echo ""
echo "  2. 编辑项目 .env 文件，添加以下配置："
echo ""
echo "     WECHAT_SERV00_PROXY_ENABLED=1"
echo "     WECHAT_SERV00_PROXY_URL=http://${SERVER}:8765"
echo "     WECHAT_SERV00_PROXY_SECRET=${SECRET}"
echo ""
echo "  3. 如果用 GitHub Actions，添加 GitHub Secrets："
echo "     WECHAT_SERV00_PROXY_ENABLED = 1"
echo "     WECHAT_SERV00_PROXY_URL = http://${SERVER}:8765"
echo "     WECHAT_SERV00_PROXY_SECRET = ${SECRET}"
echo ""
echo "  4. 测试发布："
echo "     python main.py --wechat-publish-latest-approved"
echo ""
echo "=========================================="
