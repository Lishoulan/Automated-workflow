# 两个头条账号 + 公众号自动化部署完成

## 概览

成功配置了两个今日头条账号和一个微信公众号的每日自动化内容生产和发布系统。

## 配置内容

### 1. 配置文件

- **.env：已配置微信公众号启用
- **config.yaml：两个头条账号配置正确

### 2. 任务配置

#### 科技号 (default)

| 时间 | 任务名称 |
|------|----------|
| 08:00 | Toutiao_Default_0800 |
| 12:00 | Toutiao_Default_1200 |
| 18:30 | Toutiao_Default_1830 |
| 22:00 | Toutiao_Default_2200 |

#### 国际号 (robot_notes)

| 时间 | 任务名称 |
|------|----------|
| 07:30 | Toutiao_Intl_0730 |
| 11:30 | Toutiao_Intl_1130 |
| 18:00 | Toutiao_Intl_1800 |
| 21:30 | Toutiao_Intl_2130 |

#### 微信公众号

| 时间 | 任务名称 |
|------|----------|
| 09:00 | WeChat_0900 |
| 12:30 | WeChat_1230 |
| 19:00 | WeChat_1900 |

### 3. 脚本文件

- `scripts/multi_account_daily_run.ps1：多账号统一运行脚本
- `scripts/setup_scheduled_tasks.ps1：定时任务安装脚本

## 下一步

1. 配置头条账号和微信公众号的浏览器登录状态
2. 进行人工审核内容发布

## 验证

可以运行以下命令测试：

```powershell
# 测试科技号
python main.py --account default --daily-run

# 测试国际号
python main.py --account robot_notes --daily-run
```
