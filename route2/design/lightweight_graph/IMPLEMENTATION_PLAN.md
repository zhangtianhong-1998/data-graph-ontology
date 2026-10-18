# 轻量图谱：实施、迁移与验收计划

状态：待执行。本文没有将代码静态核查写成已经实现或已经通过测试。所有新模块名均为建议名称。

## 1. 实施方式

在当前主工程中新增独立 `lightgraph/` 包，与 `route2/` 并列；当前文档暂放在 `route2/design/lightweight_graph/`。迁移期间保留旧入口和现有实验结果。不要把 Gauss、Excel、Markdown 的分支继续加进现有大型 pipeline，也不要直接复制两套工程的所有模块。

按接口提取可复用逻辑，并带上相应回归测试。先完成小实验闭环，再判断是否把通用算子提成第三个公共包；首期避免同时维护两个业务主流程和一个尚不稳定的公共框架。

首期产品边界：一个经验证的 Gauss 产品/版本、Excel、Markdown、实体列组、长/宽层级、受限跨表引用、共享实体、两类文档、源端 DISTINCT 对账和最小可解释浏览。已有 BI 适配能力作为后续接入项，不作为本轮客户输入前提。

## 2. 模块处置表

### 2.1 ontology 主工程

路径均相对当前 `route2/`。

| 当前模块与位置 | 复用内容 | 必须调整/新建 |
| --- | --- | --- |
| [storage.py:62](../../code/ontology_r2/storage.py:62) | 输出 Sink 的职责划分、标识转义思想 | 新 Gauss 路径不调用 Dataset；禁止全量 DuckDB 导入、索引创建、本地行缓存及 `__r2_row` 身份 |
| [profiling.py:28](../../code/ontology_r2/profiling.py:28)、[table_keys.py:22](../../code/ontology_r2/table_keys.py:22) | 类型形态、基数、作用域、函数依赖指标 | 编译为 Gauss SQL；样本只内存使用；源行唯一性与实体可识别分开 |
| [row_semantics.py:185](../../code/ontology_r2/row_semantics.py:185) | 联合基数检验 | 已有的是有限组合计数；新增真正的 EntityProjection/RelationProjection 执行 |
| [quantity_registry.py:111](../../code/ontology_r2/quantity_registry.py:111) | 编码到名称的依赖检验、登记表线索 | 去除必须物理逐行唯一的前提，改为定义投影一致性；不能只支持登记表 |
| [column_role_inference.py:64](../../code/ontology_r2/column_role_inference.py:64) | 有来源的列角色提案 | 扩展到列组、身份限定、关系限定与排除用途 |
| [dimension_members.py:316](../../code/ontology_r2/dimension_members.py:316) | 长表 namespace/code/name/parent 的模型 | 新增宽层级发现/展开/填充压缩/多父与环检查；允许重复事实 |
| [discovery.py:294](../../code/ontology_r2/discovery.py:294) | 候选筛选、命名/值包含等线索 | 替换 CREATE/INSERT/DROP 临时字典；只读查询下推和候选限流 |
| [association_rules.py:25](../../code/ontology_r2/association_rules.py:25) | 受控工具、测试匹配、反例与预算语义 | 当前输出只能改已有候选；新增实体列组、层级及通用算子 DAG；分开探索预算与验证预算 |
| [registry_identity.py:109](../../code/ontology_r2/registry_identity.py:109) | 定义等价与引用范围分离、局部公式依赖解析 | 接统一 RuleSpec/EntityIdentity；增加持久稳定 ID、版本/拆分和 ACL |
| [registry_rows.py:83](../../code/ontology_r2/registry_rows.py:83)、[structural_relations.py](../../code/ontology_r2/structural_relations.py) | 多来源绑定、结构关系 | 直接产生轻量节点/边，不创建每个业务定义的 DerivedType；不 `SELECT *` 拉行 |
| [metadata_graph.py:93](../../code/ontology_r2/metadata_graph.py:93)、[viewer.html:398](../../code/ontology_r2/viewer.html:398) | 现有表列、外键、技术边、列展开 | 加列组、属性角色、规则映射、字段对照；不重复开发已存在的列级边 |
| [knowledge.py:148](../../code/ontology_r2/knowledge.py:148) | 先搜索后读原文、范围与引用核验 | 新 Markdown 输入、文档双用途、认知说明与版本依赖；移除工具正文日志 |
| [semantic_state.py:93](../../code/ontology_r2/semantic_state.py:93)、[llm.py:252](../../code/ontology_r2/llm.py:252) | 预算/调用接口与失败状态思想 | 新状态契约；禁原始 evidence JSONL、模型输入/输出缓存和 trace 正文 |
| `semantic_cards/instance_bundles` | 完整证据单元的组包原则 | Gauss 模式不建 SQLite 语义卡源值缓存，改为临时假设上下文 |
| `fact_*`、完整类型归纳/泛化/等价等 | 需要时作为研究分支参考 | 不进入客户轻量主流程；不删除旧代码 |

