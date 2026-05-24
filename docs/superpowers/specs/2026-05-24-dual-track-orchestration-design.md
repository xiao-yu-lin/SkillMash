# SkillMash 准生产双轨编排设计（召回轨 + 验证轨）

- 日期：2026-05-24
- 适用范围：SkillMash 在线编排与排序链路
- 目标基线：准生产（稳定、可观测、可解释、失败可恢复）

## 1. 设计目标与约束

### 1.1 目标

1. 降低错误可执行计划输出概率（错计划）。
2. 降低可执行计划漏召回概率（漏计划）。
3. 在不确定时采用保守策略：拒答而不是勉强推荐。

### 1.2 已确认约束

1. 在线阶段允许使用 LLM（query grounding、候选 rerank）。
2. 当前阶段吞吐不是首要约束。
3. 默认排序偏好：最高可靠性。

## 2. 核心方案：双轨分层

### 2.1 召回轨（Recall Track）

职责：尽量不漏，扩大入口与路径候选覆盖。

- Goal/Grounding（LLM可用）
- 宽入口召回
- 图搜索与候选路径生成
- 并行下游候选槽位识别（slot grouping）

### 2.2 验证轨（Validation Track）

职责：确保不错，只有通过硬校验的计划才允许进入最终推荐。

- 硬约束过滤（hard filter）
- 槽位选择（在合法候选中择优）
- 可靠性优先评分
- 可选 LLM rerank（仅在已通过集合内排序）

### 2.3 保守决策

- 若 `validated_plans` 为空：返回“无可执行计划”与失败原因聚合，不给可执行建议。
- 若 `validated_plans` 非空：仅从该集合返回 `recommended_plans`。

## 3. 两段式剪支（关键）

### 3.1 搜索阶段轻剪支

只剪“可证明错误”的分支，不做不可逆软决策。

可早剪：

1. 必需输入无来源。
2. 明确类型硬冲突。
3. 策略禁用（黑名单、最低阈值未达）。

不可早剪：

1. 同槽位候选谁更优（例如 `B` vs `B2`）。
2. 仅基于弱语义偏好做分支淘汰。

### 3.2 候选成型后主剪支

在完整候选 DAG/路径层做最终选择：

1. 先跑 hard filter。
2. 再在每个 slot 内做合法候选择优。
3. 最后对整体计划做可靠性优先排序。

## 4. 槽位归并与选择规则

### 4.1 场景定义

给定：

- `A can_feed B`
- `A can_feed B2`
- `B2 substitute_for B`

处理原则：

1. 图上保留两条可执行候选主链（`A->B` 与 `A->B2`）。
2. 在剪支阶段将 `B/B2` 视为同一 slot 的候选集合。
3. 槽位内先合法性校验，再可靠性择优。

### 4.2 候选合法性（硬规则）

候选 `Bx` 必须同时满足：

1. `A -> Bx` 存在显式 `can_feed` 或可证实 deterministic exact-io 证据。
2. 替换后与上下游链路保持 I/O 闭合。
3. 不触发策略禁用规则（阈值/黑名单/关键 unknown 类型）。

任一不满足即淘汰。

### 4.3 可靠性优先（默认）

在通过硬校验的候选中比较：

1. 关键边最低置信（越高越好）。
2. 全链平均置信（越高越好）。
3. 历史成功信号（若有，越高越好）。
4. 不确定项（越少越好）。
5. 步数（越短越好，低优先级）。

## 5. 策略抽象接口（可扩展）

```python
@dataclass
class PruneContext:
    query: str
    grounded_query: dict
    policy: dict
    runtime_constraints: dict

@dataclass
class FilterResult:
    passed: bool
    hard_fail_codes: list[str]

class PlanStrategy(Protocol):
    name: str
    def hard_filter(self, plan: dict, ctx: PruneContext) -> FilterResult: ...
    def rank_score(self, plan: dict, ctx: PruneContext) -> float: ...
```

运行约束：

1. `hard_filter` 先于任何 rerank。
2. `rank_score` 仅用于通过 hard filter 的计划。
3. LLM rerank 不得新增/改写计划拓扑，只能在通过集合内排序。

## 6. 模块落位（对当前代码的映射）

### 6.1 召回轨

- 保留：`skillmash/orchestration/planning/grounding.py`
- 保留并增强：`skillmash/orchestration/planning/search.py`
- 新增：`skillmash/orchestration/planning/slot_grouping.py`

### 6.2 验证轨

- 新增：`skillmash/orchestration/validation/validator.py`
- 新增：`skillmash/orchestration/validation/policy.py`

### 6.3 策略层

- 新增：`skillmash/orchestration/strategy/interfaces.py`
- 新增：`skillmash/orchestration/strategy/reliability_first.py`

### 6.4 排序层

- 复用：`skillmash/reranking/plan_reranker.py`
- 限制：输入必须是 `validated_plans`。

### 6.5 门面编排顺序

`ground -> recall -> slot_group -> hard_filter -> strategy_rank -> llm_rerank(optional) -> conservative_decision`

## 7. P0/P1 问题映射与最小改造

### 7.1 P0（必须先改）

1. 缺少硬闸门后置拒答：需确保推荐仅来自 `validated_plans`。
2. 槽位替换推导边标高置信：禁止把推导边当执行依据。
3. 替换后缺少邻接显式可喂约束：替换后必须复核邻接边。
4. deterministic exact-io 过强接收：纳入策略阈值与禁用规则。

### 7.2 P1（紧随其后）

1. 入口召回与分支耦合：拆分 `max_entry_skills` 与 `max_branch`。
2. 候选限流方向偏置：按有向候选限流。
3. DAG 合成偏激进：增加目标一致性约束。
4. 配置命名不一致：统一文档与代码配置名（`LLM_*`）。
5. 产物路径猜测：把 vocab 路径显式写入 manifest。

## 8. 验收标准

### 8.1 功能正确性

1. 不通过 hard filter 的计划不得进入 `recommended_plans`。
2. 当无通过计划时返回保守拒答与失败原因聚合。
3. 槽位选择能在 `A->B` 与 `A->B2` 间正确择优。

### 8.2 可解释性

1. 每个推荐计划可追踪到：候选来源、校验结果、评分分解。
2. 每个拒答结果可追踪到：高频失败码与代表性失败点。

### 8.3 回归安全

1. reranker 不得改写计划拓扑。
2. 验证轨策略变化需具备快照测试与回放测试。

## 9. 下一步优化（已记录，不在本次改造内）

1. 边走边剪优化：搜索阶段扩大“硬失败早剪”覆盖，降低无效扩展。
2. 参数窗口调优：后续单独评估 `max_entry_skills/max_branch/max_depth`。
3. 引入在线反馈学习：在不破坏保守策略前提下微调排序权重。

## 10. 非目标

1. 当前不引入吞吐优化专项（并发控制、缓存分层、批处理编排）。
2. 当前不引入复杂多租户权限模型。
3. 当前不改动执行器运行时，仅完成在线编排与验证链路稳定化。
