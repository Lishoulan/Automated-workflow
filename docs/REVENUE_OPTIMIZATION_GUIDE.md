# 收益优化与内容级经营分析指南

## 概述

本方案把“流量→线索→成交→复盘”闭环落到代码与 CLI：

- 每篇文章都有稳定 `content_id`，串联 run_dir、平台、账号；
- 记录 API / 图片 / 人工成本，并关联线索、成交、赞助等变现事件；
- 支持 1h / 6h / 24h / 72h 多时点快照与单位经济性计算；
- 选题自动补齐商业意图、内容组合、CTA 变体、标题/封面/平台改写元数据；
- 输出可直接用于决策的经营报表，而不仅是阅读量报表。

## 核心改动

### 1. 收益归因服务
新增 `src/toutiao_content_factory/services/revenue_attribution_service.py`：

- `ContentIdentity`：稳定 content_id + 商业意图 + 内容组合 + CTA / 标题 / 封面 / 平台变体；
- `CostRecord`：API / 图片 / 人工成本；
- `RevenueEvent`：lead_value / deal / sponsorship 等变现事件；
- `PerformanceSnapshot`：多时点表现快照；
- `UnitEconomics`：RPM、净利润、线索成本；
- `build_experiment_report()`：按平台、选题桶、商业意图、内容组合、CTA、标题、封面聚合加权阅读率、线索和收入；
- `build_cooling_candidates()`：低收益选题桶冷却建议；
- `build_business_report()`：经营分析 Markdown 报表。

### 2. 选题元数据增强
`TopicCandidate` 与 `WorkflowResult` 新增字段：

- `commercial_intent`: low / medium / high
- `content_mix`: traffic / professional / conversion
- `cta_variant`, `cta_source_code`
- `title_variant`, `cover_variant`, `platform_variant`

`TopicAutomationService._enrich_topic_revenue_metadata()` 会自动推断这些字段，写入 `selected_topic.json` 与 `selected_topic.md`。

### 3. 实验标签扩展
`GrowthOpsService.build_experiment_tags()` 现在也输出上述字段，供后续按 CTA、标题、封面、平台做对照分析。

### 4. 单一 CTA 策略
`WriterAgent._default_comment_prompt()` 优先根据 `cta_variant` / `content_mix` 选择：

- `consultation` → 咨询类 CTA
- `newsletter` → 周报类 CTA
- `discussion` → 讨论类 CTA

避免一篇文章同时放多个入口分散转化。

### 5. 平台改写元数据
`ContentAdapter.adapt_for_platform()` 现在返回 `platform_variant` 与 `adaptation_note`，为后续按平台统计表现提供基础。

### 6. 发布时自动注册 content_id
`PublishService.publish_run()` 和微信发布会在完成后调用统一的 `register_published_run()`，把 content_id 写入 `growth_ops/revenue_attribution/content_index.json`，并从运行目录的 `workflow_result.json` 自动记录 API 成本。

## CLI 用法

```powershell
# 生成经营报表
python .\main.py --build-revenue-attribution-report

# 注册 content_id（基于最近一次 review/publish）
python .\main.py --register-content-identity --run-dir "output/runs/..." --performance-platform toutiao

# 记录成本
python .\main.py --record-content-cost --content-id ci_xxx --api-cost 0.8 --image-cost 0.2 --labor-minutes 15 --labor-rate-per-hour 120

# 记录变现事件
python .\main.py --record-content-revenue --content-id ci_xxx --revenue-event-type lead_value --revenue-amount 200 --revenue-product-key research_sprint --revenue-channel wechat_keyword

# 记录带内容归因的真实线索（可使用 CTA 来源码自动解析）
python .\main.py --record-lead --lead-name "客户A" --lead-contact "customer@example.com" --lead-intent "咨询" --lead-source wechat_keyword --lead-cta-source-code robot_high_consultation --lead-platform toutiao

# 计算单位经济性
python .\main.py --compute-unit-economics --content-id ci_xxx --platform-revenue 12.5
```

## 推荐运营节奏

1. 每篇发布后 1h / 6h / 24h / 72h 各跑一次 `--record-performance`；
2. 每周跑一次 `--build-revenue-attribution-report`，按加权阅读率、RPM 和线索收入排序选题；
3. 默认策略只使用 24h / 72h 成熟快照，至少 10 个样本后才会触发冷却或停止；
4. 有线索或成交的低阅读选题最多降权，不会被硬停止；
5. 优先验证高毛利 B2B 产品（研究定制、诊断、顾问），再扩展 CPS / 积分等复杂变现。

## T+1 收益口径

头条平台的收益按 T+1 结算，采集日、发布日期和收益归属日必须分开记录：

- `collection_date`：实际从平台采集数据的日期；
- `published_date`：文章实际发布日期；
- `earnings_date`：本次数据对应的收益日期，头条默认是采集日的前一天；
- 当天发布的头条文章标记为 `pending_t1`，可以保留阅读和互动数据，但不进入收益排行、低收益冷却或 Prompt 演化；
- 待结算收益显示为“待回流”，不能当作已知的 0 元；只有明确回传金额（包括明确的 0 元）时，才将 `revenue_amount_known` 设为 true；
- 重新采集失败或金额未知时，保留同一内容已有的有效收益和指标，避免把历史数据覆盖成 0。

## 已知限制

- 当前不会自动从头条/公众号拉取 1h/6h/24h/72h 快照，仍需手动或后续 RPA 补充；
- 成交金额仍需由支付/CRM 回调或 CLI 写入；线索和付款状态已能自动同步到内容收益账本，但尚未接入外部支付/CRM；
- 平台改写仍为规则级适配，未接入 LLM 重写；
- 低收益冷却仅基于已有归因数据，样本不足、快照未成熟或 RPM 无成本/收入信号时不会触发停止建议。
