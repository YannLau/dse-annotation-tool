# 给实现 agent 的提示词：自定义标注方案的 adviser 自动建议

请在 `/home/punk/yann_repo/paper_code_01/dse_annotation_tool` 实现下面的功能，完成代码、测试、界面和文档。先阅读适用的 AGENTS.md 和现有实现，再动手修改；不要只提交设计说明或仅删除 generic 限制。

## 1. 用户目标和已确认的选择

标注工具已经支持独立标注方案、generic schema、动态类别 ID、each_arm/global 字段和条件树。现在需要让“行为与校准 → adviser 方向预标注”适配这些方案：当当前方案中 `adviser.x`、`adviser.y`、`adviser.z`、`adviser.orientation` 的适用条件成立时，为实际适用的字段生成候选。

用户于 2026-11-17 明确确认：

1. adviser 沿用原有语义：**经认可的示教短窗口实际运动方向/姿态变化**。它不是基于目标误差推导的纠偏方向，也不证明当前已经对准、ready 或持物。
2. **继续要求用于计算的短动作窗口连续 approved**。用户可以先用现有动作质量审核功能批量审核可信片段。未审核动作不参与自动建议，rejected 动作不能被跳过后再拼接后面的恢复动作。
3. 支持 **当前帧 + 当前选区逐帧计算**。不新增整 episode 扫描入口，不把当前帧的一个答案复制到整段。

本次只实现 adviser 的适配及其必要的可靠性修复。不要顺带重构夹爪事件检测器、采样策略、任务几何协议或模型训练系统。常规实现选择自行完成；只有新的业务语义确实无法从本文确定时才向用户询问。

## 2. 先理解现有代码和缺口

重点阅读以下仓库相对路径：

- `backend/dse_tool/assist.py`：`adviser_candidate` 当前写死 phase ID 2/5、相同 phase 的未来窗口、固定方向类别 ID。
- `backend/dse_tool/api.py`：`/api/adviser/suggest` 当前拒绝 generic，固定选择末端反馈字段；事件查询没有筛选确认状态和手臂。
- `backend/dse_tool/schema.py`：`evaluate`、`resolve`、`references`、`field_map` 和三值条件树；适用和必填是不同结果。
- `backend/dse_tool/store.py`：原始值/有效值、单帧覆盖、批量事务、revision、撤销和 provenance。
- `backend/dse_tool/review.py`：标签审核与动作质量审核的区别。
- `backend/dse_tool/schemes.py`：独立方案隔离与请求上下文。
- `backend/dse_tool/dataset.py`：原始帧和裁剪后输入帧的 source_key/source_map。
- `backend/dse_tool/exporter.py`、`backend/dse_tool/adapter.py`：导出字段、追溯信息和模型读取契约。
- `frontend/src/main.tsx`：现有 adviser 界面、保存候选路径、上下文切换及请求过期处理。
- `frontend/src/locales/en.json`、`tests/test_assist_evidence.py`、`tests/test_core.py`、`tests/test_schemes.py`、`tests/test_range_review.py`、`tests/test_point_policy.py`。
- `docs/INDEPENDENT_SCHEMES_AND_MODEL_COMPATIBILITY.md`、`docs/ANNOTATED_DATASET_MANUAL_ZH.md` 及英文对应文档。

已知缺口需要一起解决：generic 被一律拒绝；关闭 adviser 门控的预设仍显示计算工具；接受候选时逐字段发送请求且可能全部跳过却提示成功；候选未绑定生成时的帧/手臂等上下文；未确认或其他臂的事件可能错误截断窗口。

## 3. 能力判断与类别语义

### 3.1 按实际字段提供能力