### 2.2 xiaomeng

| 当前模块与位置 | 复用内容 | 必须调整/新建 |
| --- | --- | --- |
| [config.py:42](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/config.py:42)、[loaders.py:17](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/loaders.py:17) | 六类 BI 导出来源的解析 | 改为可选 BI 适配器，不要求新客户提供报表 SQL/组件/血缘 |
| [preprocess.py:94](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/preprocess.py:94) | 资产归一、字段来源与报表范围 | 脱离报表中心的表筛选；来源 ID 不随字段排序变化 |
| [sql_ast.py:47](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/sql_ast.py:47) | 历史 SQL 解析与作用域解析 | 保留为证据解释器；执行网关另建严格解析器，禁止容错修复后执行 |
| [evidence.py:361](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/evidence.py:361) | SQL 过滤/分组/依赖证据 | 可辅助规则发现；不把投影依赖当字段等价 |
| [evidence.py:62](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/evidence.py:62) | 保持完整语义单元 | 新包以表模式、列组、假设、文档段落为单位，不按报表组件组织所有数据 |
| [extraction_boundary.py:38](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/extraction_boundary.py:38)、[assembly.py:24](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/assembly.py:24) | 坏条目隔离、证据绑定、确定性组装 | 新规则 DSL 与图事实通道；不持久化被隔离的原始响应 |
| [validation.py:145](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/validation.py:145) | 状态、端点、来源检查 | 新 FD/层级/引用/完整分区验证；分析文档单设模态 Claim 校验，不沿用推测词一律隔离；格式正确不代表数据规律成立 |
| [models.py:534](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/models.py:534)、[knowledge.py:33](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/knowledge.py:33) | Node/Edge/Assertion 与发布结构 | 增加 rule_revision、support、分区、撤回状态；来源校验不依赖本地 snapshot |
| [config.py:109](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/config.py:109)、[resolution.py:1738](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/resolution.py:1738) | 候选复核、旧 ID 稳定策略 | 不沿用 scope/physical binding 作为全局共享定义身份；模糊归并只处理待决 |
| [pipeline.py:320](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/pipeline.py:320) | 契约版本、成功任务复用 | 数据库增量另建；禁 source_snapshot、llm_inputs.txt 和原始包检查点 |
| [identity_context.py:176](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/identity_context.py:176)、[semantic_structure.py:32](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/semantic_structure.py:32)、[case_semantics.py:24](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/case_semantics.py:24)、[resolution.py:1099](/Users/sanjiuganmaoling/Desktop/code/xiaomeng/src/xiaomeng/resolution.py:1099) | 表达式结构与定义匹配 | 清理固定 ClickHouse 方言；方言随数据来源和表达式传递 |

## 3. 开发阶段与退出条件

各阶段必须提交可运行的小样例、失败样例和差异报告。以下工时是工作分解用的粗估，以 1 名熟悉代码的工程师为单位，不是已测算承诺；Gauss 环境等待、生产审批和未确定功能不计入。

### P0：冻结契约与真值，约 3–4 人日

**对应 US：** 01、05、21 的准备部分。

交付：目标描述、数据保留矩阵、输入/输出模型、12 组真值用例、审查版本记录。记录当前未提交修改的代码指纹，保留之前共享实体修复的回归测试。确定 Gauss 产品/版本及结果存储位置。

人工构造 8–12 张、总计约 3,000–10,000 行的小表，另有 Excel 与 3–6 份 Markdown。至少包含长表和宽表两种层级、重复事实、不同命名空间、误导 schema、复合引用和同名异义。分类答案不直接塞进每一行；说明文档作为单独输入可开关做对照。

