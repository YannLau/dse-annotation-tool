# DSE 双臂 LeRobot v2.1 标注工作台

单人、本机运行的 React + TypeScript / FastAPI 应用。读取真实 Parquet 和 AV1 视频，提供三视角四时刻同步浏览、左右臂独立区间标注、逐帧覆盖、schema 门控配置、动作复核和独立片段数据集导出。数据与标注留在本地；没有账号、CDN、云服务或模型 API。

## 启动

需要 Python 3.12、Node.js 20.19+/22.12+、uv、ffmpeg/ffprobe。已安装本项目独立环境及前端依赖，可直接运行：

```bash
cd /home/punk/yann_repo/paper_code_01/dse_annotation_tool
./scripts/run.sh --work ./workspaces/my-project
```

打开 **http://127.0.0.1:8765**。默认仅绑定回环地址。`--work` 指定 SQLite、缓存、日志、备份目录；不同 schema 项目建议使用不同工作目录。`--port` 可指定其他端口。开发模式在另一个终端执行 `cd frontend && npm run dev`，访问 Vite 打印的本机地址。

首次安装或源码修改后的构建：

```bash
uv sync --frozen --group dev
(cd frontend && npm ci && npm run build)
./scripts/run.sh --work ./workspaces/my-project
```

依赖固定在 `uv.lock`、`frontend/package-lock.json`。运行脚本清除外部 `PYTHONPATH`，避免当前机器的 ROS Python 路径污染。只在首次安装依赖时需要网络；已安装后浏览、编辑、导出、重开均可离线执行。

## 使用步骤

1. 顶部输入本机 LeRobot **v2.1** 目录，点击“导入 / 重新绑定”。原始样例为 `/home/punk/yann_repo/paper_code_01/tron2_dataset/lerobot_v2.1_examples`。程序读取目录，不上传视频，也不向源目录写缓存或标签。v3/未知版本明确拒绝。
2. 左侧选择 episode。拖动统一帧游标或输入源帧，中央显示当前三相机与 t−0.6 / t−0.4 / t−0.2 / t 四列历史。缺少历史显示缺失卡，不借用未来帧。点击图片放大，使用＋/−缩放和拖动平移。
3. **状态标注**只展示当前允许历史的 state/末端实测反馈，隐藏 action、末端命令、未来曲线、adviser 答案与阶段轨。**行为复核**展示整段遥测、事件与标签轨，用于动作审核和 adviser。切换模式保留游标与选区；查看行为证据会留下 `future_seen` 记录，不宣称能够消除人的未来记忆。
4. 设置首帧（含）和末帧（不含），例如 `[150,180)` 包含 150–179 帧。点击“保留训练区间”只设训练中心。多个区间、各字段区间和动作审核区间相互独立。
5. 默认选择下拉类别即自动保存**当前单帧草稿**。勾选“应用到明确选区”后，下拉选择会先显示影响/跳过/适用未知/重叠数量，确认后按区间分割覆盖。逐帧覆盖始终优先于批量区间；不会自动向后传播或插值类别。
6. 左右臂分别标注，例如左 `2 · grasp_align`、右 `4 · transport`。适用与必填由各自门控计算。已填写的状态字段须勾选观测证据确认后才能审核；可单独保留有效字段监督。“检查本帧完成条件”会报告缺失必填、未审核或语义冲突，草稿随时保留。
7. “字段与规则”可新增字段/类别、修改显示文本、颜色、快捷键、废弃类别并拖动排序；使用可视化 all/any/not 与 eq/ne/in 条件树分别配置适用和必填。先校验/预览，再发布不可变版本。重复 ID、循环、无效引用、复用旧 ID 会被拒绝。旧标签保留值并变为 needs_review，新字段保持 missing。状态字段不允许通过门控引用未来行为字段。字段快捷键在对应下拉框获得焦点时生效。
8. “行为与校准”设置目标部位、承接区域、W、标定说明；选择具名夹爪通道、单位、方向、开闭阈值、滞回和持续时间后生成**建议事件**。反馈与命令分开标识。动作区间独立保存 approved/rejected/unreviewed 和原因。未验证米制单位与规范基座系变换时，自动 adviser 方向计算禁用；手工标签仍可用，参照系不明确应填 unknown/missing。
9. “导出数据集”填写**不存在的新目录**并选择初始关键/普通采样比例。后台作业显示进度，可取消；失败/取消清理临时目录，不覆盖已有结果。完成后可填入导入路径重新打开。

导入或发布schema会建立新的撤销边界，避免旧快照覆盖新载入的项目状态；旧修订记录仍保留。

快捷键：空格播放暂停，←/→逐帧，`[`/`]`设置区间边界，Tab切换模式，Ctrl+Z撤销，Ctrl+Shift+Z或Ctrl+Y重做。编辑输入框时保留正常文本操作。播放采用墙钟时间推进，解码较慢时跳过显示帧，标签落点仍为整数源帧。

## 标签和 loss mask

- `unknown`：稳定整数类别之一，表示证据不足；审核后可有 mask=1。
- `missing`：没有类别，Parquet `value_id=null`；适配器目标为 −100、mask=0。
- `not_applicable`：阶段/门控明确不适用，保留旧值也不产生监督。
- `undetermined`：依赖 missing/unknown，无法确定适用性，不错误当成 false。
- `draft / reviewed / needs_review`：分别为草稿、已审核、需要复核。字段值或父门控变化后，不自动恢复为已审核。

