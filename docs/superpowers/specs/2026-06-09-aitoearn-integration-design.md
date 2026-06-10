# AiToEarn 能力集成设计

> 借鉴 AiToEarn 的多平台发布、互动Agent、变现系统、矩阵运营能力，移植到内容工厂项目。

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    内容工厂 (Content Factory)              │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │InsightAgent│  │WriterAgent│  │DeerFlow  │  内容生产层   │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘              │
│        └───────────────┼───────────────┘                  │
│                        ▼                                  │
│  ┌─────────────────────────────────────┐                 │
│  │       MultiPlatformPublisher        │  发布层          │
│  │  ┌─────┐┌─────┐┌─────┐┌─────┐      │                 │
│  │  │头条 ││微信 ││抖音 ││B站  │      │                 │
│  │  └─────┘└─────┘└─────┘└─────┘      │                 │
│  │  ┌─────┐┌─────┐┌─────┐┌─────┐      │                 │
│  │  │知乎 ││微博 ││小红书││YouTube│     │                 │
│  │  └─────┘└─────┘└─────┘└─────┘      │                 │
│  └──────────────┬──────────────────────┘                 │
│                 ▼                                        │
│  ┌─────────────────────────────────────┐                 │
│  │        EngagementAgent              │  互动层          │
│  │  点赞 · 收藏 · 关注 · AI评论回复      │                 │
│  │  评论挖掘 → 选题池                    │                 │
│  └──────────────┬──────────────────────┘                 │
│                 ▼                                        │
│  ┌─────────────────────────────────────┐                 │
│  │       MonetizationEngine            │  变现层          │
│  │  CPS带货 · CPE互动 · CPM曝光         │                 │
│  │  积分账户 · 收入统计                  │                 │
│  └──────────────┬──────────────────────┘                 │
│                 ▼                                        │
│  ┌─────────────────────────────────────┐                 │
│  │       MatrixOpsManager              │  矩阵运营层      │
│  │  多账号管理 · 批量发布 · 数据看板      │                 │
│  └─────────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────┘
```

## 2. 多平台发布 (MultiPlatformPublisher)

### 2.1 设计原则

- **Playwright浏览器自动化**：与现有头条/微信/抖音一致
- **统一基类 + 平台特化**：`PlatformPublisher` 基类定义接口，子类实现平台细节
- **异步队列**：发布任务入队，支持定时发布和重试
- **内容适配**：同一篇文章自动适配不同平台格式（字数、标签、图片要求）

### 2.2 平台适配器接口

```python
class PlatformPublisher(ABC):
    """统一发布接口"""

    @abstractmethod
    def publish_article(self, title: str, content: str, images: list[str],
                        *, tags: list[str] = None, category: str = None) -> PublishResult:
        """发布文章"""

    @abstractmethod
    def check_login_status(self) -> bool:
        """检查登录状态"""

    @abstractmethod
    def login_interactive(self) -> dict:
        """交互式登录（扫码/密码）"""

    def adapt_content(self, content: str, platform: str) -> str:
        """适配内容到平台格式（字数限制、标签格式等）"""
