# 图构建模块设计说明书

## 1. 模块定位

图构建模块负责把结构化 Skill 表征转成本体驱动的 Skill 关联图谱、检索索引和离线构建产物。

它只消费表征提取模块的输出，不直接读取 `SKILL.md`。图谱关联主要由 LLM 基于结构化表征判断，程序侧负责输入裁剪、输出 schema 校验、确定性基础边补齐、索引生成和诊断记录。

本模块参考 Agent-OM 的核心思想：先给匹配器提供结构化的本体上下文，再让匹配器输出可解释的关系判断。对应到 SkillMash，直接使用 `description`、`tasks`、`inputs`、`outputs` 作为本体上下文即可，不额外引入 metadata/syntactic/lexical/semantic 四类画像作为持久化产物。

## 2. 组件划分

```text
SkillRegistryBuilder
CandidateGenerator
LLMOntologyMatcher
SkillGraphBuilder
SkillIndexBuilder
GraphDiagnostics
BuildArtifactWriter
```

推荐组件职责：

| 组件 | 职责 |
| --- | --- |
| `SkillRegistryBuilder` | 注册并校验 `SkillRepresentation`，处理重复 ID 和缺失字段。 |
| `CandidateGenerator` | 基于 input/output/task/type/text 倒排索引生成高召回候选对。 |
| `LLMOntologyMatcher` | 将候选对和 `description/tasks/inputs/outputs` 组织成 LLM 输入，生成关联判断和匹配证据。 |
| `SkillGraphBuilder` | 将确定性基础边和 LLM 匹配边组装为 typed graph。 |
| `SkillIndexBuilder` | 生成在线检索需要的倒排索引、邻接索引和候选计划入口。 |
| `GraphDiagnostics` | 记录冲突、低置信关联、孤立节点和不可闭合输入。 |
| `BuildArtifactWriter` | 写出 manifest、skills、graph、index、llm_matches 和 diagnostics。 |

## 3. 模块 N+1 视图

### 3.1 职责视图

职责：

1. 注册并校验 SkillRepresentation。
2. 基于 `description/tasks/inputs/outputs` 组织 LLM 可理解的本体上下文。
3. 先用确定性索引生成 Skill-Skill 候选对。
4. 调用 LLM 对候选对生成关联判断和解释。
5. 构建 Skill、Artifact、Task、DataType 之间的确定性 typed edge。
6. 将通过校验和阈值的 LLM 关联写入 Skill-Skill typed edge。
7. 生成在线检索需要的倒排索引、邻接索引和候选组合入口。
8. 写出构建产物。
9. 记录重复 ID、缺失字段、孤立节点、低置信关联、LLM 输出不合法项和图结构诊断。

非职责：

1. 不解析原始 Skill 文件。
2. 不让 LLM 直接改写 Skill 表征。
3. 不根据用户任务规划。
4. 不执行 Skill。
5. 不把低置信匹配边当作确定性依赖边。

### 3.2 输入输出视图

输入：

```text
SkillRepresentation[]
```

输出：

```text
BuildArtifact
  build_manifest.json
  skills.json
  skill_graph.json
  skill_index.json
  llm_matches.json
  diagnostics.json
```

### 3.3 数据结构视图

```mermaid
classDiagram
  class SkillRepresentation {
    id
    name
    description
    tasks
    inputs
    outputs
  }

  class LLMMatch {
    source_id
    target_id
    relation_type
    confidence
    method
    reasons
  }

  class RelationCandidate {
    source_id
    target_id
    relation_hint
    evidence
    priority
  }

  class SkillGraph {
    nodes
    edges
  }

  class SkillIndex {
    by_output
    by_input
    by_task
    by_data_type
    neighbors
    upstream_by_input
    downstream_by_output
    by_text_term
  }

  class BuildArtifact {
    manifest
    skills
    graph
    index
    llm_matches
    diagnostics
  }

  SkillRepresentation --> SkillGraph
  SkillRepresentation --> RelationCandidate
  RelationCandidate --> LLMMatch
  SkillRepresentation --> LLMMatch
  LLMMatch --> SkillGraph
  SkillGraph --> BuildArtifact
  SkillIndex --> BuildArtifact
```

### 3.4 协作视图

```mermaid
sequenceDiagram
  participant Offline as OfflineBuilder
  participant Registry as SkillRegistryBuilder
  participant Candidate as CandidateGenerator
  participant Matcher as LLMOntologyMatcher
  participant Graph as SkillGraphBuilder
  participant Index as SkillIndexBuilder
  participant Writer as BuildArtifactWriter

  Offline->>Registry: register(representations)
  Registry-->>Offline: registry
  Offline->>Candidate: generate(registry)
  Candidate-->>Offline: relation_candidates
  Offline->>Matcher: match(registry, relation_candidates)
  Matcher-->>Offline: llm_matches
  Offline->>Graph: build(registry, llm_matches)
  Graph-->>Offline: graph
  Offline->>Index: build(registry, graph)
  Index-->>Offline: skill_index
  Offline->>Writer: write(registry, llm_matches, graph, index)
  Writer-->>Offline: BuildArtifact
```

