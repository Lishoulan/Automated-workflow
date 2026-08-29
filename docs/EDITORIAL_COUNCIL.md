# 编辑委员会

项目现在有两层内容智能体：

- 基础生产链：趋势、数据、洞察、写作、润色、标题、内容审核、发布审核和演进 Agent。
- 写作前专家委员会：证据审计、产业链策略、读者编辑、反方质询、原创性编辑，最后由主编协调器裁决。

## 实际流程

```text
热点与素材
  -> TrendAgent
  -> DataAgent
  -> DataMiningAgent（默认云端开启）
  -> EditorialCouncilAgent
       -> 角色独立判断
       -> 主编统一裁决
  -> WriterAgent
  -> PolishAgent
  -> QualityReviewCouncilAgent
       -> 事实与证据
       -> 逻辑与因果
       -> 结构与可读性
       -> 原创性与 AI 痕迹
       -> 合规与风险
       -> 必要时自动返工一次
  -> TitleAgent
  -> ReviewAgent
  -> PublishReviewAgent
```

委员会只使用当前素材、结构化数据、历史去重信息和已验证的运营规则。输出会保留事实证据、因果链、产业链路径、反方观点、证据缺口和原创切口，并写入 `WorkflowResult.editorial_council`，方便复盘。

成稿质量审核专家团独立检查润色后的正文，结果写入 `WorkflowResult.quality_review_council`，并导出为 `quality_review_council.json`。模型发现问题时最多自动返工一次；模型不可用时使用本地保守规则。未通过质量审核的文章会被设置为 `revise_before_publish`，自动发布就绪分为 0。若后续评审再次改写正文，发布审核前会触发一次最终质量复核。

## 配置

默认开启：

```env
TOUTIAO_EXPERT_COUNCIL_ENABLED=1
TOUTIAO_ENHANCED_INSIGHTS_USE_DATA_MINING=1
```

关闭委员会时，将 `TOUTIAO_EXPERT_COUNCIL_ENABLED` 设为 `0`，系统会回退到原有 `InsightAgent`。专家角色卡位于 `config/agency_roles/`，可在不改编排代码的情况下调整角色边界。

委员会采用一次批量结构化调用完成多角色讨论，避免每个角色单独调用导致云端耗时和成本成倍增加。模型不可用时保留基于输入证据的本地回退，不会为了凑字数虚构数字、公司关系或订单信息。
