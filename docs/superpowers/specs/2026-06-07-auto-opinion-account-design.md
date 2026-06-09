# 全自动观点分享账号 - 系统增强设计方案

## 📋 项目概述

基于现有的 AI Content Factory 系统，增强其深度观点生成能力，实现一个全自动运营的观点分享账号。

### 核心目标
- **数据驱动** - 每个观点都有数据支撑
- **多源验证** - 交叉验证不同信源，发现被忽视的信号
- **历史视角** - 与历史事件对比，发现规律
- **独特洞察** - 差异化的分析角度

---

## 🏗️ 系统架构

### 现有架构回顾

```
选题 → 数据提取 → 写作 → 润色 → 发布
  ↓        ↓         ↓      ↓      ↓
Trend   Data    Writer  Polish  Publish
Agent   Agent   Agent   Agent   Service
```

### 增强后架构

```
多源采集 → 交叉验证 → 数据挖掘 → 历史对比 → 深度观点生成 → 写作 → 润色 → 发布
   ↓          ↓         ↓         ↓           ↓           ↓       ↓       ↓
Multi    Multi    DataMining Historical  Insight    Writer  Polish  Publish
Source   Source    Agent    Agent     Agent     Agent   Agent   Service
Agent    Service
```

---

## 📦 核心模块设计

### 1️⃣ MultiSourceAgent - 多源信息聚合Agent

**文件位置**: `src/toutiao_content_factory/agents/multi_source_agent.py`

**核心功能**:
- 扩展信源（科技媒体、社交媒体、行业报告、官方公告）
- 交叉验证不同信源的信息一致性
- 发现信源之间的差异点和争议点
- 提取"被忽视的信号"

**数据模型** (新增到 `models.py`):
```python
@dataclass
class MultiSourceReport:
    """多源信息聚合报告"""
    primary_sources: list[SourceDocument]
    secondary_sources: list[SourceDocument]
    consensus_points: list[str]  # 共识点
    controversy_points: list[str]  # 争议点
    overlooked_signals: list[str]  # 被忽视的信号
    source_credibility: dict[str, float]  # 信源可信度
    synthesis_summary: str  # 综合摘要
```

**输出到工作流**:
- 在 DataAgent 之前增加 MultiSourceAgent
- 将 MultiSourceReport 注入 DataAgent

---

### 2️⃣ InsightAgent - 深度观点生成Agent

**文件位置**: `src/toutiao_content_factory/agents/insight_agent.py`

**核心功能**:
- 基于数据生成观点假设
- 多角度分析（技术、商业、行业、用户）
- 构建"观点-证据"的逻辑链
- 生成差异化角度

**数据模型** (新增到 `models.py`):
```python
@dataclass
class InsightReport:
    """深度洞察报告"""
    main_insight: str  # 核心观点
    supporting_data: list[str]  # 支撑数据
    alternative_angles: list[str]  # 替代角度
    counterarguments: list[str]  # 反论点
    prediction: str  # 预测/展望
    confidence_score: float  # 置信度
```

**与现有系统集成**:
- 在 DataAgent 之后、WriterAgent 之前运行
- 为 WriterAgent 提供观点框架

---

### 3️⃣ HistoricalAgent - 历史对比分析Agent

**文件位置**: `src/toutiao_content_factory/agents/historical_agent.py`

**核心功能**:
- 检索历史类似事件
- 对比分析当前与历史
- 提取历史规律
- 基于历史预测未来

**数据模型** (新增到 `models.py`):
```python
@dataclass
class HistoricalComparison:
    """历史对比报告"""
    historical_events: list[dict]  # 历史事件
    similarities: list[str]  # 相似点
    differences: list[str]  # 差异点
    historical_outcomes: list[str]  # 历史结果
    historical_insights: list[str]  # 历史教训
    future_prediction: str  # 基于历史的预测
```

---

### 4️⃣ DataMiningAgent - 数据挖掘洞察Agent

**文件位置**: `src/toutiao_content_factory/agents/data_mining_agent.py`