### 3.5 约束视图

1. 基础边、schema 校验、阈值过滤和索引生成必须是确定性的。
2. 同一个 Skill ID 冲突时必须显式诊断。
3. 图边必须有类型，不能只存无语义连接。
4. `build_manifest.json` 是在线加载的唯一入口。
5. 新增产物必须通过 manifest 暴露，不能依赖目录扫描猜测。
6. 图谱必须区分确定性边和匹配推断边。
7. 每条匹配推断边必须能追溯到 `llm_matches.json` 中的证据。
8. 关联边必须带 `confidence` 和 `method`，在线规划默认只使用达到阈值的边。
9. LLM 输出的 source、target、relation_type 必须经过程序侧 schema 和 ID 校验。
10. LLM 不能新增不存在的 Skill，也不能新增未在 schema 中声明的边类型。
11. LLM 调用必须记录 model、prompt_version、temperature 和输入摘要，保证构建结果可追踪。
12. LLM 不负责从全量 Skill 中枚举关系；候选必须先由 `CandidateGenerator` 生成。

### 3.6 LLM 本体上下文

LLM 构图不需要额外持久化 `entity_id/entity_type/metadata/syntactic/lexical/semantic` 画像。对当前 SkillMash 来说，`description/tasks/inputs/outputs` 已经是结构化后的本体上下文，更直接、更少重复。

推荐传给 LLM 的最小上下文：

```json
{
  "skills": [
    {
      "id": "web_search",
      "name": "Web Search",
      "description": "Search the web and return relevant results.",
      "tasks": ["search"],
      "inputs": [{"name": "topic", "type": "text", "required": true}],
      "outputs": [{"name": "search_results", "type": "json"}]
    }
  ],
  "allowed_relation_types": [
    "can_feed",
    "similar_to",
    "substitute_for",
    "composes_with"
  ]
}
```

`entity_id` 和 `entity_type` 仍然有必要，但只应该出现在最终图节点中，而不是作为单独画像层：

| 字段 | 是否保留 | 理由 |
| --- | --- | --- |
| `entity_id` | 保留在 graph node | 图边需要稳定引用，例如 `skill:web_search`、`artifact:search_results`。 |
| `entity_type` | 保留在 graph node | 在线加载后要区分 Skill、Artifact、Task、DataType。 |
| `metadata/syntactic/lexical/semantic` | 不作为 v1 产物 | 与现有表征重复，先用 LLM 直接消费原始结构化字段。 |

### 3.7 候选生成策略

候选生成目标是高召回、低成本。它不决定最终关系是否成立，只负责把可能有关联的 Skill 对交给 LLM 判断。避免让 LLM 一次性对全量 Skill 做两两组合。

候选生成先构建倒排索引：

```text
by_output_name: output.name -> producer skill ids
by_input_name: input.name -> consumer skill ids
by_task: task -> skill ids
by_data_type: input/output.type -> skill ids
by_text_term: description/name/task/input/output tokens -> skill ids
```

候选类型：

| 候选来源 | relation_hint | 规则 | 优先级 |
| --- | --- | --- | --- |
| `exact_io_match` | `can_feed` | `source.outputs[].name == target.inputs[].name` | 高 |
| `compatible_type_match` | `can_feed` | output/input 的 `type` 相同，且 name 或 description token 有交集 | 中 |
| `task_transition_match` | `composes_with` | task 符合常见流程，例如 `search -> summarize`、`extract -> analyze`、`analyze -> write` | 中 |
| `task_overlap_match` | `similar_to` | 两个 Skill 的 task 有交集 | 中 |
| `shape_similarity_match` | `substitute_for` | inputs/outputs 的 name 和 type 结构相近 | 中 |
| `text_term_match` | `similar_to` | description/name 中有明显共享关键词 | 低 |

候选对象格式：

```json
{
  "source_id": "web_search",
  "target_id": "summarize_text",
  "relation_hint": "can_feed",
  "candidate_method": "exact_io_match",
  "priority": "high",
  "evidence": {
    "source_outputs": [{"name": "search_results", "type": "json"}],
    "target_inputs": [{"name": "search_results", "type": "json"}],
    "matched_terms": ["search_results"]
  }
}
```

候选去重和裁剪：

1. 同一 `(source_id, target_id, relation_hint)` 只保留一条候选。
2. 多个方法命中同一候选时合并 evidence，并提升 priority。
3. 每个 Skill 的候选数量设置上限，例如每种 `relation_hint` 最多保留 top-k。
4. `can_feed` 候选优先保留 exact I/O 命中，其次保留 type-compatible 命中。
5. 自环候选默认丢弃，除非未来明确支持 recursive/composite Skill。

### 3.8 LLM 本体匹配策略

本体匹配由 LLM 判断输入候选并产出匹配结果，程序侧做 deterministic validation。

LLM 需要判断：

1. `can_feed`：A 的输出是否能满足 B 的输入。
2. `similar_to`：两个 Skill 的能力是否相近。
3. `substitute_for`：两个 Skill 是否可在相似上下文中替代。
4. `composes_with`：两个 Skill 是否自然形成连续步骤。

