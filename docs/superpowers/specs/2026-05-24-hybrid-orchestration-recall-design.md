# SkillMash 轻量混合图召回增强设计

- 日期：2026-05-24
- 适用范围：SkillMash 离线图构建与在线编排召回链路
- 目标基线：通用、多阶段、可解释、执行前严格验证

## 1. 背景

当前在线编排主要依赖高置信 `can_feed` 边扩路。这对“明确数据流”的链路有效，但对“多路评审 -> 汇总”与“生成 -> 审查 -> 汇总”这类流程覆盖不足。真实问题不是排序器把错的计划排到前面，而是图本身经常缺少可走的结构边，导致系统退化成单点 Skill 推荐。

当前场景只是一个样例：

- 输入：`PRD + API 文档 + UI 原型 + 技术约束`
- 预期：组织多路评审、形成汇总，再经过质量门禁与上线摘要

本设计不为该场景硬编码 workflow，而是抽象出一组跨场景可复用的节点和关系，让系统在更多“多阶段协作”任务中都能连得起来。

## 2. 目标与非目标

### 2.1 目标

1. 提升多阶段计划的召回连通率，降低“只有单点 Skill 推荐”的退化概率。
2. 保持模型层抽象稳定，不把具体业务流程写死进 schema。
3. 让召回增强与执行正确性分层：召回可以宽，执行必须严。
4. 同时支持两类主形态：
   - 多路评审 -> 汇总
   - 生成 -> 审查 -> 汇总

### 2.2 非目标

1. 本阶段不重写执行器，不直接解决最终答案生成或 Skill runtime 调度。
2. 不升级成开放式知识图谱，只做受约束的轻量混合图。
3. 不为单一场景写死模板化分支规则。
4. 不用弱边直接冒充强输入闭合证明。

## 3. 核心方案

### 3.1 图模型升级

将当前纯 `Skill -> Skill` 图升级为受约束的三类节点混合图：

1. `Skill`
   - 现有 Skill 表征节点
   - 保留 `tasks`、`inputs`、`outputs`、`description`
2. `Artifact`
   - 明确工件，如 `prd`、`api_spec`、`source_path`、`review_report`
3. `Slot`
   - 逻辑协作槽位，不要求对应单一文件
   - 第一阶段以评审维度为主，例如：
     - `requirements_review_findings`
     - `design_review_findings`
     - `security_findings`
     - `test_findings`
     - `delivery_brief`

`Slot` 的职责是承接“围绕同一逻辑结果协作”的关系，避免把这些语义扭曲成脆弱的 Skill-Skill 直连。

### 3.2 关系分层

第一阶段只引入 4 类核心关系：

1. `produces`
   - `Skill -> Artifact/Slot`
   - 表示某个 Skill 产出明确工件或逻辑槽位结果
2. `consumes`
   - `Artifact/Slot -> Skill`
   - 表示某个 Skill 需要消费该工件或槽位
3. `depends_on`
   - `Skill -> Skill`
   - 只表示纯时序/前置约束，不表示数据可直接传递
4. `aggregates`
   - 汇总型 Skill 与多个 `Slot` 的关系
   - 表示该 Skill 的职责是聚合这些维度的中间结果

原有 `similar_to`、`substitute_for` 继续保留，但仍然不直接扩路。

## 4. 召回增强策略

### 4.1 双层召回

召回增强分成两层：

1. 离线稳定骨架
   - 构建 `Artifact taxonomy`
   - 构建 `Slot taxonomy`
   - 产出稳定的父类节点与关系
2. 在线受控扩展
   - 可根据 query 补充更具体的 `Slot`
   - 新 `Slot` 必须映射回已有父类
   - 不允许完全自由生成一套新 taxonomy

例如在线可推断：

- `api_contract_security_findings` -> 父类 `security_findings`
- `ui_accessibility_findings` -> 父类 `design_review_findings`

### 4.2 扩路优先级

在线候选组织时，关系按强弱分层：

1. `produces/consumes`
   - 主扩路依据
   - 优先组织真正的工件/槽位流
2. `aggregates`
   - 只对汇总型节点开放
   - 用于收口，不用于随意串阶段
3. `depends_on`
   - 弱边
   - 只能在已有 `Artifact/Slot` 对齐基础上补充顺序约束
   - 不能裸连两个语义相近的 Skill

### 4.3 通用编排形状

允许在线层使用少量通用“编排形状”辅助召回，但这些形状必须是结构抽象，不是业务模板：

1. 多路评审 -> 汇总
2. 生成 -> 审查 -> 汇总
3. 多维 findings -> 统一 brief

这些形状只用于帮助候选组织，不得写成“如果 query 像发布评审就走固定路径”。

## 5. 执行前验证

### 5.1 两道门

系统明确拆成两道门：

1. 召回门
   - 目标：尽量把合理阶段组织出来
   - 输出：候选计划与结构依据
2. 执行门
   - 目标：只放行真正能跑的计划
   - 输出：`executable`、`structurally_valid_but_incomplete` 或 `invalid`

### 5.2 强弱边规则

1. `can_feed` 仍然是唯一的“直接数据交接”强保证。
2. `depends_on` 不能证明输入闭合。
3. `aggregates` 不能绕开输入契约，只能消费兼容的 `Slot`。

