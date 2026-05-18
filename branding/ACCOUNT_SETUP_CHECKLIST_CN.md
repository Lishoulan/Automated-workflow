# 多账号开设与接入清单

这份清单用于你实际开号和接入自动化流程时逐项确认。

## 一、建议先做的 3 个账号

第一阶段先上这 3 个：

1. `手蓝数据笔记`
2. `手蓝机器人笔记`
3. `手蓝储能笔记`

原因：

- 主账号负责品牌心智
- 机器人号最贴近你当前已有内容能力
- 储能号和机器人号差异明显，不容易内容互撞

AI硬件号和智造号可以第二阶段再接。

## 二、每个账号开号时要准备的信息

每个账号需要你准备：

- 账号名称
- 账号简介
- 头像
- 登录手机号 / 登录方式

## 三、开好号后接入顺序

### 1. 初始化第一个账号登录

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey default
```

### 2. 初始化机器人号登录

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey robot_notes
```

### 3. 初始化储能号登录

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -AccountKey energy_notes
```

## 四、接入后要验证的事情

每个账号都要验证：

1. 能进入头条图文发布页
2. 能生成内容
3. 能导出发布包
4. 能自动填入标题和正文
5. 能上传封面和正文图
6. 能成功发布或提交审核

## 五、全部验证通过后再开启定时任务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_all_accounts.ps1 -EnableAutoPublish
```

## 六、你辅助我的方式

你后面只需要做这几件事：

1. 按文档把账号开好
2. 把头像上传到对应账号
3. 在我执行 `init_publish_login.ps1` 时，协助完成登录
4. 告诉我哪个账号已经登录完成

后续我就可以继续帮你逐个账号接入自动化发布链路。