LLM 输出 schema：

```json
{
  "candidate_id": "web_search->summarize_text:can_feed",
  "matches": [
    {
      "source_id": "web_search",
      "target_id": "summarize_text",
      "relation_type": "can_feed",
      "confidence": 0.95,
      "method": "llm_ontology_match",
      "reasons": [
        "web_search outputs search_results.",
        "summarize_text accepts search_results as input."
      ],
      "supporting_fields": {
        "source_outputs": ["search_results"],
        "target_inputs": ["search_results"],
        "source_tasks": ["search"],
        "target_tasks": ["summarize"]
      }
    }
  ]
}
```

程序侧校验：

1. `source_id` 和 `target_id` 必须存在。
2. `relation_type` 必须在白名单中。
3. `confidence` 必须在 `[0, 1]`。
4. `can_feed` 至少要能在 source outputs 和 target inputs 中找到一组 LLM 标注的支持字段。
5. LLM 输出的关系必须对应输入候选；如果 LLM 发现候选方向错误，只能 reject 并说明，不能自由新增反向边。
6. 低于阈值的匹配只进入 `llm_matches.json` 和 diagnostics，不进入默认可规划图。

### 3.9 图谱边类型

基础确定性边：

| 边 | 含义 |
| --- | --- |
| `produces` | Skill 产生某个 Artifact。 |
| `consumes` | Skill 消费某个 Artifact。 |
| `has_task` | Skill 具备某类任务能力。 |
| `uses_data_type` | Skill 输入或输出涉及某种数据形态。 |

本体匹配边：

| 边 | 含义 |
| --- | --- |
| `can_feed` | 一个 Skill 的输出可满足另一个 Skill 的输入。 |
| `similar_to` | Skill 的任务、输入输出和描述语义相近。 |
| `substitute_for` | Skill 在当前表征下可近似替代。 |
| `composes_with` | Skill 之间存在可组合倾向。 |

### 3.10 产物格式

`build_manifest.json`：

```json
{
  "schema_version": "skillmash-build-v1",
  "artifacts": {
    "skills": "skills.json",
    "graph": "skill_graph.json",
    "index": "skill_index.json",
    "llm_matches": "llm_matches.json",
    "diagnostics": "diagnostics.json"
  },
  "thresholds": {
    "can_feed": 0.8,
    "similar_to": 0.75,
    "substitute_for": 0.85
  }
}
```

`llm_matches.json`：

```json
{
  "matches": [
    {
      "source_id": "web_search",
      "target_id": "summarize_text",
      "relation_type": "can_feed",
      "confidence": 0.95,
      "method": "llm_ontology_match",
      "reasons": [
        "web_search outputs artifact:search_results",
        "summarize_text requires artifact:search_results"
      ],
      "accepted": true
    }
  ]
}
```

### 3.11 +1 模块场景

输入：

```text
web_search
  inputs: topic
  outputs: search_results
  tasks: web_search

summarize_text
  inputs: search_results
  outputs: summary
  tasks: summarization
```

图构建输出边：

```text
web_search -> artifact:search_results       produces
artifact:topic -> web_search                consumes
summarize_text -> artifact:summary          produces
artifact:search_results -> summarize_text   consumes
web_search -> task:web_search               has_task
summarize_text -> task:summarization        has_task
web_search -> summarize_text                can_feed
```

索引输出：

```text
by_output.search_results = [web_search]
by_input.search_results = [summarize_text]
by_task.web_search = [web_search]
neighbors.web_search = [summarize_text]
upstream_by_input.search_results = [web_search]
```

匹配证据：

```text
web_search can_feed summarize_text
  method: llm_ontology_match
  confidence: 0.95
  evidence: output search_results == input search_results
```

## 4. 实现分期

### 4.1 v1 LLM 构图

1. 使用 `description`、`tasks`、`inputs`、`outputs` 构造 LLM prompt。
2. 生成 `produces`、`consumes`、`has_task`、`uses_data_type` 基础边。
3. 用 `CandidateGenerator` 生成 relation candidates。
4. 调用 LLM 判断候选是否成立，输出 `can_feed`、`similar_to`、`substitute_for`、`composes_with`。
5. 程序侧校验 LLM 输出并过滤低置信边。
6. 写出 `build_manifest.json`、`skills.json`、`skill_graph.json`、`skill_index.json`、`llm_matches.json`、`diagnostics.json`。

### 4.2 v2 批处理与候选裁剪

1. 当 Skill 数量较多时，先用 output/input name、type、task 和 text term 做候选裁剪。
2. 分批调用 LLM，避免一次 prompt 过长。
3. 对跨批次重复边做合并和置信度聚合。
4. 将 LLM 原始输出和校验结果写入 diagnostics。

### 4.3 v3 可选增强

1. 引入 embedding 仅用于候选召回，不作为最终边判断。
2. 对高价值、低置信候选调用二次 LLM validator。
3. 支持外部领域词表或本体文件作为 `external_ontology`。
4. 记录模型、prompt 版本和温度，提升构建可追踪性。