- 同时支持 dse_v1 与 generic；generic 不要求包含 phase，不把其任何数字类别解释为旧 DSE 阶段。
- 首版自动建议目标限于上述四个精确 field_id，且目标字段须为 `categorical`、`scope=each_arm`、`evidence=behavior`。左右臂分别计算。
- 四个字段允许只存在一部分、只适用一部分；不要要求四项同时打开。
- 门控可依赖当前方案的 self/left/right/global 字段及 all/any/not、eq/ne/in 条件树。支持全局开关控制左右臂 adviser，但不自动推断 global adviser 应使用哪只臂。
- 若同名目标字段声明为 global 或 state evidence，明确报告不支持原因；不要擅自改 schema、作用域、证据模式或写入状态字段。
- 能力检查和实际计算都应由服务端给出逐臂、逐字段的结构化原因，前端不能靠 semantic_profile 或字段前缀独立判定可用。

### 3.2 类别 ID 不固定

算法先产生语义结果，再映射到当前字段的稳定类别 ID：

- x/y/z：none、positive、negative、unknown。
- orientation：keep、adjust、unknown。

可用标准 class.key 自动匹配前三类/前两类；unknown 使用该字段自己的 unknown_id，未知类别的 key 可以是 unsure 等。针对自定义 key，提供按字段配置的显式类别映射；有歧义或映射不完整时禁用对应字段并说明缺项。每个角色映射到当前字段内有效、未废弃的类别，角色不得错误共用同一个 ID。

禁止写死 x/y/z 的 0/1/2/3、orientation 的 0/1/2；不同轴可使用不同 ID，包括非连续 ID。额外类别不必删除。schema 改动后重新校验映射，映射配置绑定方案和 schema 指纹，不改变分类 schema/head_spec。

## 4. 门控与短窗口的严格定义

### 4.1 当前帧是否可建议

使用**当前已发布 schema 和已保存标签**，复用现有 evaluate/resolve 的语义，逐臂、逐字段判断：

- applicability=applicable：可继续检查数据、approved、标定和类别映射。
- applicability=not_applicable：跳过该字段，不生成或写入答案。
- applicability=undetermined：报告依赖缺失/unknown，不能自动当作 false，也不能强行生成 unknown 标签代替缺失证据。
- required_when 仅决定是否必填，不能代替 applicable_when。适用但非必填的字段仍可建议。
- 明确在条件树中允许 unknown_id 的分支，继续遵守现有三值规则，不额外把所有 unknown 无条件拒绝。
- 未提交的表单草稿、未发布的规则草稿不参与计算；界面提示先保存门控标签。
- 同一轮不利用尚未接受的候选反向开启其他 adviser 门控；只能基于已保存值计算。

### 4.2 为每个目标帧/臂/字段找窗口

从目标时刻 t 开始，沿真实连续源帧向后取不超过 0.25 秒的已认可短动作；可用实际持续时间不足 0.1 秒则报告 missing。以真实 timestamp 判断时长，正确处理浮点边界，不假定固定 FPS。

在下列条件首次出现前截断，不能跳过后继续拼接：

1. 后续动作不再 approved，或遇 rejected/unreviewed。
2. 该字段 applicability 不再为 true，或变成 undetermined。
3. 门控上下文发生变化。采用明确的保守策略：跟踪该字段 applicable_when/required_when 的直接及传递依赖，正确展开 self 和各 scope；依赖值组合变化即截断，即便条件总结果仍为 true。这样可阻止旧阶段 2→5 或自定义两个操作段在同一次窗口中混合，无需写死 phase ID。
4. 遇到同一手臂的 confirmed 事件。其他臂事件和 suggestion 事件默认不截断；不要因此改动事件的导出采样策略。
5. episode 结束、source UUID 改变、source_frame 不连续、时间戳无效或所需反馈数据缺失/无效。

动作批准沿用现有 action-at-frame 约定：每个被纳入的位姿转移 i→i+1，对应源帧 i 的动作必须 approved；写清末端点与审核半开区间的边界语义并测试。

每个分量按自己的门控/依赖决定可用窗口；可以共享相同窗口的计算，但不能用 x 的适用结果代替其他分量。窗口只有两个有效采样点时也必须满足实际持续时间和连续性条件。