**核心功能**:
- 从新闻中提取量化数据
- 数据清洗和验证
- 数据趋势分析
- 数据驱动的结论

**数据模型** (新增到 `models.py`):
```python
@dataclass
class DataInsightReport:
    """数据洞察报告"""
    extracted_metrics: list[dict]  # 提取的指标
    trends: list[str]  # 趋势分析
    anomalies: list[str]  # 异常发现
    correlations: list[str]  # 相关性分析
    data_conclusions: list[str]  # 数据结论
```

---

## 🛠️ 配套服务模块

### 1. HistoricalInsightService - 历史观点库服务

**文件位置**: `src/toutiao_content_factory/services/historical_insight_service.py`

**核心功能**:
- 存储历史观点库
- 向量检索相似历史
- 观点演化追踪
- 历史对比查询

**数据库扩展** (新增到 `database/models.py`):
```python
class HistoricalInsight(SQLModel, table=True):
    """历史洞察记录"""
    __tablename__: str = "historical_insights"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    insight_text: str = Field(sa_type=Text)
    topic_title: str = Field(index=True)
    topic_keywords: str = Field(sa_type=Text)  # JSON list
    embedding: str = Field(sa_type=Text)  # 向量嵌入
    created_at: datetime = Field(default_factory=datetime.now)
    account_key: str = Field(index=True)
    
    # 性能数据
    views: int = Field(default=0)
    engagement: float = Field(default=0.0)
```

---

### 2. MultiSourceService - 多源验证服务

**文件位置**: `src/toutiao_content_factory/services/multi_source_service.py`

**核心功能**:
- 多信源内容获取
- 信源可信度评分
- 信息交叉验证
- 争议点识别

**集成工具**:
- Firecrawl / Crawl4AI - 智能网页抓取
- Jina Reader - 网页内容提取

---

### 3. DataMiningService - 数据挖掘服务

**文件位置**: `src/toutiao_content_factory/services/data_mining_service.py`

**核心功能**:
- 数值数据提取
- 数据清洗和验证
- 趋势分析
- 相关性发现

**集成工具**:
- Hugging Face Transformers - NLP分析
- 本地LLM（可选）- DeepSeek/Qwen 深度推理

---

### 4. 扩展的 TopicCollectionService

**文件位置**: `src/toutiao_content_factory/services/topic_collection.py`

**扩展内容**:
- 增加更多RSS源
- 社交媒体监控（可选）
- 行业报告采集
- 官方公告追踪

---

## 🔄 增强后的工作流

### 完整工作流步骤

```python
# 1. 多源采集
multi_source_agent = MultiSourceAgent(...)
multi_source_report = multi_source_agent.run(extraction, style)

# 2. 数据挖掘
data_mining_agent = DataMiningAgent(...)
data_insight_report = data_mining_agent.run(extraction, multi_source_report)

# 3. 历史对比
historical_agent = HistoricalAgent(...)
historical_comparison = historical_agent.run(extraction, data_insight_report)

# 4. 深度观点生成
insight_agent = InsightAgent(...)
insight_report = insight_agent.run(
    multi_source_report,
    data_insight_report,
    historical_comparison,
    style
)

# 5. 现有流程（数据提取、写作等）
data_agent = DataAgent(...)
data_report = data_agent.run_with_insights(
    extraction, trend_report, style, insight_report
)

writer_agent = WriterAgent(...)
content = writer_agent.run_with_insights(
    trend_report, data_report, insight_report, style
)

# 6. 后续流程（润色、标题、发布）...
```

---

## 📝 新增提示词模板

| 文件 | 用途 |
|------|------|
| `prompts/multi_source_prompt.md` | 多源信息聚合 |
| `prompts/insight_prompt.md` | 深度观点生成 |
| `prompts/historical_prompt.md` | 历史对比分析 |
| `prompts/data_mining_prompt.md` | 数据挖掘洞察 |
| `prompts/writer_with_insights_prompt.md` | 基于观点的写作 |

