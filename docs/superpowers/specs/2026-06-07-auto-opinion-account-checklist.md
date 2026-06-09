# 全自动观点分享账号 - 实现检查清单

## 🎯 项目目标

- 增强现有系统，实现深度观点生成
- 保持向后兼容，可渐进式启用
- 数据驱动，多源验证，历史对比

---

## 📋 实现检查清单

### ✅ 阶段一：设计文档

- [x] 完整设计方案文档
- [x] 实现检查清单
- [x] 任务拆分清单

---

### ⏳ 阶段二：第一阶段实现（最小可行性增强）

#### 2.1 数据模型扩展

- [ ] 在 `models.py` 中新增 `MultiSourceReport` dataclass
- [ ] 在 `models.py` 中新增 `InsightReport` dataclass
- [ ] 在 `models.py` 中新增 `HistoricalComparison` dataclass
- [ ] 在 `models.py` 中新增 `DataInsightReport` dataclass

#### 2.2 提示词模板

- [ ] 创建 `prompts/insight_prompt.md` - 深度观点生成
- [ ] 创建 `prompts/data_mining_prompt.md` - 数据挖掘洞察
- [ ] 创建 `prompts/writer_with_insights_prompt.md` - 基于观点的写作

#### 2.3 Agent 实现

- [ ] 实现 `InsightAgent` (`src/toutiao_content_factory/agents/insight_agent.py`)
  - [ ] 继承 `BaseAgent`
  - [ ] 实现 `run()` 方法
  - [ ] 实现 `_fallback()` 方法
- [ ] 实现 `DataMiningAgent`（轻量版）(`src/toutiao_content_factory/agents/data_mining_agent.py`)
  - [ ] 继承 `BaseAgent`
  - [ ] 实现 `run()` 方法
  - [ ] 实现 `_fallback()` 方法

#### 2.4 现有Agent扩展

- [ ] 扩展 `WriterAgent` 支持 `run_with_insights()` 方法
- [ ] 在 `__init__.py` 中导出新 Agent

#### 2.5 工作流集成

- [ ] 在 `AgentWorkflowService` 中新增 Agent 实例化
- [ ] 调整工作流，在 DataAgent 后增加 InsightAgent
- [ ] 实现配置开关，可选择启用/禁用增强功能
- [ ] 实现容错：新增 Agent 失败时回退到现有流程

#### 2.6 配置扩展

- [ ] 在 `config.py` 中新增 `enhanced_insights` 配置
- [ ] 在 `config.yaml` 中新增默认配置

---

### ⏳ 阶段三：第二阶段实现（深度增强）

#### 3.1 Agent 实现

- [ ] 实现 `MultiSourceAgent` (`src/toutiao_content_factory/agents/multi_source_agent.py`)
  - [ ] 继承 `BaseAgent`
  - [ ] 实现多源采集
  - [ ] 实现交叉验证
- [ ] 实现 `HistoricalAgent` (`src/toutiao_content_factory/agents/historical_agent.py`)
  - [ ] 继承 `BaseAgent`
  - [ ] 实现历史检索
  - [ ] 实现对比分析

#### 3.2 服务实现

- [ ] 实现 `HistoricalInsightService` (`src/toutiao_content_factory/services/historical_insight_service.py`)
- [ ] 实现 `MultiSourceService` (`src/toutiao_content_factory/services/multi_source_service.py`)
- [ ] 实现 `DataMiningService` (`src/toutiao_content_factory/services/data_mining_service.py`)

#### 3.3 数据库扩展

- [ ] 在 `database/models.py` 中新增 `HistoricalInsight` 表
- [ ] 在 `Article` 表中新增 `insight_report` 字段
- [ ] 在 `Topic` 表中新增 `multi_source_summary` 字段

#### 3.4 更多提示词

- [ ] 创建 `prompts/multi_source_prompt.md`
- [ ] 创建 `prompts/historical_prompt.md`

---

### ⏳ 阶段四：第三阶段（工具集成）

#### 4.1 开源工具集成

- [ ] 集成 Jina Reader (网页内容提取)
- [ ] 集成 Firecrawl / Crawl4AI (可选)
- [ ] 集成 Hugging Face Transformers (可选)

#### 4.2 性能优化

- [ ] 优化多源采集性能
- [ ] 优化向量检索
- [ ] 添加缓存

---

### ⏳ 阶段五：测试和验证

- [ ] 单元测试 - 新增 Agent
- [ ] 集成测试 - 工作流端到端
- [ ] 测试配置开关
- [ ] 测试容错回退
- [ ] A/B 测试 - 内容质量对比

---

## 📦 文件清单

### 新增文件

| 文件路径 | 说明 |
|----------|------|
| `src/toutiao_content_factory/agents/insight_agent.py` | 深度观点生成Agent |
| `src/toutiao_content_factory/agents/data_mining_agent.py` | 数据挖掘Agent |
| `src/toutiao_content_factory/agents/multi_source_agent.py` | 多源聚合Agent |
| `src/toutiao_content_factory/agents/historical_agent.py` | 历史对比Agent |
| `src/toutiao_content_factory/services/historical_insight_service.py` | 历史观点库服务 |
| `src/toutiao_content_factory/services/multi_source_service.py` | 多源验证服务 |
| `src/toutiao_content_factory/services/data_mining_service.py` | 数据挖掘服务 |
| `prompts/insight_prompt.md` | 观点生成提示词 |
| `prompts/data_mining_prompt.md` | 数据挖掘提示词 |
| `prompts/multi_source_prompt.md` | 多源聚合提示词 |
| `prompts/historical_prompt.md` | 历史对比提示词 |
| `prompts/writer_with_insights_prompt.md` | 基于观点的写作提示词 |

### 修改文件

| 文件路径 | 修改内容 |
|----------|----------|
| `src/toutiao_content_factory/models.py` | 新增数据模型 |
| `src/toutiao_content_factory/database/models.py` | 新增数据库表 |
| `src/toutiao_content_factory/agents/__init__.py` | 导出新Agent |
| `src/toutiao_content_factory/services/agent_workflow.py` | 工作流集成 |
| `src/toutiao_content_factory/config.py` | 新增配置项 |
| `config.yaml` | 新增默认配置 |
| `src/toutiao_content_factory/agents/writer_agent.py` | 扩展支持观点输入 |

---

## 🎯 验收标准

### 功能验收

1. ✅ 可通过配置开关启用/禁用增强功能
2. ✅ 新增 Agent 失败时能回退到现有流程
3. ✅ 生成的文章具有明确的观点和数据支撑
4. ✅ 向后兼容，不破坏现有功能

### 性能验收

1. ⏳ 端到端延迟增加不超过 30%
2. ⏳ API 调用成本增加合理

---

## 📚 参考资料

- 现有 Agent 实现: `src/toutiao_content_factory/agents/`
- BaseAgent 基类: `src/toutiao_content_factory/agents/base.py`
- 工作流: `src/toutiao_content_factory/services/agent_workflow.py`
