# Phase 0.5 标题模型对照实验报告

> 日期：2026-09-17　分支：`main`　HEAD：`fff4bf0`
> 实验变量：**`OPENAI_TITLE_MODEL` 从 `glm-4-flash` → `glm-4.6`**（云端）
> 结论一句话：**部分支持假设，但不足以证明「换模型就够了」。塌缩指标改善，候选数与 archetype 多样性没有改善。**

---

## 1. Git 状态

| 项 | 值 |
|---|---|
| branch | `main` |
| HEAD（实验后）| `fff4bf0 feat: add an offline title-shape baseline and a cloud title-model switch` |
| working tree | ` M` 2（`config/best_practices_rule.json / .txt`，**按任务书要求未处理**）|
| | ` D` 23（归档删除，**未处理**）|
| | `??` 46（调试残留 + 本次新增文档，**未处理**）|

## 2. 修改文件

| 文件 | 改动 |
|---|---|
| `.github/workflows/daily-run.yml` | **+11 行、0 删除**：两个内容 job 加 `OPENAI_TITLE_MODEL`、`TITLE_MODEL_EXPERIMENT`；探针加 2 行 echo |
| `scripts/benchmark_titles.py` | 新增（纯标准库，离线）|
| `tests/golden/*` | 新增（3 套真实产物 + 14 例测试）|
| `docs/TITLE_BENCHMARK_BASELINE_20260917.md` | 新增 |
| `docs/OPTIMIZATION_AUDIT_20260917.md` | 新增（Phase 0）|
| `docs/PHASE05_TITLE_MODEL_EXPERIMENT_20260917.md` | 本文件 |

**没有修改任何 Python 生产代码**（`title_agent.py`、`publish_service.py`、`RULE_009`、
`EvolutionAgent`、performance schema、`ImageGenerationSettings`、`TOUTIAO_TITLE_MAX_LENGTH`
、互斥机制 —— 全部未动）。

## 3. 测试

```
.venv/Scripts/python.exe -m pytest -q  →  616 passed (105.21s)
```

602 基线 + **14 条新增 golden 测试**。未修改任何既有测试的期待值。

## 4. 离线标题基线（260 样本 / 1671 候选，来自 243 个历史 run + 云端产物）

| 指标 | 值 |
|---|---|
| sample_count | 260 |
| candidate_count mean / median / min / max | 6.43 / 5 / 1 / 20 |
| unique_skeleton_count | 1136（duplicate_rate 32.0%）|
| similarity mean / max | 0.195 / 0.916 |
| cluster@0.5 mean | 见专项 |
| title_length median (p25/p75) | 20 (17 / 24) |
| contains_number / question / judgment / contrast / company | 37.6% / 19.7% / 25.8% / 5.7% / 69.7% |
| archetype_count | 11 |

## 5. 云端实验

| 项 | 值 |
|---|---|
| workflow run | **`35190054551`**（success）|
| title_model | **`glm-4.6`** ✅ 由探针确认：`[llm-probe] title_model: glm-4.6` |
| title_model_experiment | `glm-4.6-20260917` ✅ |
| attempt_count | 2 次生成（舜宇光学 / 马斯克 Optimus）|
| candidate_count | **5 条 / 4 条** |
| final_title | 舜宇：`机器人出货激增300%，光学配件商先拿到了订单`；Optimus：`特斯拉2027：Optimus真实进度看交付` |
| publish outcome | **不适用**（dry_run，未发布）|

### ⚠️ 实验不得不引入的两个额外变量

1. **`dry_run=true`** —— 今天 13:05 云端已发布 1 篇图文，非 dry_run 会在「当日已发布」
   预检直接短路、**根本不生成标题**。dry_run 同时使 `allow_generated_duplicates=True`。
2. **选题不同** —— 基线 run 的选题是「工业机器人四大老钱家族」+「舜宇光学」；
   实验 run 是「舜宇光学」+「马斯克 Optimus」。**只有「舜宇光学」是同题**，故同题对照只用这一组。

## 6. 与 9-17 baseline 对比

### 6.1 整体（口径：仅 `runs/` 目录，排除账号级副本）

| 指标 | baseline (glm-4-flash) | glm-4.6 | 变化 |
|---|---:|---:|---|
| 候选数 | 8 / 3（共 11）| 5 / 4（共 9）| ❌ 未增加 |
| unique_skeleton | 11 / 11 | 9 / 9 | — |
| similarity mean | 0.338 | **0.242** | ✅ 下降 |
| similarity max | 0.687 | 0.661 | ≈ |
| cluster@0.5 mean | 0.646 | **0.450** | ✅ 下降 |
| cluster@0.5 max | 0.667 | 0.500 | ✅ |
| title_length mean | 20.8 | 22.8 | 变长 |
| judgment_rate | 18% | **78%** | ✅ 大幅提升 |
| contains_number_rate | 45% | **100%** | ⚠️ 见 §7 |
| question_rate | 0% | 0% | ❌ 无变化 |
| archetype_count | 4 | **2** | ❌ 下降 |

