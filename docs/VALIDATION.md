# 验证报告 · 2026-10-31

[English](VALIDATION_EN.md) | 简体中文

2026-11-10补充：Python包0.2.0完整回归 **66 passed**，新增可变维数、31维具名标量、独立末端转换及旧36维兼容验证。真实31维数据API导入、三相机首/中/末帧和导出重开结果见 [机器记录](named-scalar-real-validation.json)，两项目接口见 [接入说明](STATE_LAYOUT_INTEGRATION.md)。以下保留2026-10-31的历史验收数据，不表示本次重新运行了历史GPU或官方loader验收。

实现与验证在独立目录 `dse_annotation_tool` 中完成，已执行 `git init`。原始数据目录只读。以下为实际执行结果，不是计划或模拟结果。

## 自动验证

- Python：`./scripts/test.sh -q`，**18 passed**。涵盖三值规则、unknown/missing mask、左右臂独立与显式跨臂依赖、范围分割、逐帧覆盖、撤销重做及重启、schema 迁移与动态 head、并发 API、媒体/版本拒绝、背景导出取消、现存目标保护、搬移重读、再次裁剪的祖先映射、文件校验和、schema/导入撤销边界。
- 前端：TypeScript 检查与 Vite 生产构建通过。安装时 npm 审计报告0个漏洞；生产 JS 约593 KB（gzip约180 KB），构建器提示单 chunk 大于500 KB，属于已知体积提示。
- 官方兼容性：未修改的 `lerobot.datasets.lerobot_dataset.LeRobotDataset`，`lerobot=0.3.3`、`torch=2.7.1+cpu`、`datasets=3.6.0`、`video_backend=pyav`。独立 `.validation` 环境，禁止 Hub 联网；成功读取搬移后的 **2个episode / 105行**，抽查首、中、末数据及三路480×640视频。精确环境见 `requirements/lerobot-validation.lock`，输出见 `native-validation.json`。PyTorch/LeRobot不进入Web服务依赖。

官方 v0.3.3 中 [CODEBASE_VERSION 为 v2.1](https://github.com/huggingface/lerobot/blob/v0.3.3/src/lerobot/datasets/lerobot_dataset.py)。这里的兼容结论由实际 loader 重读支持，没有用自写 reader 代替官方验收。

## 真实数据

- 来源：`/home/punk/yann_repo/paper_code_01/tron2_dataset/lerobot_v2.1_examples`。
- **14 episodes / 13,432 rows / 3 cameras / 30 FPS**；逐个打开14个 episode。首次导入读取 packet PTS 索引并解码每路首帧，不把全部视频解码缓存进内存。
- episode0：299帧；t=5秒的历史索引为132 / 138 / 144 / 150，对应4.4 / 4.6 / 4.8 / 5.0秒。浏览器中看到四时刻三相机。
- 元数据声明float32而实际为double的差异共98条，保留记录；导出数值列逐行对应原始来源，物理精度保持并同步元数据。
- 测试中心 `[150,180)` 与 `[220,245)`，合计55个中心帧；导出2个独立episode、105行（50行仅上下文）。按时间保留历史及尾部，不拼接间隔。
- 对两个片段的首帧、中间帧、末帧与三路源图逐一比较（共18组），PTS映射正确；有损H.264重编码最大平均像素绝对误差为1.7633/255，不宣称像素完全一致。
- 复制导出到另一个目录后，工具可重开，DSE wrapper实际组装图像batch `[2, 4, 3, 3, 256, 256]`。原生loader也读取该复制目录，未引用原始视频路径。
- 所有真实语义标签保持 **missing**，没有生成伪造的人类审核真值。unknown监督、缺失/不适用/context-only屏蔽与采样测试使用合成夹具。
- 导入前后完整内容指纹一致：`ee5011e1baece32d12286f19e25ca9b9ed69c2470f3245e0621ad6a77f91d9d9`。未在原数据目录写入缓存、工作库或导出。

## 实测性能

设备：Intel(R) Core(TM) Ultra 7 270K Plus，24逻辑核心，Linux-7.0.0-28-generic-x86_64-with-glibc2.39。

| 项目 | 时间/内存 |
|---|---:|
| 新工作项目完整导入 | 2.563秒 |
| 12图历史面板 JPEG冷缓存 | 0.112秒 |
| 同一面板热缓存 | 0.020秒 |
| 单相机seek冷缓存 | 0.0078秒 |
| 单相机seek热缓存 | 0.0019秒 |
| 2片段/105行/6路输出视频导出 | 4.475秒 |
| 验收进程最大RSS | 486.5MiB |

以上为服务请求/批处理耗时，非浏览器端到端渲染时间。冷缓存指应用JPEG缓存；没有清空OS页缓存。仅14条样例，不外推海量性能。内存缓存最多3个episode表、9个媒体索引；JPEG磁盘缓存256MiB，每来源索引缓存64MiB，超限重建。

## 浏览器交互

在本机内嵌浏览器执行：真实导入→t=5秒同步→连续100/220/150帧快速跳转→逐帧151→播放恢复→状态/行为切换。测试过默认窄视口和1440×1000桌面布局；旧请求返回前锁定编辑/隐藏旧画面。

标签写入使用清楚命名的 **Synthetic test fixture**：左grasp_align/右transport→选择[25,30)→区间覆盖预览与确认→配置visibility_check及门控→发布v2→出现动态表单与needs_review→单帧27覆盖unknown→撤销为grasp_align、重做unknown→后台导出→界面重导入→新片段帧20保留原源帧27的unknown与新增字段→服务重启恢复。

正式实现把导入与schema发布设为撤销边界：旧修订仍保存，不能用导入前的快照覆盖新载入状态。普通编辑仍可撤销/重做并重启恢复。

## 可复核文件

- `exports/real-acceptance-final/`：真实独立导出，中心55帧；标签全部missing。
- `exports/real-acceptance-final-moved/`：用于搬移/官方loader验收的副本。
- `workspaces/real-acceptance-final/`：示例工作项目，可用 `./scripts/run.sh --work ./workspaces/real-acceptance-final` 直接重开。
- `exports/ui-acceptance/` 与 `workspaces/ui-fixture/`：合成夹具与界面测试产物；不是真实训练真值。
- `real-validation.json`、`native-validation.json`：原始测量、映射与loader输出。
- `workbench.png`：真实画面的界面截图。

## 实测范围与待确认项

真实坐标单位、变换及夹爪阈值未由使用者核对，因此默认禁用自动事件/方向预标注；手工标注及所有不依赖标定的功能可用。尚未进行全42路视频逐帧穷举解码、超大数据集压力测试、长时间会话、真实断电/文件系统故障注入；也未训练DSE/VLA或连接真机。schema发布采用保守迁移，旧字段统一needs_review，新字段missing，需用户复核后恢复监督。
