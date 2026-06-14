---
name: login-check
description: 检查各平台（头条、微信、135编辑器）的浏览器登录状态，如果 session 过期则引导重新登录。适用于用户说"检查登录"、"login check"、"session 状态"等场景。
---

# Login Check — 平台登录检查

## 概述

检查头条创作者平台、微信公众号、135编辑器的浏览器 session 是否有效，失效时引导重新登录。

## 执行步骤

### Step 1: 运行保活脚本

在项目根目录执行 session 保活检查：

```powershell
python scripts/keep_alive.py --account default
```

这会访问各平台首页并检查 Cookie 是否有效。

### Step 2: 解读结果

读取 `output/automation/keep_alive_result.json`，展示各平台状态：

```
🔐 平台登录状态
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  头条：✅ Session 有效 / ❌ 需要重新登录
  微信135：✅ Session 有效 / ❌ 需要重新登录
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 3: 处理登录过期

如果有平台需要重新登录，使用 MCP browser-use 工具引导登录：

**头条登录：**
1. 使用 `browser-use` MCP 的 `navigate_page` 打开 `https://mp.toutiao.com/profile_v4/graphic/publish`
2. 使用 `take_screenshot` 截图展示当前页面
3. 如果出现登录二维码，提示用户用手机扫码
4. 等待用户扫码后再次 `take_screenshot` 确认登录成功

**135编辑器登录：**
1. 使用 `browser-use` MCP 的 `navigate_page` 打开 `https://www.135editor.com/beautify_editor.html`
2. 截图展示登录页面
3. 引导用户完成微信 OAuth 授权登录

### Step 4: 验证登录

登录完成后，再次运行保活脚本验证：

```powershell
python scripts/keep_alive.py --account default
```

确认所有平台状态为 `alive`。

## 账号切换

检查其他账号的登录状态：

```powershell
python scripts/keep_alive.py --account robot_notes
python scripts/keep_alive.py --account politics_notes
```

## 注意事项

- 头条登录使用手机扫码，二维码有效期约 2 分钟
- 135编辑器使用微信 OAuth，需要微信已登录
- 浏览器 Profile 目录隔离在 `.runtime/accounts/<account>/toutiao_profile`