### 4.3 选区范围与证据范围分开

- 当前帧模式：目标标注范围为 `[frame, frame+1)`。
- 当前选区模式：目标标注范围为 `[start,end)`，分别为范围内每一帧计算。
- t 接近选区末端时，可读取选区外、同 episode 内最多 0.25 秒的合格未来证据；**只保存选区内目标帧的标签**。选择范围不是未来证据截断边界。
- 不自动扩大目标选区、不新增训练保留选区、不修改父标签或动作审核。
- 行为复核的 0.5/1/2/5 秒未来显示跨度不能改变自动计算最多 0.25 秒的规则。

## 5. 位姿、物理标定和数值规则

- 只使用相应手臂的实测末端反馈。默认支持 `observation.ee_pose_left/right`；若提供其他字段选择，必须显式核实其反馈属性和 `[x,y,z,qx,qy,qz,qw]` 布局，禁止用 action、ee_pose_cmd 或另一臂数据代替。
- 原始 observation.state 的维数和模型 use_ee_pose 开关不决定此功能是否可用；检查真实的末端字段和布局。
- 首版保持原始位置已经为米、目标坐标为规范 robot_base（+X 前、+Y 机器人左、+Z 上）的要求，不顺带新增自动单位推断/转换。界面必须显示这一前提。
- 使用已核实的 4×4 刚体变换；检查形状、所有数值有限、末行、旋转正交和 det=+1。噪声参数有限且非负。
- 标定配置绑定数据集/设备标识、反馈字段和手臂；方案/数据/手臂/反馈字段/变换等变化时重新校验，不能无提示沿用另一数据上下文的 verified=true。左右臂可以显式确认共享同一变换，但不能默认另一臂也已核实。
- 平移死区沿用 `max(0.005 m, 3×position_noise)`；无显著位移→none，正负显著位移→positive/negative。显著往返/抵消→该轴 unknown_id，不能以净位移小误判 none。
- 姿态沿用归一化四元数的相对角，以及 `max(5°, 3×angle_noise)`，内部角度单位统一弧度；显著往返→unknown_id。q 与 -q 等价，禁止直接减欧拉角。
- 非有限数值、无效/缺失四元数不得崩溃或被当作 keep。分量独立：某个姿态分量不能计算时，已有合格位置证据的平移分量仍可建议；不得捏造缺失数据。
- none/keep 不表示已经对准或应该保持不动。所有结果都是建议，needs_review=false 也不等于已审核。

## 6. 后端：计算预览、接受草稿和上下文一致性

先设计清晰的类型化请求/响应；允许复用/扩展现有 API 或增加 adviser 专用的能力、预览、应用接口，不必机械保留旧函数内部结构。保留旧调用的合理兼容性及明确错误信息。

### 6.1 预览

- 预览阶段只计算候选和统计，不写 annotations，不改变 reviews/events/selections 或原始数据。按现有行为证据约定登记必要的 future_seen；纯能力检查不读取未来、不登记曝光。
- 返回逐目标帧/臂/字段的候选或结构化跳过原因，以及实际计算窗口、截断原因、映射 ID、现有记录状态。
- 汇总请求帧数、实际候选数、可保存数，以及 inapplicable、undetermined、unapproved、window_too_short、missing_pose、invalid_calibration、invalid_mapping、existing_label 等数量；全部不可用时明确报告 0 项可保存。
- 一次读取所需行、标签、审核和事件并缓存重复门控计算；不要为每帧发 HTTP 请求，也不要为了数值计算解码视频。
- 候选/计算计划绑定 annotation_scheme（project_id/id）、输入数据 fingerprint、episode、源 UUID/源帧、目标帧/选区、手臂、schema version/hash、数据 revision、标定及映射指纹。

### 6.2 接受

