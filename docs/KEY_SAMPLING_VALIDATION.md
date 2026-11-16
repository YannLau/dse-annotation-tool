# 关键类别采样验证 · 2026-11-16

导出页可选择 left/right/global 字段的关键类别；规则仅影响采样池和分组，不改变 schema、预测头或监督 mask。模型始终使用自身 `sampling.key_ratio` 在最终训练子集重算权重。

- 标注工具完整 Python 回归：107 passed；新增测试覆盖默认兼容、自定义作用域/类别、非法 ID、规则版本、确认事件邻域、冻结快照、分组、无监督和单池回退。
- 模型完整 Python 回归：101 passed；新增测试覆盖导出规则追踪、指纹校验、训练划分重算、忽略导出权重/比例，以及两种输入顺序下的同源 pool/event_group 冲突。
- TypeScript/Vite 生产构建、关键类别组件测试、原规则预览组件测试和中英文消息检查通过；改动 Python 的 Ruff 检查及两个项目的 diff whitespace 检查通过。
- 本机隔离服务和无头 Chrome 验证：实际勾选、刷新恢复、方案切换隔离、原 DSE 默认八项选择、中英文界面、清空类别、提交和完成真实导出。无页面 JavaScript 异常。
- 两项目贯通验证使用合成夹具：4 个来源 episode、32 个训练候选中心，通用 each_arm/global 字段及不连续类别 ID。由工具工作区生成新导出，再由模型当前安装的 DSETool 读取器读取，并完成 CPU tiny_test 一轮训练。导出比例 0.9，模型比例 0.4；训练记录实际 key/background 概率质量为 0.4/0.6，规则指纹同时保存在 sampling.json 和检查点中。

工具测试在当前禁止创建 socket 的沙箱中通过临时 asyncio 定时唤醒脚本运行 pytest；该适配仅在 /tmp 测试启动器中，未进入项目运行代码。浏览器服务在批准的本机端口使用 /tmp 合成数据；未操作真实标注工作库或改变模型依赖安装/锁文件。此验证检验数据与训练接口，不评估模型的真实业务识别效果。

接口及行为见 [数据集手册 §9.2–9.3](ANNOTATED_DATASET_MANUAL_ZH.md#92-key_zonepool-和-event_group-的区别)。