```

### 2.3 新增平台适配器

| 平台 | 类名 | 发布URL | 特殊处理 |
|------|------|---------|---------|
| B站 | BilibiliPublisher | creator.bilibili.com | 专栏文章，需选分区 |
| 知乎 | ZhihuPublisher | zhuanlan.zhihu.com | 长文专栏，支持专栏选择 |
| 微博 | WeiboPublisher | weibo.com | 140字短文或长文，话题标签 |
| 小红书 | XiaohongshuPublisher | creator.xiaohongshu.com | 图文笔记，9图限制 |
| YouTube | YoutubePublisher | studio.youtube.com | 需视频内容，文章转视频 |

### 2.4 内容适配规则

```python
PLATFORM_RULES = {
    "toutiao": {"max_length": 50000, "tag_prefix": "#", "tag_suffix": "#"},
    "wechat": {"max_length": 20000, "tag_prefix": "", "tag_suffix": ""},
    "douyin": {"max_length": 3000, "tag_prefix": "#", "tag_suffix": " "},
    "bilibili": {"max_length": 25000, "tag_prefix": "", "tag_suffix": ","},
    "zhihu": {"max_length": 50000, "tag_prefix": "", "tag_suffix": ""},
    "weibo": {"max_length": 2000, "tag_prefix": "#", "tag_suffix": "#"},
    "xiaohongshu": {"max_length": 1000, "tag_prefix": "#", "tag_suffix": " ", "max_images": 9},
}
```

## 3. 互动Agent (EngagementAgent)

### 3.1 功能设计

| 动作 | 说明 | 触发方式 |
|------|------|---------|
| 点赞 | 自动点赞同领域热门内容 | 定时扫描 |
| 收藏 | 收藏优质内容到素材库 | 定时扫描 |
| 关注 | 关注同领域创作者 | 发现新作者时 |
| AI评论回复 | 用DeepSeek生成评论回复 | 收到评论时 |
| 评论挖掘 | 提取热门评论作为选题 | 每日定时 |

### 3.2 互动策略

```python
class EngagementStrategy:
    """互动策略配置"""
    target_keywords: list[str]     # 目标关键词
    target_accounts: list[str]     # 目标账号
    max_daily_likes: int = 50      # 每日最大点赞数
    max_daily_comments: int = 20   # 每日最大评论数
    comment_style: str = "professional"  # 评论风格
    engagement_hours: list[int]    # 互动时间段（避免凌晨）
```

### 3.3 AI评论生成

```python
def generate_reply(self, comment: str, article_title: str) -> str:
    """用DeepSeek生成评论回复"""
    prompt = f"""你是一个科技领域的专业创作者，有人在你关于"{article_title}"的文章下评论：
"{comment}"
请生成一条专业、友善、有价值的回复（50字以内）。"""
    return self.llm_service.call(prompt, max_tokens=100)
```

### 3.4 评论挖掘 → 选题池

```python
def mine_comments_for_topics(self, platform: str, account_id: str) -> list[TopicCandidate]:
    """从评论中挖掘选题"""
    # 1. 获取最近7天的热门评论
    # 2. 用DeepSeek提取话题和需求
    # 3. 评分排序后加入选题池
```

## 4. 变现系统 (MonetizationEngine)

### 4.1 积分账户

```python
class CreditsAccount:
    user_id: str
    balance: int          # 当前积分余额
    total_earned: int     # 累计收入
    total_spent: int      # 累计支出
    pending: int          # 待结算

class CreditsTransaction:
    id: str
    user_id: str
    type: str             # earn | spend | refund | settlement
    amount: int
    source: str           # cps | cpe | cpm | ai_service | refund
    description: str
    created_at: datetime
    settlement_status: str  # pending | settled | failed | refunded
```

### 4.2 CPS带货

```python
class CPSOffer:
    """CPS带货商品"""
    id: str
    title: str
    product_url: str
    commission_rate: float  # 佣金比例
    category: str
    platform: str           # 来源平台（淘宝/京东/拼多多）

class CPSTracking:
    """CPS追踪"""
    offer_id: str
    article_id: str
    click_count: int
    order_count: int
    commission_earned: float
```

### 4.3 CPE互动任务

```python
class CPETask:
    """CPE互动任务"""
    id: str
    platform: str
    task_type: str         # like | comment | follow | share
    target_url: str
    reward: int            # 完成奖励积分
    status: str            # pending | completed | expired
```

### 4.4 收入统计

```python
class RevenueReport:
    """收入统计"""
    period: str            # daily | weekly | monthly
    total_revenue: float
    by_source: dict        # {cps: xx, cpe: xx, cpm: xx}
    by_platform: dict      # {toutiao: xx, wechat: xx, ...}
    top_articles: list     # 收入最高的文章
