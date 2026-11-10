# 0.2.0：状态维数与模型接入

标注工具保留原始 `observation.state` 和 `action` 的维数、名称、数值及物理存储类型。两者不必同维。具名附加特征 `shape=[1]` 可以保存为数值标量或单元素数组；曲线仅在响应中统一为单通道数组。原始数据与导出不因训练选择而删除固定头部。

`dse_model>=0.2.0` 根据 `features.shape/names` 选择输入，使用 `preprocessing.state_exclude` 排除固定头部，或用 `state_fields` 明确选择及排序。模型保存选定的 D 维布局，归一化、输入投影、checkpoint 和推理使用同一布局。16维与18维数据只有在选定通道的含义、顺序、单位一致时才可混用；灵巧手增加关节不自动增加逐指标签。当前默认适配器仍要求三相机与双臂布局。

共享 Python 接口：

| 接口 | 输入与输出 |
|---|---|
| `DSEDataset` | 原始状态 `[T,D]`，batch `[B,T,D]`，保持源数据布局 |
| `ee_pose_features(poses, config)` | 有效位置/四元数 `[...,7]` → 位置/旋转6D `[...,9]`，与状态维数无关 |
| `proprioception(state, poses, config)` | `[...,D]` 与 `[...,2,7]` → `[...,D+18]` |
| `proprioception36` | 兼容旧18维状态，继续返回36维特征 |

末端转换要求经过核实的单位及基座变换；先按有效 mask 取真实位姿，不把 padding 的全零四元数传入。模型直接调用独立的末端转换接口，无需构造假18维状态或复制另一臂位姿。`use_ee_pose=false` 时不要求末端字段或坐标标定。

本次回归覆盖16/18/31/48维、31维具名标量、来源值保留、非单位刚体变换以及旧36维接口。真实31维数据的API导入、三相机首/中/末帧、导出及重开结果见 [机器记录](named-scalar-real-validation.json)。真实语义标签未创建；测试训练只使用合成标签。

工具提交应先推送，再推送模型中引用它的 `uv.lock`。模型使用独立环境中的 Git 快照，不会自动读取旁边的源码目录；更改锁文件后需要 `uv sync --locked` 并重启相关 Python 进程。本地 Git 提交验证不等于远端已发布。

## English summary

Version 0.2.0 preserves arbitrary source state/action dimensions and named scalar storage. The model selects and records channels without changing exports. The shared `ee_pose_features` API converts valid `[...,7]` poses to `[...,9]` independently of state dimensions; `proprioception36` remains compatible. Filter missing poses before conversion and supply verified units/transforms. Publish the tool commit before the model lockfile referencing it. Source-only tests do not replace checking the installed Git snapshot.
