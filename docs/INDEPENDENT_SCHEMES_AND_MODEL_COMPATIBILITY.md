# 独立标注方案与 dse_model 兼容性

初次检查：2026-11-14；依赖说明更新：2026-11-15。模型已完成独立方案适配，并使用发布到 GitHub 的工具实现；以下区分当前接入状态与初次验证记录。

## 使用方式

页面顶部的“标注方案”用于切换。“新建独立标注方案”支持使用当前规则、选择预设、导入独立 `schema.json`。填写名称，校验规则，再创建并切换。新方案从版本 1 和空标注开始，复用已登记的数据源路径，不复制视频。

规则导入有两种不同用途：

| 入口 | 作用 |
| --- | --- |
| 字段与规则 → 导入 schema.json → 发布 | 升级当前方案；继续检查稳定身份，保留旧值并要求重新审核 |
| 新建独立标注方案 → 导入独立 schema.json | 新建独立字段身份空间；允许与旧方案的 ID/key 含义冲突，不迁移旧标签 |

“字段与规则”的“规则测试值”使用当前草稿中的字段、作用域和类别，支持 left/right/global。默认不预选类别；无 phase 的方案不会显示左右 phase 测试项。示例值用于预览适用与必填条件，预览结果包含全局字段。

独立导入默认使用 `semantic_profile: "generic"`，不要求 `phase`，不施加原始 DSE 的固定阶段/持物语义。仍须遵循项目的 categorical schema 格式：字段作用域为 `each_arm` 或 `global`，提供 classes、unknown_id、适用条件与必填条件，依赖不得循环。并非支持任意 JSON Schema 标准文件。

从当前规则或预设创建时，保留原规则的语义配置；原始预设为 `dse_v1`。可在创建前选择通用规则。旧文件缺少 semantic_profile 时，在原有升级和读取路径上仍按 `dse_v1` 解释，保持兼容。方案内不能通过普通发布切换语义配置。

切换会重新加载页面。已保存标签、审核、事件、训练选区、撤销历史、偏好和规则草稿均隔离；未提交草稿不会迁移到另一方案。浏览器标签页分别保存所选方案，请求通过 `X-Annotation-Scheme` 绑定方案，图片 URL 通过 `scheme` 参数绑定。缺少标识的旧 API 客户端仍操作原有方案。

## 数据加载与保存

- 原有方案继续使用工作目录中的 `project.sqlite3`；独立方案使用 `schemes/<uuid>/project.sqlite3`。备份完整项目时应包含整个工作目录，而不仅是根数据库。
- 数据源登记集中保存在根数据库，各方案可直接选择同一数据集。Dataset 的视频、状态和 source_map 读取格式不变。
- UI 导入数据集默认只加载来源；明确勾选“同时恢复导出集标注”才恢复随导出保存的规则、标签、审核、事件和训练中心选区。恢复进有记录的方案时要求规则完全一致，否则拒绝，要求使用独立方案。
- API `/api/import` 新增 `restore_annotations`。显式 false 仅加载来源；true 恢复标注。省略时，原有方案保留首次导入自动恢复的旧行为，新方案默认 false。已恢复的同一导出不会重复写入。
- 新方案只保留自身规则历史；从旧规则创建独立身份空间时不继承旧方案的退役 ID 限制。
- 导出仍使用 LeRobot-v2.1+DSE-1。schema、head_spec、labels、intervals 和历史仅来自当前方案；manifest 新增 `annotation_scheme`（project_id、id、name）。导出线程绑定具体方案和冻结快照，切换页面不改变作业归属。
- 通用方案不使用原始 phase 数字 ID 的关键阶段采样：key_zone 为 null；导出页可按 left/right/global 字段选择关键类别，任一命中或确认事件附近为 key，其余为 background。不选类别则仅事件邻域为 key。规则及指纹记录于 manifest，不改变分类 schema。固定阶段的自动 adviser 建议被禁用；普通人工标注和声明的门控条件仍可使用。
- `DSEDataset` 按导出的 schema 解析动态字段，新增 `annotation_scheme` 属性供调用方识别来源方案。原始 Dataset 不需要改变数据格式。