### 5.3 Slot 契约

第一阶段每个核心 `*_findings` 父类都要定义最小 schema。推荐最小字段：

- `summary`
- `severity`
- `evidence`
- `recommendation`
- `blocking`

只有当某个 Skill 声明产出的 `Slot` 满足该父类契约时，汇总节点才允许消费。

### 5.4 计划分级

每条候选计划最终落到三类判定：

1. `executable`
   - 输入闭合，slot 契约满足，可直接执行
2. `structurally_valid_but_incomplete`
   - 结构合理，但存在未闭合 Artifact 输入或 Slot 契约不满足
3. `invalid`
   - 关系类型越界，或链路不成立，直接丢弃

## 6. 离线构建改造

### 6.1 Representation 侧

在现有 `SkillRepresentation` 基础上补充两类可选声明：

1. `emits_slots`
   - Skill 产出的逻辑槽位
2. `consumes_slots`
   - Skill 依赖的逻辑槽位

若源 Skill 未显式声明，允许图构建阶段通过现有 `description/tasks/inputs/outputs` 与 taxonomy 做保守推断，但必须留下诊断与置信信息。

### 6.2 Graph 侧

离线图构建新增职责：

1. 建立 `Artifact` 节点与 `Slot` 节点
2. 生成 `produces/consumes/aggregates/depends_on` 候选
3. 区分强证据与弱证据
4. 将 taxonomy 与节点契约写入 manifest 或独立构建产物

### 6.3 构建产物

推荐新增或扩展的产物信息：

1. `skill_graph.json`
   - 支持三类节点与多类边
2. `skill_index.json`
   - 新增按 `slot`、`artifact`、`aggregator` 的检索索引
3. `build_manifest.json`
   - 显式暴露 taxonomy、schema、索引路径
4. `diagnostics.json`
   - 记录 slot 推断、父类映射、弱边来源、契约缺口

## 7. 在线编排改造

### 7.1 Goal/Grounding

在线 grounding 除了识别显式工件，还要识别：

1. 当前 query 隐含需要哪些评审维度
2. 是否属于“多路评审/汇总”或“生成/审查/汇总”形态
3. 是否需要在线细化子 `Slot`

### 7.2 Candidate Composer

候选组织逻辑从“只沿 Skill-Skill `can_feed` BFS”升级为：

1. 从显式 Artifact 与 query goal 进入
2. 召回能 `consume` 这些 Artifact/Slot 的 Skill
3. 收集其 `produces` 的 Artifact/Slot
4. 对 `aggregates` 节点执行收口组织
5. 仅在必要时用 `depends_on` 补阶段顺序

### 7.3 推荐输出

推荐结果中必须暴露：

1. 哪些阶段是由强边组织的
2. 哪些阶段依赖 Slot 父子映射
3. 哪些连接只是弱时序依赖
4. 当前计划是可执行还是仅结构合理

## 8. 验收策略

### 8.1 第一阶段样例集

第一阶段至少覆盖 4 类样例：

1. 交付前评审
   - 例如：`PRD + API + UI + 技术约束 -> 风险评审 -> 汇总建议`
2. 代码/仓库治理
   - 例如：`repo + 安全要求 + 测试目标 -> 多路评审 -> 整改建议`
3. 方案生成后审查
   - 例如：`需求 -> 生成设计/API 草案 -> 审查 -> 汇总`
4. 技术选型决策
   - 例如：`候选技术栈 + 业务约束 -> 多维评估 -> 推荐`

### 8.2 验收指标

1. 连通率
   - 代表性样例能稳定产出多阶段计划
2. 结构正确率
   - 不因召回增强而串入明显无关节点
3. 执行可分级性
   - 能稳定区分 `executable`、`structurally_valid_but_incomplete`、`invalid`
4. 解释质量
   - 能说明每个节点对应的 Artifact/Slot 与边依据

### 8.3 第一阶段成功标准

1. 多数样例可产出结构合理的多阶段计划
2. 至少一部分样例达到真正可执行
3. 其余样例能明确暴露“缺哪个输入/哪个 Slot 契约未满足”
4. 不通过“业务模板硬编码”达成以上结果

## 9. 风险与取舍

### 9.1 主要风险

1. Slot 设计过细，taxonomy 膨胀，重新引入过拟合
2. 弱边使用过多，导致结构看似合理但执行不可用
3. 汇总节点声明过宽，变成新的垃圾桶

### 9.2 控制策略

1. 父类 Slot 数量保持小而稳定，在线只允许有限子类扩展
2. 弱边不参与输入闭合证明
3. 汇总节点必须声明消费的 Slot 类别与最小 schema
4. 验收看“结构正确率”和“执行可分级性”，不只看连通率

## 10. 非目标后的下一步

本设计完成后，下一步才进入实现计划，优先顺序建议为：

1. 定义 `Artifact/Slot` 数据模型与 manifest 扩展
2. 增量改造 graph candidate generation 与 graph artifacts
3. 改造在线 composer，从纯 `can_feed` 扩路升级为混合图组织
4. 增加执行前验证与计划分级
5. 补齐多样例测试夹具与回放测试