每字段 mask 为“明确选择的训练中心 AND labeled AND applicable AND reviewed AND schema版本有效”。仅历史/复核上下文、待复核、missing、不适用、适用未知都为0；一个字段未完成不会清除另一个已完成字段。holding 在 phase 缺失时，可通过默认 schema 的 `independent_when_missing` 声明及独立审核贡献监督；明确 idle 时仍不适用。key_zone 从 phase=grasp_align/place_align 派生，phase unknown 时保持未知。

`release ready` 必须有 place_align 且 holding；搬运/放置配 empty、grasp_align 配 holding 会要求复核，不能提交为完成。

## 导出协议

每个不相邻中心区间形成独立新 episode，并按源时间保留最多0.6秒前史、0.25秒尾部审核上下文。相邻/重叠中心合并，不能把有缺口的片段接成虚构连续动作。重新生成 episode/frame/global index 和从0起的时间，保留原 observation/action 数值，按实际物理类型修正元数据，支持多个 chunk。

视频精确解码选帧后重编码为 H.264/yuv420p，保留源分辨率与 FPS；记录编码参数和源 PTS 映射。H.264 是有损重编码，不承诺 AV1 字节或像素完全一致。

```text
meta/{info.json,tasks.jsonl,episodes.jsonl,episodes_stats.jsonl,stats.json}
data/chunk-NNN/episode_NNNNNN.parquet
videos/chunk-NNN/<camera>/episode_NNNNNN.mp4
annotations/
  schema.json                  当前不可变 schema
  schema_history.json          历史 schema
  head_spec.json               动态分类头、类别 ID 顺序
  labels.parquet               双臂/全局长表、nullable类别、状态与mask
  samples.parquet              中心/历史/复核角色、分层与sampling_weight
  source_map.parquet           原始 UUID/帧/时刻、直接父集和媒体映射
  intervals.parquet            区间、逐帧修正、动作审核、事件
  manifest.json                版本、来源、选区、参数、文件校验和
  validation_report.json       完整/部分/无监督中心及类别计数
```

`stats.json` 是标准数据集描述性统计，包含携带的上下文；**模型归一化**请调用只接受明确训练来源组的 `training_statistics`，不要把验证/测试集统计混入训练。类别与 mask 不归一化。采样默认关键70%/普通30%，按来源、事件/阶段组平衡，另行输出权重；无有效监督的中心权重为0。所有同源片段共享 split_group，不自动随机切成 train/test。没有普通池时报告并按实际池回退。

导出含所有视频，未使用指向原始路径的 symlink；可独立复制。原生 LeRobot 读取标准 data/meta/videos，**不会自动应用 DSE annotations 或 loss**。兼容性使用官方 `lerobot==0.3.3`（其代码的 `CODEBASE_VERSION=v2.1`）实测，不能用最新版库替代该测试。

## 接入 DSE

```python
from dse_tool.adapter import DSEDataset, proprioception36, training_statistics

reader = DSEDataset('/path/to/export', cache='/path/to/work/cache/dse')
batch = reader.batch([0, 1])
# images: [B,4,3,3,256,256], RGB float32 [0,1]
# image_valid_mask: [B,4,3]
# state: [B,4,18] 原始精度；state_valid_mask 屏蔽不足历史
# ee_pose_feedback: [B,4,2,7]，另有 ee_pose_valid_mask
# labels / loss_masks: arm -> field_id -> 整数数组
# source_keys / task；默认没有 action、ee_pose_cmd 或未来图像
indices = reader.sample_indices(16, seed=0)  # 全无有效监督会明确报错
```

适配器校验 annotations 校验和，并重新使用共享规则引擎校验 mask。稳定类别 ID 不必连续，训练分类头顺序见 head_spec 的 class_ids；需要时使用 `encode_class_targets` 转为交叉熵索引。若训练环境安装 PyTorch，可用 `to_torch_batch` 递归转换 NumPy 数组。

`proprioception36(state, ee_pose_feedback, verified_config)` 提供18维state + 双臂各位置3维/旋转6维的接口。配置须明确 `verified=true, unit='m', frame='robot_base', transform=4×4刚体矩阵`；未验证会拒绝规范化，原始反馈仍可读取。该工具不训练模型、不改执行代码、不连接真机。裁剪集不是完整 VLA 动作块的自动替代。

## 恢复与验证

工作目录含 `project.sqlite3`（WAL事务）、schemas、cache、backups、logs。重启使用相同 `--work` 即恢复。启动及每20次编辑自动备份，保留10份；恢复备份前先停服务，再将备份复制为 project.sqlite3，移走旧的 `-wal/-shm` 文件。缓存可删除重建。源文件内容/签名变化会阻止使用旧映射，需重新导入；移动目录后输入新路径重新绑定，相同内容复用 UUID。

```bash
./scripts/test.sh -q
# 新目标名，避免覆盖已有成功验收结果
DSE_VALIDATION_RUN=my-new-validation PYTHONPATH= .venv/bin/python scripts/validate_real.py
# 可选：独立官方 loader 环境，精确依赖见 requirements/lerobot-validation.lock
uv venv .validation
uv pip install --python .validation/bin/python --no-deps \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  -r requirements/lerobot-validation.lock
PYTHONPATH= .validation/bin/python scripts/validate_lerobot.py /path/to/moved/export
```

真实验收报告见 `docs/VALIDATION.md`、`docs/real-validation.json`、`docs/native-validation.json`。真实样例导出没有伪造人工语义标签；含 unknown、冲突、门控、mask 的测试使用合成夹具。机器未验证的单位/标定仍需由使用者核对；仅对14条样例和测试夹具作实测，不推断海量性能。