## dse_model：2026-11-14 已完成独立方案适配

模型项目位置：`/home/punk/yann_repo/paper_code_01/dse_model`。原兼容性检查中的主要缺口已在模型项目实现；完整操作文档见 [新标注方案训练、测试、推理与导出指南](../../dse_model/docs/NEW_ANNOTATION_SCHEME_GUIDE.md)，最新校验见 [模型验证记录](../../dse_model/docs/VALIDATION.md)。模型使用 GitHub main 来源，锁文件固定确切提交，升级操作需在模型自己的环境中执行。

| 环节 | 当前实现 |
| --- | --- |
| 依赖 | 模型 pyproject 使用 GitHub main，uv.lock 固定具体提交；2026-11-15 本次校准回归核对安装为 `93fa0e333d051767f54db8689242b2dfbb5a6b1a`。`uv sync --locked` 复现，`uv sync --upgrade-package dse-annotation-tool` 主动升级；不要求相邻工具源码 |
| 训练与配置 | 动态 each_arm/global 分类头及稳定 ID 映射；`dse init-scheme` 从导出生成完整实验配置、字段损失权重、schema/head/方案快照和导出模板草稿 |
| 方案身份 | 默认拒绝混合不同 (project_id,id)，显式合并仍要求 schema/layout 相同且同源标签无冲突；原来源划分键不添加方案 ID |
| 推理门控 | 按 schema 条件树和依赖顺序处理全部 scope、unknown_id、阈值及逐类别连续确认；仅 dse_v1 使用原始一致性约束 |
| C-text | 支持旧 DSE 词汇、通用字段表达、绑定 schema hash 的逐类别字面映射模板；支持 global，模板内容变化阻止恢复旧导出 |
| 评估与追踪 | unknown 统计依据 unknown_id；run/checkpoint/校准/sidecar 记录方案；五折校验 schema、方案集合、模板与部署策略一致 |
| 温度校准（2026-11-15） | 有效样本数足够时允许部分类别覆盖；导出只让本头已覆盖的预测类别进入后续门控，未覆盖类别保留原始预测并记录 class_not_calibrated；适用于全部 categorical 方案 |

每个新方案仍需明确字段权重、采集组划分、输入证据、校准覆盖、阈值与确认策略、文本业务含义及下游 token 预算。任意新业务矛盾/时序语义不能仅凭字段名自动推断；超出 categorical schema 和声明条件树的任务需专门实现并测试。更换方案需要新实验，不能直接复用旧分类头、续训状态或校准报告。

某些定义类别（包括 unknown）从未使用，不再单独导致模型校准关闭整个字段。覆盖依据各 left/right/global 头在独立 calibration 集里的有效真值，不能拿全部标注数量替代。unknown_id 的现有语义继续保留；无需为补齐类别制造 unknown 或把普通标签绑定成 unknown。模型代码升级后，旧实验可重新校准写出 v2 报告，并使用新目录导出 C-text；旧报告不自动提升 usable。改动原理、兼容性与操作见 [部分类别覆盖校准说明](../../dse_model/docs/PARTIAL_COVERAGE_CALIBRATION.md)。

## 初次验证记录（2026-11-14）

后端全套 78 项测试通过；新增测试覆盖独立 ID、无 phase/global 字段、旧标签与撤销隔离、重启恢复、并发请求、来源复用、导出与 DSEDataset 读取、显式恢复和重复导入。前端构建、中英文检查和改动 Python 文件的 Ruff 检查通过。

环境禁止创建网络 socket，因此普通 TestClient 事件循环唤醒会阻塞；测试使用临时定时唤醒启动脚本完成，项目运行代码未加入该补丁。同一限制使本轮无法启动 HTTP 服务完成真实浏览器交互验收。