**退出条件：** 每个用例有明确实体/边集合或明确“应该未决”的结论；不能直接把用户不一致的整棵树写为唯一答案。此阶段不启动大规模付费抽取。

### P1：Gauss/Excel/Markdown 接入与不落盘约束，约 6–9 人日

**对应 US：** 01–04、15 的输入部分。

开发：`contracts`、`sources/gauss`、`sources/excel`、`sources/markdown`、`query/policy`、`query/budget`、`PersistencePolicy`。先接一个实际 Gauss 产品版本；其他产品显示不支持，不做模糊兼容。

验证：合法 SELECT/聚合/CTE、严格拒绝集、最小权限、只读事务确认、超时与取消、流式批次、Excel 编码/表头/公式缓存值、Markdown 位置与版本。不保存结果的约束覆盖日志、异常、trace、SDK、临时文件及模型网关。

**退出条件：** 隔离 Gauss 环境完成源库安全集成测试；canary 值在成功/失败/取消后的持久介质中均未泄漏；上传文件没有产生额外解析副本。未满足时不能接入真实样本。

### P2：数据认知与表内列组，约 5–7 人日

**对应 US：** 05–08、11、12、15。

开发：目标筛选、声明/观察/推断分离的数据说明、注释质量、分层观察、列组提案、FD/身份限定验证、实体投影规则。模型处理单位为列组/模式，执行器处理唯一语义投影。

**退出条件：** 正常重复事实不再被逐行唯一门槛拒绝；`code/cn_name/en_name/sort` 例子形成正确实体规则；金额事实和排序不变成语义节点；错误 schema 被报告；无文档/有正确文档/有冲突文档三种情况区分。

### P3：层级、共享身份和第一条完整发布流程，约 6–8 人日

**对应 US：** 09、12、13、17。

开发：宽转长、连续重复压缩、缺层与多父策略、防环、共享语义身份、范围绑定、轻量图输出与元数据映射。复用当前 registry 的同义定义/局部编码解析测试，新增持久 ID 与定义变更隔离。

**退出条件：** 重复层不形成多个对象或自环；同父 L5 不形成父子；跨租户共享定义但引用范围完整；同名不同公式不合并；一次小数据端到端结果与真值逐项相符。

### P4：跨表发现和分析知识文档，约 6–8 人日

**对应 US：** 10、11、14、16。

开发：优先级候选生成、复合/条件引用、语义属性角色对齐、受限 JSON 展开、分析段落抽取和图中绑定、文档冲突与权限。把已有文档检索工具迁入无正文日志环境。

**退出条件：** 两列同为中文名称可显示角色对齐，但不伪造 FK；通用列名与整数碰撞反例被拒；分析方法有出处且不能升级为数据库事实；两个输入通道合并后的支持关系可查询。

### P5：增量、撤回与中断恢复，约 5–7 人日

**对应 US：** 18–20。

先实现无需 CDC 的版本：复用规则，源端 DISTINCT，按完整分区对账；再接客户已有的更新时间/变更来源作为优化。新增结果支持账本、完整分区记录、原子发布、游标提交、依赖失效。账本分别支持节点存在、每个属性值/别名、边与文档主张；不能只给整个共享节点记来源。

**退出条件：** 原样重跑没有重复图事实、没有逐行模型调用；新增符合规则的成员直接构建；删除某租户引用不删其他租户实体；部分失败不误删；公式改变只影响相应来源；规则/文档改变只重算依赖范围。

### P6：浏览、成本与完整验收，约 4–6 人日

**对应 US：** 14、21、22。

交付：表列总览、双表字段对照、列组/规则详情、业务实体与引用范围、冲突/覆盖报告、实际 Gauss 版本兼容报告。规模试验以重复倍数扩张，保持语义真值不变，另加“语义基数增加”试验。

**退出条件：** 所有 P0 故事通过；性能指标与正确性指标分别报告；数据库扫描成本与返回量分别报告；未支持范围明确列出。没有真实客户语义标注时，只能验收工具与固定结构用例，不能宣称客户业务含义全部正确。

### 后续可选项

- 更多 Gauss 产品/版本和驱动平台适配。
- 已有 CDC 接口接入与细粒度删除优化；不改变源库只读约束。
- 迁移 xiaomeng BI 组件/SQL/血缘适配器。
- 更复杂 JSON、交叉表、跨表层级和文档公式绑定。
- 客户数据规模证明确有需要后，再增加图数据库后端、向量索引和高级检索。