```

## 5. 矩阵运营 (MatrixOpsManager)

### 5.1 多账号管理

```python
class MatrixAccount:
    """矩阵账号"""
    id: str
    platform: str          # toutiao | wechat | douyin | ...
    account_name: str
    group_id: str          # 账号分组
    login_status: str      # logged_in | expired | never
    profile_dir: str       # 浏览器Profile路径
    stats: AccountStats    # 粉丝/阅读/收入统计

class AccountGroup:
    """账号分组"""
    id: str
    name: str              # 如"科技矩阵"、"财经矩阵"
    accounts: list[str]    # 账号ID列表
    publish_strategy: str  # 同步发布 | 错峰发布 | 差异化内容
```

### 5.2 批量发布

```python
class BatchPublishTask:
    """批量发布任务"""
    id: str
    content_id: str        # 源内容
    target_accounts: list[str]  # 目标账号
    status: dict[str, str]     # {account_id: status}
    scheduled_at: datetime | None
    content_adaptations: dict   # {platform: adapted_content}
```

### 5.3 数据看板

```python
class DashboardData:
    """运营看板"""
    accounts_overview: list[AccountStats]
    publish_stats: PublishStats       # 发布统计
    engagement_stats: EngagementStats  # 互动统计
    revenue_stats: RevenueStats       # 收入统计
    content_performance: list[ContentPerformance]  # 内容表现
```

## 6. 数据存储

使用 **SQLite**（而非MongoDB），与项目现有架构一致：

- `matrix_accounts.db` — 矩阵账号数据
- `credits.db` — 积分账户数据
- `engagement.db` — 互动记录数据
- `monetization.db` — 变现追踪数据

## 7. 配置扩展

### .env 新增字段

```env
# 多平台发布
BILIBILI_ENABLED=0
ZHIHU_ENABLED=0
WEIBO_ENABLED=0
XIAOHONGSHU_ENABLED=0
YOUTUBE_ENABLED=0

# 互动Agent
ENGAGEMENT_ENABLED=0
ENGAGEMENT_MAX_DAILY_LIKES=50
ENGAGEMENT_MAX_DAILY_COMMENTS=20
ENGAGEMENT_COMMENT_STYLE=professional

# 变现系统
MONETIZATION_ENABLED=0
CPS_ENABLED=0
CPE_ENABLED=0

# 矩阵运营
MATRIX_ENABLED=0
```

### config.yaml 新增

```yaml
multi_platform:
  platforms:
    - name: bilibili
      enabled: false
      category: "科技"
    - name: zhihu
      enabled: false
    - name: weibo
      enabled: false
    - name: xiaohongshu
      enabled: false
      max_images: 9

engagement:
  enabled: false
  strategy:
    target_keywords: ["AI", "机器人", "新能源"]
    max_daily_likes: 50
    max_daily_comments: 20
    engagement_hours: [8,9,10,11,12,14,15,16,17,18,19,20,21]

monetization:
  enabled: false
  cps:
    enabled: false
    platforms: ["taobao", "jd"]
  cpe:
    enabled: false

matrix:
  enabled: false
  groups: []
```

## 8. Docker Compose 扩展

无需新增容器，所有功能在 content-factory 容器内运行（Playwright已包含）。

## 9. GitHub Actions 扩展

```yaml
# daily-run.yml 新增步骤
- name: Run engagement agent
  if: env.ENGAGEMENT_ENABLED == '1'
  run: python main.py --engagement --account robot_notes

- name: Run monetization sync
  if: env.MONETIZATION_ENABLED == '1'
  run: python main.py --monetization-sync
```

## 10. 实施优先级

| 阶段 | 模块 | 预计文件数 |
|------|------|-----------|
| P0 | 多平台发布（B站+知乎+微博） | 8 |
| P1 | 互动Agent（点赞+AI评论回复） | 4 |
| P2 | 矩阵运营（多账号+批量发布） | 4 |
| P3 | 变现系统（CPS+CPE+积分） | 5 |
