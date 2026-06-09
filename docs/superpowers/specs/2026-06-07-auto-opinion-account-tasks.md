# 全自动观点分享账号 - 任务清单

## 📋 任务总览

按优先级和阶段拆分的实现任务。

---

## 🏗️ 阶段一：最小可行性增强（优先）

### 任务 1.1：扩展数据模型

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 1-2小时 |
| 文件 | `src/toutiao_content_factory/models.py` |

**任务内容**:
- 新增 `InsightReport` dataclass
- 新增 `DataInsightReport` dataclass
- 新增 `MultiSourceReport` dataclass（为未来准备）
- 新增 `HistoricalComparison` dataclass（为未来准备）

**验收标准**:
- 新增的 dataclass 有完整的类型注解
- 实现 `from_dict()` 方法
- 能正常导入和实例化

---

### 任务 1.2：创建提示词模板

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 1-2小时 |
| 文件 | `prompts/` |

**任务内容**:
- 创建 `prompts/insight_prompt.md` - 深度观点生成提示词
- 创建 `prompts/data_mining_prompt.md` - 数据挖掘洞察提示词
- 创建 `prompts/writer_with_insights_prompt.md` - 基于观点的写作提示词

**验收标准**:
- 提示词与现有风格一致
- 包含完整的示例和说明

---

### 任务 1.3：实现 InsightAgent

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 3-4小时 |
| 文件 | `src/toutiao_content_factory/agents/insight_agent.py` |

**任务内容**:
- 创建 `InsightAgent` 类，继承 `BaseAgent`
- 实现 `run()` 方法，接收 extraction、trend_report、data_report、style
- 实现 `_fallback()` 方法，返回默认 InsightReport
- 在 `__init__.py` 中导出

**参考实现**:
- 参考 `DataAgent` 的结构
- 参考 `WriterAgent` 的 prompt 渲染方式

**验收标准**:
- 代码能正常导入
- 与现有 Agent 风格一致
- 有完整的类型注解

---

### 任务 1.4：实现 DataMiningAgent（轻量版）

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 2-3小时 |
| 文件 | `src/toutiao_content_factory/agents/data_mining_agent.py` |

**任务内容**:
- 创建 `DataMiningAgent` 类，继承 `BaseAgent`
- 实现 `run()` 方法
- 实现 `_fallback()` 方法
- 在 `__init__.py` 中导出

**验收标准**:
- 代码能正常导入
- 与现有 Agent 风格一致

---

### 任务 1.5：扩展 WriterAgent

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 2-3小时 |
| 文件 | `src/toutiao_content_factory/agents/writer_agent.py` |

**任务内容**:
- 新增 `run_with_insights()` 方法，接收额外的 `insight_report` 参数
- 更新 `run()` 方法，可选地接收 insights
- 新增 `writer_with_insights_prompt` 的支持

**验收标准**:
- 不破坏现有 `run()` 方法
- 新方法能正常调用

---

### 任务 1.6：配置扩展

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 1小时 |
| 文件 | `src/toutiao_content_factory/config.py`, `config.yaml` |

**任务内容**:
- 在 `config.py` 中新增 `enhanced_insights` 配置项
- 在 `config.yaml` 中新增默认配置（默认关闭）

**验收标准**:
- 配置能正常加载
- 默认值设置合理（先关闭，测试后再开启）

---

### 任务 1.7：集成到工作流

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 3-4小时 |
| 文件 | `src/toutiao_content_factory/services/agent_workflow.py` |

**任务内容**:
- 在 `AgentWorkflowService.__init__` 中实例化新 Agent
- 在 `run()` 方法中增加新 Agent 的调用
- 实现配置开关控制
- 实现异常处理和回退逻辑

**验收标准**:
- 新功能可以通过配置开启/关闭
- 新 Agent 失败时能回退到现有流程
- 不破坏现有工作流

---

### 任务 1.8：第一阶段测试

| 属性 | 值 |
|------|-----|
| 优先级 | 高 |
| 预估工作量 | 2-3小时 |

**任务内容**:
- 测试新 Agent 的独立运行
- 测试工作流集成
- 测试配置开关
- 测试容错回退

**验收标准**:
- 所有测试通过
- 生成的内容质量符合预期

---

## 🚀 阶段二：深度增强（其次）

### 任务 2.1：实现 MultiSourceAgent

| 属性 | 值 |
|------|-----|
| 优先级 | 中 |
| 预估工作量 | 4-6小时 |

---

### 任务 2.2：实现 HistoricalAgent

| 属性 | 值 |
|------|-----|
| 优先级 | 中 |
| 预估工作量 | 4-6小时 |

---

### 任务 2.3：实现 HistoricalInsightService

| 属性 | 值 |
|------|-----|
| 优先级 | 中 |
| 预估工作量 | 3-4小时 |

---

### 任务 2.4：数据库扩展

| 属性 | 值 |
|------|-----|
| 优先级 | 中 |
| 预估工作量 | 2-3小时 |

---

## 🛠️ 阶段三：工具集成（可选）

### 任务 3.1：集成 Jina Reader

| 属性 | 值 |
|------|-----|
| 优先级 | 低 |
| 预估工作量 | 2-3小时 |

---

### 任务 3.2：集成 Firecrawl / Crawl4AI

| 属性 | 值 |
|------|-----|
| 优先级 | 低 |
| 预估工作量 | 3-4小时 |

---

### 任务 3.3：集成 Hugging Face

| 属性 | 值 |
|------|-----|
| 优先级 | 低 |
| 预估工作量 | 3-4小时 |

---

## 📊 工作量预估

| 阶段 | 预估工时 |
|------|---------|
| 阶段一（MVP） | 15-20小时 |
| 阶段二（深度） | 13-19小时 |
| 阶段三（工具） | 8-11小时 |
| **总计** | **36-50小时** |

---

## 📝 备注

- 阶段一可以独立完成并测试
- 阶段一完成后即可看到效果
- 后续阶段可以逐步推进
- 每个任务都应该有独立的测试