粗估合计约 35–49 人日；两人可并行接入、安全、文档和确定性算子，但规则契约、身份和增量有顺序依赖，不能简单把总人日除二当交付周期。完成 P1/P2 后应依据 Gauss 实测和反例覆盖重新估算。

## 4. 并行工作与依赖

```text
P0 契约/真值
 ├─ P1 Gauss/安全/无数据落盘 ── P2 认知与列组 ── P3 层级/共享/发布
 └─ P1 Excel/Markdown 入口 ────┘                   │
                            P4 跨表/分析文档 ←─────┤
                            P5 增量/支持撤回 ←─────┘
                                      └──────── P6 完整验收
```

可以先使用已确认的 RuleSpec 单测 P3/P5，再接模型发现；这不是要求用户长期手写数据集映射，而是把规则执行器和规则发现器分开验证。最终端到端必须从元数据、少量数据和文档自动生成规则，不能把测试真值偷偷作为模型输入。

## 5. 贯穿各阶段的验证矩阵

| 方面 | 对照方法 | 必须保留的失败结果 |
| --- | --- | --- |
| schema 依赖 | 正确注释、回声注释、误导注释三组 | 无说明时不能硬猜业务类别；误导注释不得覆盖已观察冲突 |
| 文档贡献 | 无文档/正确说明/过时说明/分析文档分别运行 | 说明有无改善规则；错误说明有没有被发现 |
| 重复数据 | 同真值下扩大重复行数 | 图集合稳定；DB 耗时可能上升，不能隐藏 |
| 关键组合 | 联合元组真值与独立 DISTINCT 集合对照 | 没有产生源中不存在的笛卡尔配对 |
| 身份共享 | 同义不同编号/同名不同公式/缺失定义 | 正确合并与不误合并分别统计 |
| 层级 | 长表/宽表/填充/缺层/环/多父 | 未决原因与错误边数量，不能仅比较节点数 |
| 增量 | 全量重建图与增量维护图比较 | 节点、边、属性、支持范围一致；审计时间等运行字段可不同 |
| 中断 | 在取数、变换、发布、游标提交间注入失败 | 旧支持不误撤回、重试不重复计数 |
| 权限 | A/B 两个范围访问共享实体 | A 看不到 B 私有支持和属性证据 |
| 数据保留 | canary 值、文件写入拦截、临时目录/日志/追踪检查 | 无完整输入包、样本、原始响应落盘 |
| 方言 | 实际 Gauss 环境验证；不支持语法负例 | 不以 DuckDB/PostgreSQL 测试通过代替 Gauss 结果 |
| 成本 | 查询次数/耗时/返回字节、模型调用、内存分别记账 | 预算耗尽必须显示未覆盖范围 |

图集合比较要同时覆盖反例：只减少节点而丢失业务差异不能通过。示例中的语义类别如果无法由输入证明，应在真值里允许 unresolved，而不是迫使模型猜中人工标签。

## 6. 第一份可评审交付应包含什么

建议 P0/P1 首轮提交这些具体内容，暂不运行大规模抽取：

1. 类型化契约与数据保留白名单，明确哪些旧写文件路径无法被新模式调用。
2. Gauss 连接能力报告和严格 SQL 拒绝集。
3. Excel/Markdown 统一输入清单与小样例。
4. 固定 RuleSpec 执行“销售费用”列组和宽层级的测试；尚不接模型也能核对图输出。
5. 结果支持账本的最小接口，使下一轮共享身份与删除不必重做数据模型。

P2/P3 再接自动发现，比较“人工真值规则”与“模型提出并经 SQL 核验的规则”的差别。这样能区分规则执行器错误、结构发现错误、语义证据不足，避免再次用长时间全量运行把问题混在一起。

## 7. 本次交付与未验证范围

本次完成的是两项目静态代码核查，以及 README、22 条 US、SDD 和分阶段计划。没有修改运行代码、生成新数据、调用模型、连接 Gauss 或启动实验。

还没有取得客户数据库的实际版本、权限与数据结果。性能数字是待测指标，工时是规划估计；没有对客户语义准确率作出保证。后续实施应先完成连接和数据保留验证，再进入结构发现实验。
