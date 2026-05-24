# SkillMash 全自动 Slot 表征提取设计（Demo V1）

- 日期：2026-05-24
- 范围：`OUTPUT/representation` 生成链路（表征提取与归一化）
- 目标：全自动提取 `emits_slots/consumes_slots`，为后续混合图编排提供稳定输入

## 1. 背景与问题

当前 representation 已能稳定抽取 `id/name/tasks/inputs/outputs`，但在“多路评审 -> 汇总”与“生成 -> 审查 -> 汇总”场景中，核心结构信号缺失：

1. `emits_slots` / `consumes_slots` 基本为空，无法表达跨 Skill 的逻辑槽位协作关系。
2. 输出语义过度收敛到通用 `report`，导致图构建阶段难以区分“中间 findings”和“最终文档”。
3. 单靠 `can_feed` 与泛化输入（例如 `context`）难以组织稳定的多阶段候选计划。

因此需要在表征提取阶段新增全自动 slot 抽取，并在落盘前进行确定性门控，确保“少填不乱填”。

## 2. 目标与非目标

### 2.1 目标

1. 在 32 个 Software Development 技能上全自动生成 `emits_slots/consumes_slots`。
2. 保持高精度优先：低置信候选允许直接置空。
3. 产物可解释：每条候选去留都有诊断理由。
4. 为后续混合图提供可消费的结构化 slot 信号。

### 2.2 非目标

1. 本阶段不做运行时执行器改造。
2. 不做历史数据兼容，直接采用新字段结构。
3. 不引入开放式 slot taxonomy，父类集合固定为首版白名单。

## 3. 方案概览

采用“双阶段混合”策略：

1. **LLM 候选生成阶段**
   - 从 `description/tasks/inputs/outputs` 生成 slot 候选。
   - 候选包含：`name/parent/confidence/evidence/source/kind`。
2. **Deterministic 门控阶段**
   - 严格按固定规则裁剪与归一化。
   - 低置信、非法父类、方向冲突、歧义冲突候选直接丢弃。
3. **产物落盘阶段**
   - `representations.json` 仅保留通过门控的 slots。
   - `diagnostics.json` 写入候选去留轨迹与失败码。

## 4. 数据契约（V1）

### 4.1 字段结构

`emits_slots` 与 `consumes_slots` 统一为对象数组，结构如下：

```json
{
  "name": "api_contract_security_findings",
  "parent": "security_findings",
  "confidence": 0.86,
  "source": "llm_infer_v1",
  "status": "accepted",
  "evidence": "reviews API contracts from security perspectives"
}
```

### 4.2 必填字段

1. `name`：子槽位名（`snake_case`）
2. `parent`：父类名（必须命中白名单）
3. `confidence`：`0.0 ~ 1.0`
4. `source`：`llm_infer_v1 | rule_infer_v1`
5. `status`：门控结果状态码

`evidence` 为可选字段，但建议保留用于可解释与调试。

### 4.3 父类白名单（固定）

1. `requirements_review_findings`
2. `design_review_findings`
3. `security_findings`
4. `test_findings`
5. `delivery_brief`

## 5. 门控规则（固定顺序）

参数默认值：

- `slot_confidence_threshold = 0.80`
- `parent_conflict_margin = 0.05`
- `max_slots_per_kind = 3`

规则顺序如下：

1. `schema gate`
   - 缺 `name/parent/confidence/source` 任一字段 -> 丢弃
2. `parent gate`
   - `parent` 不在白名单 -> 丢弃
3. `confidence gate`
   - `confidence < 0.80` -> 丢弃（低置信置空）
4. `direction gate`
   - `emits/consumes` 与语义方向冲突 -> 丢弃
5. `duplicate gate`
   - 同技能同 kind 同名候选：保留最高置信
6. `parent conflict gate`
   - 同名不同 parent：若最高分与次高分差值 < 0.05，全部丢弃；否则保留最高置信 parent
7. `cardinality gate`
   - 每技能每 kind 最多保留 3 条，超出部分按置信度截断
8. `role-shape gate`（告警）
   - 评审/审计类缺少 emits -> warning
   - 汇总/编排类缺少 consumes -> warning

## 6. 状态码与诊断

### 6.1 状态码

1. `accepted`
2. `dropped_invalid_schema`
3. `dropped_invalid_parent`
4. `dropped_low_confidence`
5. `dropped_direction_mismatch`
6. `dropped_duplicate`
7. `dropped_parent_conflict`
8. `dropped_ambiguous_parent`
9. `dropped_overflow`

### 6.2 诊断落盘要求

每条候选必须记录：

1. `skill_id`
2. `kind`（`emits|consumes`）
3. 候选快照（`name/parent/confidence`）
4. `status`
5. `reason`

这使得后续阈值调优可回放，不需要人工逐条追查。

## 7. 首版验收标准

### 7.1 功能正确性

1. 32 个技能全部生成新结构 `emits_slots/consumes_slots` 字段（可为空数组）。
2. 低置信候选按阈值被稳定剔除，无隐式放行。
3. 非法 parent 不进入最终 representation。

### 7.2 质量指标

1. 核心评审/汇总技能（例如 `prd-review-team`、`api-design-review-team`、`security-audit-team`、`testing-pyramid-team`、`wcag-audit-team`、`wisedev-team`）的 slot 非空率显著高于当前基线。
2. “仅输出泛化 report 的评审技能”比例下降（通过 emits slot 补充结构语义）。
3. 诊断中 `dropped_*` 原因分布合理，主因集中于低置信与语义冲突，而非 schema 错误。

### 7.3 对后续编排的可用性

至少能支持以下结构组织能力：

1. 多路评审节点可通过 findings 父类聚合到汇总节点。
2. 生成节点与审查节点可通过 slot/工件形成可解释衔接。
3. 计划可明确区分“结构可连通”与“输入可执行”。

## 8. 风险与缓解

1. **风险：LLM 候选漂移过大**
   - 缓解：严格 parent 白名单 + 高阈值 + 冲突全丢策略
2. **风险：召回下降**
   - 缓解：先保 precision，后续通过回放诊断增量调优阈值与 prompt
3. **风险：槽位命名过拟合场景**
   - 缓解：限制命名规范，禁止业务专名，强调父类复用

## 9. 落地范围（Demo V1）

本设计仅要求改动表征提取链路：

1. 抽取 prompt 与返回 schema
2. normalizer 门控逻辑
3. diagnostics 结构
4. representation 落盘结构

不包含 graph builder、online orchestrator 与 runtime executor 的实现改造。

## 10. 下一步（实现计划输入）

本 spec 经确认后，进入实现计划阶段时应拆成四个任务包：

1. 候选生成与 schema 扩展
2. 门控器实现与参数配置
3. 诊断落盘与统计指标
4. 核心技能验收回归（基于 32 技能样本）