- 用户预览后主动“接受为草稿”。后端重新验证上下文、源数据签名、门控和现有记录；不能只相信前端携带的建议值。
- 方案、数据、episode、目标范围、手臂、schema、标签/审核/事件 revision、标定或映射变化后，旧候选失效；过期异步响应不能覆盖新上下文。目标范围指当前帧模式的游标或选区模式的选区；只浏览选区内明细、不改变目标范围时，不必无故废弃整批预览。切换当前帧/选区模式也要校验。
- 默认只填真正缺失且可适用的字段。保留现有人工值、unknown 值、已审核值、needs_review 值及显式单帧 missing 覆盖。不要用一个建议静默覆盖它们。
- 同一次接受中的全部目标变更在一个事务中应用，失败全部回滚，一次撤销/重做覆盖整个批次。复用现有批量保存能力，必要时增加专用后端计划应用路径。
- 现有 BatchAnnotationRequest 最多 128 条，选区逐帧会很容易超限。不要前端拆成一串独立事务；采用有界计划/分页预览与后端单事务应用，或其他可验证的批量方案。
- 逐帧建议保留逐帧语义和来源，不把不同帧答案压成一个统一值。
- 写入 `label_source=rule_suggestion`、行为证据模式、`review_status=draft`；未人工审核前 effective_loss_mask=0。接受候选不自动 approved 动作，不自动审核语义标签。
- 服务端返回真实 saved/skipped/failed 数量；前端只有在实际写入后才报告已保存。禁止像现状一样吞掉单次错误后仍显示整体成功。

## 7. 界面与工作流程

在“行为与校准”的 adviser 区提供清楚的范围和结果：

1. 当前方案及版本、左/右臂选择、范围选择（当前帧/当前选区），显示目标帧号和半开范围。
2. 每个 adviser 分量分别显示：不存在、不支持、门控关闭、门控不确定、可计算或其他阻塞原因；适用和必填分开显示。没有任何支持的字段时隐藏无意义输入或展示简短说明。
3. 显示实际反馈字段、单位要求、坐标变换/噪声/核实状态，以及类别映射；generic 同样可用，不让用户点击后才得到“一律不支持 generic”。
4. 当前帧展示语义名称和实际类别 ID、实际证据窗口及截断原因；选区展示汇总与可分页查看的逐帧结果。
5. 提供预览和接受草稿；无可保存候选、上下文过期或请求处理中时禁用接受，不能只展示一大段 JSON。
6. 明示：“接受为草稿后仍须人工审核；动作 approved 与标签 reviewed 是两套独立审核。”提供现有动作质量审核流程的清楚入口，不自动替用户批准动作。
7. 所有新增界面文字支持中英文。沿用工作台同步、错误提示和快捷键规则。

示例工作流：保存自定义门控标签 → 对可信动作选区设 approved → 核实对应手臂的反馈位姿和标定 → 选择当前帧或选区 → 预览建议 → 接受缺失项为草稿 → 在行为复核中人工检查并审核。

## 8. 追溯、导出和模型兼容

- 每条接受的标签 provenance 保存规则版本、实际窗口（同时给出输入帧与源 UUID/源帧/时间戳）、门控依赖值、类别映射、标定配置/指纹、approved 依据与截断原因；不能只有“rule_suggestion”字符串。
- 保持 `annotations/labels.parquet` 的行为标签、`label_source`、`review_status`、`effective_loss_mask`、`provenance`，以及 `annotations/intervals.parquet` 中原始 annotation/provenance 的一致性；更新手册说明实际字段和 JSON 解析层次。
- 裁剪、导出、重新导入后，不把 provenance 旧输入帧号冒充当前片段坐标；稳定追踪以源 UUID/源帧和 source_map 为依据。
- 保持独立方案间的标签、动作审核、事件、辅助配置、候选计划和撤销隔离；请求继续使用方案绑定。
- 保持 LeRobot 原始数据布局、schema/head_spec 稳定 ID 和现有模型读取契约；未来证据只用于行为监督/审核，不进入 DSE 前向观测。
- 不修改真实工作库/真实导出标签，不在真实数据中创建测试语义标签，不运行真实训练，不顺带升级 dse_model 的 Git 依赖或实验配置。