---

## 🏗️ 数据库扩展

### 新增表

1. **HistoricalInsights** - 历史观点库
2. **SourceCredibility** - 信源可信度评分
3. **DataExtractionLog** - 数据提取日志（可选）

### 现有表增强

| 表名 | 新增字段 |
|------|----------|
| Article | `insight_report` (JSON) |
| Topic | `multi_source_summary` (JSON) |

---

## 🚀 实现路径（按阶段）

### 第一阶段：最小可行性增强（快速见效）

**目标**: 快速增强观点生成能力

1. ✅ 实现 InsightAgent
2. ✅ 实现 DataMiningAgent（轻量版）
3. ✅ 扩展 WriterAgent 支持观点输入
4. ✅ 新增提示词模板
5. ✅ 测试集成

### 第二阶段：深度增强

**目标**: 多源验证 + 历史对比

1. 实现 MultiSourceAgent
2. 实现 HistoricalAgent
3. 实现 HistoricalInsightService
4. 集成向量检索
5. 数据库扩展

### 第三阶段：工具集成

**目标**: 集成开源工具

1. 集成 Firecrawl/Crawl4AI
2. 集成 Jina Reader
3. 集成 Hugging Face
4. 性能优化

---

## 📊 工作流变更详情

### AgentWorkflowService 变更

**文件**: `src/toutiao_content_factory/services/agent_workflow.py`

**变更点**:
- 新增 Agent 实例化
- 调整工作流顺序
- 传递增强数据

**新增代码结构**:
```python
# 新增初始化
self.multi_source_agent = MultiSourceAgent(...)
self.data_mining_agent = DataMiningAgent(...)
self.historical_agent = HistoricalAgent(...)
self.insight_agent = InsightAgent(...)

# 新增工作流步骤
multi_source_report = self.multi_source_agent.run(...)
data_insight_report = self.data_mining_agent.run(...)
historical_comparison = self.historical_agent.run(...)
insight_report = self.insight_agent.run(...)

# 传递到 WriterAgent
content = self.writer_agent.run_with_insights(..., insight_report, ...)
```

---

## 🎯 核心设计原则

1. **向后兼容** - 不破坏现有功能
2. **增量增强** - 可逐步启用新功能
3. **配置驱动** - 通过配置控制启用哪些增强
4. **容错设计** - 新功能失败时回退到现有流程

---

## 🔧 配置扩展

新增配置项（到 `config.yaml` 和 `config.py`）:

```yaml
# 观点增强功能开关
enhanced_insights:
  enabled: true
  multi_source: true
  historical_comparison: true
  data_mining: true

# 多源采集配置
multi_source:
  max_sources: 5
  credibility_threshold: 0.7

# 历史观点库
historical_insights:
  enabled: true
  vector_db: "sqlite"  # 或 chroma
  similarity_threshold: 0.8

# 工具集成
tools:
  firecrawl:
    enabled: false
    api_key: ""
  jina_reader:
    enabled: true
    api_url: "https://r.jina.ai"
  hugging_face:
    enabled: true
    model: "Qwen/Qwen2.5-7B-Instruct"
```

---

## 📈 预期效果

| 维度 | 现有系统 | 增强后 |
|------|---------|--------|
| 观点独特性 | 中等 | 高 |
| 数据支撑 | 基础 | 强 |
| 多角度分析 | 有限 | 丰富 |
| 历史视角 | 无 | 有 |
| 信源验证 | 无 | 有 |

---

## 🧪 测试策略

1. **单元测试** - 新增 Agent 独立测试
2. **集成测试** - 工作流端到端测试
3. **A/B测试** - 对比现有和增强的内容质量
4. **历史回测** - 用历史数据验证预测准确性

---

## 📚 参考文档

- 现有Agent实现: `src/toutiao_content_factory/agents/`
- 现有工作流: `src/toutiao_content_factory/services/agent_workflow.py`
- 数据库模型: `src/toutiao_content_factory/database/models.py`
- 提示词模板: `prompts/`