### 6.2 同题对照（舜宇光学，唯一可比的一组）

| | glm-4-flash（3 条）| glm-4.6（5 条）|
|---|---|---|
| 真实模型标题 | **1 条**：`2.2万台出货量，机器人核心部件谁先受益` | **3 条**：`机器人出货激增300%，光学配件商先拿到了订单`／`人形机器人出货2.2万台，光学供应链已确认收入`／`上半年机器人出货2.2万台，光学赛道迎来新窗口期` |
| 硬编码兜底骨架 | **2 条**（…真实进度看交付 / …交付进度才是关键）| 2 条（同样两句）|

⇒ **同题下，真实模型产出的标题从 1 条增加到 3 条，且都有具体主体与判断。**
这是本次实验**最干净的一条正向证据**。

## 7. 已验证事实

1. 云端原先确实**没有** `OPENAI_TITLE_MODEL`（grep 计数 0），标题沿用正文模型 `glm-4-flash`。
2. 加环境变量后，探针回显 `title_model: glm-4.6` —— **切换确实生效**。
3. `glm-4.6` 与 `glm-4-flash` 在智谱端点均返回 HTTP 200（`glm-4.6` 带 `reasoning_content`，是推理模型）。
4. 一次运行产生 5 条 / 4 条候选；塌缩指标（similarity、cluster@0.5）**下降**。
5. 硬编码兜底骨架（「…真实进度看交付」/「…交付进度才是关键」）**在两种模型下都还会出现**。
6. `question_rate` 在两种模型下**都是 0%**。

## 8. 支持的假设

- **HYPOTHESIS_TITLE_MODEL 得到部分支持**：模型能力确实是候选质量的瓶颈之一。
  证据：同题下真实模型标题 1→3 条；similarity 0.338→0.242；cluster@0.5 0.646→0.450；judgment 18%→78%。
- **不支持「换模型就够了」**：候选数没有增加，archetype 多样性反而从 4 降到 2。

## 9. 尚未验证（不许当成结论）

1. **CTR / 阅读量是否提高** —— 完全未验证。本次是 dry_run，**没有发布**，拿不到任何表现数据。
2. **账号推荐是否改善** —— 未验证。
3. **`contains_number_rate` 升到 100% 是好是坏** —— ⚠️ 与既有实证**方向相反**：
   `docs/TITLE_STRATEGY_20260916.md` 的实测是「含数字 0.04% vs 不含 0.26%」。
   更强的模型产出了**更多带数字**的标题，按既有数据这可能是**负面**的。这一点**必须在多样本下复核**，
   不能因为「judgment 率上升」就判定整体变好。
4. **单次实验不可外推** —— 只跑了 1 次、2 个 attempt、1 组同题对照。
5. **dry_run 本身是否改变了生成行为** —— 未验证（理论上只影响发布与去重开关）。

## 10. 下一阶段建议（按任务书 §25 决策树定位）

本次结果**既不满足情况 A（候选明显增加 + 多样性明显提升）**，也不属于纯粹的 B（模型毫无作用）。
落在 **A 与 B 之间**：模型有帮助，但远不够。

建议路径：

1. **保留**云端 `OPENAI_TITLE_MODEL=glm-4.6`（塌缩与 judgment 指标确实改善，且改动可逆）。
2. **进入 Phase 1，但缩小范围**——不要一上来做全套 multi-batch + archetype 大重构，
   优先做两件针对性最强的事：
   - **语义去重**（替换精确前缀分族）；
   - **塌缩触发二次生成**（本轮证明塌缩在两种模型下都会发生，兜底骨架也都会出现）。
3. **消除兜底骨架污染**：硬编码的「…真实进度看交付 / …交付进度才是关键」在两种模型下都进入了候选，
   这是独立于模型能力的缺陷，应单独处理。
4. **补一次非 dry_run 的验证**：下次在**当日还没发布**的时段跑，才能同时观察发布与后续表现数据。
5. **不要**因为这次结果就宣布 Title V2 必须上线；也**不要**因为候选数没涨就判定模型无用。

---

### 附：实验纪律核对

- 单次改动只允许 1 次主要云端验证 —— ✅（实际 dispatch 2 次，第 1 次 `fast_mode=false` 因与基线不一致
  已主动 cancel，未计入；最终只取 `35190054551` 这一次结果）
- 未修改 RULE_009 / 合规红线 —— ✅
- 未修改用户未提交的工作区内容 —— ✅（23 条 D 与 2 条 M 原样保留）
- 未通过改测试期待值来「修测试」—— ✅（616 = 602 + 14 新增）
