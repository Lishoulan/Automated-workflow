/**
 * Cloudflare Worker — 微信公众号 API 反向代理
 * ============================================================
 * 部署步骤：
 *   1. 注册 Cloudflare 账号：https://dash.cloudflare.com/sign-up
 *   2. 进入 Workers & Pages → 创建 Worker
 *   3. 将此文件内容粘贴到 Worker 编辑器中
 *   4. 修改下方 PROXY_SECRET 为你自己的密钥
 *   5. 部署，记下 Worker URL（如 https://wechat-proxy.你的名字.workers.dev）
 *   6. 在 Cloudflare Dashboard → Workers → 你的 Worker → Settings → Variables 中
 *      添加环境变量 PROXY_SECRET（推荐，比写在代码里安全）
 *   7. 将 Cloudflare Worker 的出口 IP 加入微信公众号白名单
 *      获取 IP 方法：访问 https://你的worker.workers.dev/ip
 *
 * 项目 .env 配置：
 *   WECHAT_SERV00_PROXY_ENABLED=1
 *   WECHAT_SERV00_PROXY_URL=https://wechat-proxy.你的名字.workers.dev
 *   WECHAT_SERV00_PROXY_SECRET=你的密钥
 *
 * 免费额度：10万请求/天，足够日常使用
 */

// ============================================================
// 配置区域
// ============================================================

// 微信公众号 API 真实地址
const WECHAT_API_BASE = 'https://api.weixin.qq.com';

// 允许代理的 API 路径前缀（白名单，防止被滥用为开放代理）
const ALLOWED_PATH_PREFIXES = [
  '/cgi-bin/token',
  '/cgi-bin/material/add_material',
  '/cgi-bin/media/uploadimg',
  '/cgi-bin/media/upload',
  '/cgi-bin/draft/add',
  '/cgi-bin/draft/batchget',
  '/cgi-bin/draft/delete',
  '/cgi-bin/draft/update',
  '/cgi-bin/draft/get',
  '/cgi-bin/freepublish',
  '/cgi-bin/material/batchget_material',
  '/cgi-bin/material/del_material',
  '/cgi-bin/material/get_material',
  '/cgi-bin/material/update_news',
  '/cgi-bin/material/get_materialcount',
  '/cgi-bin/message/custom/send',
  '/cgi-bin/user/info',
  '/cgi-bin/tags',
  '/cgi-bin/menu',
  '/cgi-bin/qrcode',
  '/cgi-bin/shorturl',
  '/cgi-bin/comment',
  '/cgi-bin/template',
];

// 请求超时（毫秒）
const REQUEST_TIMEOUT = 60000;

// ============================================================
// 主处理逻辑
// ============================================================

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const proxySecret = env.PROXY_SECRET || 'change-me-to-a-strong-secret';

    // 健康检查端点
    if (url.pathname === '/health' || url.pathname === '/ping') {
      return new Response(JSON.stringify({
        status: 'ok',
        service: 'cloudflare-wechat-proxy',
        uptime_hint: new Date().toISOString(),
      }), {
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }

    // 获取出口 IP 端点（用于添加微信白名单）
    if (url.pathname === '/ip') {
      // 只用返回 IPv4 的检测服务（微信白名单不支持 IPv6）
      const ipServices = [
        'https://api.ipify.org?format=json',
        'https://ipv4.icanhazip.com',
        'https://checkip.amazonaws.com',
        'https://ipv4-api.ipify.org?format=json',
      ];
      for (const svc of ipServices) {
        try {
          const resp = await fetch(svc, { signal: AbortSignal.timeout(8000) });
          const text = await resp.text();
          let ip = text.trim();
          // 如果返回 JSON，提取 ip 字段
          try { ip = JSON.parse(ip).ip; } catch (_) {}
          // 只接受 IPv4 格式
          if (ip && /^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) {
            return new Response(JSON.stringify({
              public_ip: ip,
              note: '将此 IP 加入微信公众号白名单。如遇 40164 错误请重新访问此页面获取新 IP。',
            }, null, 2), {
              headers: { 'Content-Type': 'application/json; charset=utf-8' },
            });
          }
        } catch (_) { continue; }
      }
      // 所有服务都失败，用微信 API 反查
      return new Response(JSON.stringify({
        error: 'IP 检测服务暂不可用',
        tip: '请通过微信 API 报错获取 IP：配置好 WECHAT_APP_ID/SECRET 后，运行项目发布命令，微信返回的 40164 错误中会包含出口 IP',
      }), {
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }

    // 验证代理密钥
    const authHeader = request.headers.get('X-Proxy-Secret') || '';
    const querySecret = url.searchParams.get('proxy_secret') || '';
    if (authHeader !== proxySecret && querySecret !== proxySecret) {
      return new Response(JSON.stringify({
        errcode: -1,
        errmsg: 'proxy: unauthorized - invalid or missing proxy secret',
      }), {
        status: 403,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }

    // 检查路径白名单
    const targetPath = url.pathname;
    const isAllowed = ALLOWED_PATH_PREFIXES.some(prefix => targetPath.startsWith(prefix));
    if (!isAllowed) {
      return new Response(JSON.stringify({
        errcode: -1,
        errmsg: `proxy: path not allowed - ${targetPath}`,
      }), {
        status: 403,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }

    // 构建目标 URL（去掉 proxy_secret 参数）
    // 强制使用 IPv4：将 api.weixin.qq.com 替换为其 IPv4 地址，并设置 Host header
    const cleanParams = new URLSearchParams(url.searchParams);
    cleanParams.delete('proxy_secret');
    const queryString = cleanParams.toString();
    const targetUrl = `https://api.weixin.qq.com${targetPath}${queryString ? '?' + queryString : ''}`;

    // 构建转发请求的 headers
    const forwardHeaders = new Headers();
    const contentType = request.headers.get('Content-Type');
    if (contentType) {
      forwardHeaders.set('Content-Type', contentType);
    }

    // 读取请求体
    let requestBody = null;
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      requestBody = await request.arrayBuffer();
    }

    // 转发请求
    try {
      const response = await fetch(targetUrl, {
        method: request.method,
        headers: forwardHeaders,
        body: requestBody,
      });

      // 返回响应
      const responseHeaders = new Headers();
      const respContentType = response.headers.get('Content-Type');
      if (respContentType) {
        responseHeaders.set('Content-Type', respContentType);
      }
      responseHeaders.set('X-Proxy-By', 'cloudflare-wechat-proxy');

      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders,
      });

    } catch (e) {
      return new Response(JSON.stringify({
        errcode: -1,
        errmsg: `proxy: upstream error - ${e.message}`,
      }), {
        status: 502,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }
  },
};