## 9. 必须覆盖的验收场景

至少增加以下有判别力的测试，使用临时合成数据，不只验证 happy path：

1. **无 phase 的 generic**：global.enable=on 时，左右臂 adviser 的适用门控生效并可建议；off 时跳过；missing/unknown 时按三值规则报告。支持显式 unknown 分支。
2. **非连续且各字段不同的 ID**：例如 x 的 none=11/positive=29/negative=43/unknown=97；orientation 的 unknown_id/key 使用其他值。输出稳定 ID、审核后 mask 和导出读取均正确，不依赖类别顺序或固定头大小。
3. **部分字段/单臂启用**：只启用 left/adviser.x 和 right/adviser.orientation；其他字段不生成、不写入。适用但非必填可计算；必填为真但适用不确定不能强行计算。
4. **动态截断**：门控关闭/不确定、依赖值变化但门控仍为 true、approved 中断、confirmed 同臂事件、episode 末尾和源帧断裂。其他臂或 suggestion 事件不错误截断。
5. **时长和数值边界**：0.1/0.25 秒及不足下限，非固定时间间隔；阈值边界、往返抵消、q/-q、无效矩阵、非有限数值、缺失位姿和仅 orientation 无法计算。
6. **选区逐帧**：同一选区前后段运动相反，各帧答案不同；允许读取选区外合格短未来但不写选区外标签；没有扩大到整 episode。测试超过现有 128 项的接受，以及一次撤销/重做。
7. **保护已有记录**：人工值、unknown、reviewed、needs_review、显式单帧 missing 均不被覆盖；无候选时零写入且不虚报成功；中途失败全部回滚。
8. **过期和并发**：预览后改实际目标范围、手臂、数据集、方案、规则、审核、事件、门控标签或标定，旧候选拒绝应用；单纯浏览选区明细不改变目标范围；异步旧响应不污染当前页面；检查到写入间的竞态不能绕过 revision 校验。
9. **证据隔离**：计算/查看属于 behavior；状态模式不泄露未来答案，草稿 mask=0；人工行为审核后，只有当前门控适用项可变为有效监督。
10. **导出/重开/读取**：当前方案候选草稿及 provenance 能正确导出、显式恢复并由标准 DSEDataset 读取；审过的标签 ID/mask 正确，其他方案不受影响。
11. **旧版兼容**：默认 dse_v1 正常生成候选；三个关闭 adviser 的状态预设不再显示可保存的无效结果；没有 adviser 的 generic 方案有明确说明。
12. **前端交互**：范围切换、逐字段原因、部分可用、过期候选、真实保存数、零保存和中英文。检查控制台和构建，不以静态字符串测试代替关键交互。

## 10. 验证和交付

- 运行相关新增/既有测试，再运行工具仓库完整 Python 回归、TypeScript/Vite 构建、中英文及现有前端检查；仅对实际改动和未解决的问题重复验证。
- 仓库入口可参考 `./scripts/test.sh -q`；前端在 `frontend` 运行 `npm run build`、`npm run test:i18n`、`npm run test:schema-preview`、`npm run test:key-classes`，具体以当前 package.json 为准。
- 当前环境历史上存在禁用 socket 导致 TestClient/事件循环挂起的限制。若重现，诊断并采用有界、隔离的测试启动适配；不要为跑测试修改生产行为，不要把挂起说成通过。
- 如果有浏览器能力，在临时工作项目用合成数据完成“generic 门控 → approved → 当前帧/选区预览 → 接受 → 行为审核 → 导出”的实际交互验收；不能做的验证如实注明。
- 更新中英文 README/手册中“generic 一律不支持 adviser”的旧说明，解释新能力前提、计算范围、窗口截断、类别映射、草稿审核和追溯位置。
- 最终交付：简明设计与实际行为、关键代码改动、验证结果、已知限制和需要用户执行的操作。完成实际实现，不停在计划；不要自动提交或推送。
