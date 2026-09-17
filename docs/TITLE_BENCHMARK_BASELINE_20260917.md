# 标题形态基线（离线）

> 本文件由 `scripts/benchmark_titles.py` 生成。
> **完全离线**：不调用 LLM、不启动 Playwright、不联网。

## 样本范围与数据来源

- `F:\Automated-workflow-main\output\accounts\robot-notes\runs`
- `F:\Automated-workflow-main\.cloud-artifacts`
- `F:\Automated-workflow-main\.runtime\diag`

- 有效样本数：**260**

## 数据缺失（重要）

- **合规过滤后的候选列表没有落盘**：titles.txt 只保留「全部标题」与「推荐标题」，逐条候选的合规判定结果（哪条被 RULE_006/007/009 拦下）**数据不可获得**，因此无法计算标题级 blocked_rate。现值仅为 run 级（文章级）代理指标。
- **去重前的原始模型输出没有落盘**：titles.txt 里的「全部标题」已经是模型输出 ∪ 兜底标题经去重后的结果，生成侧真实候选数**数据不可获得**。

## 指标

| 指标 | 值 |
|---|---:|
| sample_count | 260 |
| candidate_total | 1671 |
| candidate_count mean | 6.43 |
| candidate_count median | 5.0 |
| candidate_count min | 1 |
| candidate_count max | 20 |
| unique_title_count | 1139 |
| unique_skeleton_count | 1136 |
| skeleton_duplicate_rate | 32.0% |
| similarity mean | 0.195 |
| similarity median | 0.172 |
| similarity max | 0.916 |
| largest_cluster_ratio@0.5 mean | 0.442 |
| largest_cluster_ratio@0.5 max | 1.000 |
| title_length mean | 20.6 |
| title_length median | 20.0 |
| title_length p25 | 17.0 |
| title_length p75 | 24.0 |
| contains_number_rate | 37.6% |
| question_rate | 19.7% |
| judgment_rate | 25.8% |
| contrast_rate | 5.7% |
| company_name_rate | 69.7% |
| archetype_count | 11 |
| run_guard_blocked_rate | 13.9% (237 个样本有报告) |

## archetype 分布（启发式，仅供参考）

| archetype | 条数 |
|---|---:|
| delivery_validation | 482 |
| question_with_premise | 329 |
| other | 225 |
| comparison | 148 |
| trend_change | 98 |
| profit_chain | 97 |
| short_news | 90 |
| judgment | 69 |
| counter_intuitive | 67 |
| industry_change | 56 |
| result | 10 |

## 限制（必须随结论一起读）

- **这不是 CTR 因果证明。** 历史样本混合了发布时间、账号阶段、选题、
  推荐权重、历史时期与模型能力，混杂因素未控制。
- `archetype` 与 question/judgment/contrast 等特征均为**离线启发式正则**，
  只用于观察形态分布，不代表平台真实语义。
- `similarity` 使用字符 2-gram Jaccard + SequenceMatcher 平均，
  无 embedding、无语义理解，近义改写会被低估。
- `largest_cluster_ratio` 阈值固定 0.5（用 9-17 真实塌缩样本校准），换阈值会显著改变数值，所以指标名里带阈值。

## 专项：对照实验的比较目标

- 来源：`.runtime\diag\run_35183027828`
- 有效样本数：**3**

| 指标 | 值 |
|---|---:|
| sample_count | 3 |
| candidate_total | 14 |
| candidate_count mean | 4.67 |
| candidate_count median | 3.0 |
| candidate_count min | 3 |
| candidate_count max | 8 |
| unique_title_count | 11 |
| unique_skeleton_count | 11 |
| skeleton_duplicate_rate | 21.4% |
| similarity mean | 0.342 |
| similarity median | 0.360 |
| similarity max | 0.687 |
| largest_cluster_ratio@0.5 mean | 0.653 |
| largest_cluster_ratio@0.5 max | 0.667 |
| title_length mean | 21.6 |
| title_length median | 20.5 |
| title_length p25 | 20.0 |
| title_length p75 | 25.0 |
| contains_number_rate | 57.1% |
| question_rate | 0.0% |
| judgment_rate | 28.6% |
| contrast_rate | 0.0% |
| company_name_rate | 57.1% |
| archetype_count | 4 |
| run_guard_blocked_rate | 0.0% (2 个样本有报告) |

**观察**：候选数 3–8；`question_rate` = 0.0%、`contrast_rate` = 0.0%；`archetype_count` = 4。与全局基线相比形态更单一时，说明该次运行的标题多样性不足。
