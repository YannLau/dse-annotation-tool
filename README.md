# DSE Dual-Arm LeRobot v2.1 Annotation Workbench

English | [简体中文](README_ZH.md)

A single-user React + TypeScript / FastAPI application that runs locally. It reads real Parquet files and AV1 videos and provides synchronized three-camera viewing with configurable history, independent annotation ranges for each arm, per-frame overrides, schema gating rules, action review, and standalone segment-dataset export. Data and annotations stay local; there are no accounts, CDNs, cloud services, or model APIs.

Use the language selector in the upper-right corner to switch between Simplified Chinese and English. Your browser remembers the choice after refresh. Switching preserves the page, frame, selection, and pending drafts. In Fields & rules, custom fields/categories can have English display names, and fields can have English descriptions. Without an English category label, built-in labels use their translations and custom categories fall back to their stable keys.

See [Interface Language Switching](docs/UI_LANGUAGE_2026-11-08_EN.md) for updating an existing installation, English properties, and validation steps.

For the files added by annotation, column definitions, and training integration requirements, see the [DSE-Annotated LeRobot v2.1 Dataset Manual](docs/ANNOTATED_DATASET_MANUAL_EN.md).

Python package 0.2.0 supports variable state dimensions, named scalar channels, and a state-independent pose conversion API shared with the model. See [State Layout and Model Integration](docs/STATE_LAYOUT_INTEGRATION.md) for contracts, compatibility, validation, and cross-repository updates.

## Launch

Requires Python 3.12, Node.js 20.19+/22.12+, uv, and ffmpeg/ffprobe. With the project's isolated environment and frontend dependencies already installed, run:

```bash
cd /home/punk/yann_repo/paper_code_01/dse_annotation_tool
./scripts/run.sh --work ./workspaces/my-project
```

Open **http://127.0.0.1:8765**.

The server binds only to the loopback address by default.

`--work` sets the directory for SQLite, caches, logs, and backups. Use separate work directories for projects with different schemas.

`--port` selects a different port.

For development, run `cd frontend && npm run dev` in another terminal and open the local address printed by Vite.

First installation, or rebuilding after source changes:

```bash
uv sync --locked --group dev
(cd frontend && npm ci && npm run build)
./scripts/run.sh --work ./workspaces/my-project
```

Dependencies are locked in `uv.lock` and `frontend/package-lock.json`. The launch script clears external `PYTHONPATH` values to avoid interference from the machine's ROS Python paths. Network access is needed only when first installing dependencies; browsing, editing, exporting, and reopening work offline afterward.

## Workflow

The interface supports both languages. Chinese labels and their English meanings are given below for reference.

1. Click **选择文件夹** (Select folder) at the top to open the system folder picker, or enter a local LeRobot **v2.1** directory and click **导入 / 重新绑定** (Import / rebind). Canceling preserves the previous path. Without a desktop environment, the app automatically opens **页面内浏览** (Browse in page), with parent-directory and home navigation, filtering, and hidden-folder display. This selects a directory without uploading files. The original sample is at `/home/punk/yann_repo/paper_code_01/tron2_dataset/lerobot_v2.1_examples`. The app reads the directory without uploading videos or writing caches or labels into it. v3 and unknown versions are explicitly rejected.
2. Select an episode on the left. Drag the shared frame cursor or enter a source frame to view the current three cameras and configurable history. On the home page, set **历史跨度（秒）/时刻数（含当前）** (History span in seconds / timestamps including current) and apply. Defaults are 0.6 seconds / 4 timestamps; 5 seconds / 3 timestamps means t−5, t−2.5, t. Settings persist, and review provenance records the history configuration used. Unavailable history appears as missing cards without borrowing future frames. Click an image to enlarge it; use +/− to zoom and drag to pan.
3. **状态标注** (State annotation) displays only state and measured end-effector feedback within the permitted history. It hides action, end-effector commands, future curves, adviser answers, and phase tracks. **行为复核** (Behavior review) displays full-episode telemetry, events, label tracks, and future reference images from all three cameras, at t+0.1 / t+0.2 / t+0.25 seconds by default. The reference horizon can also be 0.5/1/2/5 seconds. Future images can be enlarged without moving the current decision frame; unavailable future is shown as missing cards. Automatic adviser computation still uses at most 0.25 seconds of continuously approved actions. Switching modes preserves the cursor and selection. Viewing behavior evidence records `future_seen`; the tool does not claim to erase a person's memory of the future.
4. Set an inclusive start and exclusive end, for example `[150,180)` includes frames 150–179. **保留训练区间** (Keep training range) selects training centers only. Multiple selections, field-annotation ranges, and action-review ranges are independent.
5. By default, choosing a category automatically saves a **draft for the current single frame**. Enable **应用到明确选区** (Apply to the explicit selection) to choose **保留单帧例外** (Preserve single-frame exceptions, the default) or **统一覆盖，包括单帧** (Overwrite uniformly, including single frames). Overwrite replaces single-frame annotations, including explicit missing values, only for the assigned fields and eligible frames within the selection. Other fields, arms, and records outside the selection remain; inapplicable frames are skipped. Changing this policy updates all pending range drafts, and the preview counts single-frame field/frame pairs that will be overwritten or preserved. Choosing a phase immediately displays the draft and recalculates required fields and applicability. Fill the remaining fields, then click **确认保存全部 N 项** (Confirm and save all N items) once. Affected/skipped/undetermined/overlap counts cover the whole group. Failure rolls back the whole group; undo/redo also treats it as one operation. Unconfirmed drafts and the override policy persist in the browser and SQLite preferences and can be restored after refresh. Older drafts default to preserving single-frame exceptions. Remove individual items or cancel the whole group as needed. Pending drafts do not become formal labels; categories are never automatically propagated forward or interpolated.
6. Annotate arms separately, for example left `2 · grasp_align` and right `4 · transport`. Each arm's gates determine applicability and required fields. Label-review scope can be the current frame or selection. After confirming the permitted observational evidence for filled state fields, review an entire range at once. Existing per-frame differences and overrides remain; missing, inapplicable, undetermined, deprecated, and semantically conflicting entries are skipped without filling labels. **预检查审核范围** (Precheck review range) is optional. One submission can be undone once; partial valid field supervision is supported. State and behavior modes review their respective fields. **检查本帧完成条件** (Check completion conditions for this frame) only reports missing required fields, review gaps, and conflicts for the current frame; it does not change masks. See [Range Review](docs/RANGE_REVIEW_2026-10-31_EN.md).
7. **字段与规则** (Fields and rules) lets you add/delete fields and categories, change display text, colors, and shortcuts, deprecate categories, and reorder them by dragging. Visual all/any/not and eq/ne/in trees configure applicability and requiredness separately. Validate/preview before publishing an immutable version. Delete first lists dependencies and historical usage. References from other rules, independent-review conditions, unknown_id, or built-in consistency checks block direct deletion. The foundational phase field cannot be removed. Deletion first changes the configuration draft; publication removes entries from the active form and head_spec. Old versions, raw labels, and retired identities remain, and deleted keys/IDs cannot be reused. Historical values of deleted categories appear as missing/needs_review with mask=0 in the current version rather than being reinterpreted. Unpublished deletions can be undone while preserving later edits. Duplicate IDs, cycles, invalid references, and identity reuse are rejected. Old labels retain their values and become needs_review; new fields remain missing. State-field gates cannot reference future behavior fields. Field shortcuts work when the corresponding dropdown has focus.
8. In **行为与校准** (Behavior and calibration), configure the target part, receiving region, W, and calibration notes. Select a named gripper channel, units, direction, open/closed thresholds, hysteresis, and minimum duration to generate **suggested events**. Feedback and commands are labeled separately. Action ranges separately store approved/rejected/unreviewed status and a reason. Automatic adviser direction computation is disabled until metric units and the canonical robot-base transform are verified. Manual labels remain available; use unknown/missing when the reference frame is unclear.
9. In **导出数据集** (Export dataset), enter a **new directory that does not exist** and choose the initial key/background sampling ratio. The background job displays progress and can be canceled. Failure/cancellation removes the temporary directory and does not overwrite existing results. Reopen a completed export through the import path.
10. In **导出检查器** (Export inspector), select an exported directory with the system picker or in-page browser, then click **打开并检查** (Open and inspect). The inspector shows training centers, history, review tails, and overlapping copies in source-frame coordinates. Inspect videos, observations, nullable labels, loss masks, source mapping, and model history inputs frame by frame. Compare supervision for the same source frame in different segments, and page through actual Parquet/JSON contents. Importing into an annotation project is unnecessary; inspection does not change the dataset or labels. See the [illustrated guide and synthetic example](docs/EXPORT_INSPECTOR_2026-10-31_EN.md).

Importing a dataset or publishing a schema establishes a new undo boundary so that an old snapshot cannot replace newly loaded project state. Old revision records remain available.

Shortcuts: Space plays/pauses; ←/→ steps frames; `[`/`]` sets range boundaries; Tab switches modes; Ctrl+Z undoes; Ctrl+Shift+Z or Ctrl+Y redoes. Text inputs keep normal editing behavior. Playback advances according to wall-clock time and skips displayed frames when decoding is slow; annotation targets remain integer source frames.

The playback and range-control panel floats near the bottom of the window. Drag its top handle to reposition it. Position is saved per work directory and restored on refresh or reopening the same workspace; different workspaces remember separate positions. Resizing the window or increasing timeline height clamps the panel into view. **回到底部** (Return to bottom) restores the default. With the handle focused, arrow keys move it, Shift accelerates movement, and Home returns it to the bottom.

During frame changes, the previous complete image group remains visible. The new current/history/future images, labels, and telemetry replace it together after every new image has decoded; cameras are no longer covered by a loading overlay. Image captions always identify the displayed frames, and label editing waits until the requested frame is ready. Images use stable URLs and a bounded decoded-image cache; labels are fetched every time. Dataset, episode, evidence-mode, and reference-window changes isolate previous images. Returning to state mode also immediately closes enlarged future images. See [Smooth Frame Switching](docs/SMOOTH_FRAME_SWITCHING_2026-11-01_EN.md).

## Labels and Loss Masks

- `unknown`: a stable integer category meaning insufficient evidence; it may have mask=1 after review.
- `missing`: no category, represented by Parquet `value_id=null`; the adapter produces target −100 and mask=0.
- `not_applicable`: the phase/gate explicitly makes the field inapplicable; an old value may remain but contributes no supervision.
- `undetermined`: missing/unknown dependencies prevent deciding applicability; this is not incorrectly treated as false.
- `draft / reviewed / needs_review`: draft, reviewed, and requiring another review. Changing a value or parent gate does not automatically restore reviewed status.

Each field's mask requires an explicitly selected training center AND labeled AND applicable AND reviewed AND a valid schema version. History/review-only context, needs_review, missing, inapplicable, and undetermined entries have mask=0. An incomplete field does not erase another field's valid supervision. When phase is missing, holding can contribute supervision through the default schema's `independent_when_missing` declaration and independent review; it remains inapplicable for explicit idle. key_zone derives from phase=grasp_align/place_align and stays unknown when phase is unknown.

`release ready` requires place_align and holding. Transport/place with empty, or grasp_align with holding, requires review and cannot be submitted as complete.

## Export Protocol

Each nonadjacent center range becomes a standalone new episode, retaining configurable history by source time (0.6 seconds by default) and a 0.25-second review tail. The export page can retain more history, such as 5 seconds, for later experiments with different spans/frame counts. The floor row needed by sparse sampling is also retained for irregular timestamps. The manifest records the default history configuration and retained capacity. Exceeding an older crop's capacity requires re-exporting from the original data. Adjacent/overlapping centers are merged; segments separated by gaps are never concatenated into fictitious continuous motion. Episode/frame/global indices and time from zero are regenerated. Original observation/action values are preserved, metadata is corrected to match physical types, and multiple chunks are supported.

Videos are decoded, selected by exact frame mapping, and re-encoded as H.264/yuv420p at the source resolution and FPS. Encoding parameters and source PTS mappings are recorded. H.264 re-encoding is lossy; byte or pixel identity with the AV1 source is not promised.

```text
meta/{info.json,tasks.jsonl,episodes.jsonl,episodes_stats.jsonl,stats.json}
data/chunk-NNN/episode_NNNNNN.parquet
videos/chunk-NNN/<camera>/episode_NNNNNN.mp4
annotations/
  schema.json                  Current immutable schema
  schema_history.json          Historical schemas
  head_spec.json               Dynamic classification heads and category-ID order
  labels.parquet               Per-arm/global long table, nullable categories, statuses and masks
  samples.parquet              Center/history/review roles, strata and sampling_weight
  source_map.parquet           Original UUID/frame/time, direct parent and media mapping
  intervals.parquet            Ranges, per-frame corrections, action reviews and events
  manifest.json                Versions, sources, selections, parameters and file checksums
  validation_report.json       Complete/partial/unsupervised centers and category counts
```

`stats.json` contains standard descriptive dataset statistics, including retained context. For **model normalization**, call `training_statistics` with explicitly selected training-source groups; do not mix validation/test statistics into training. Categories and masks are not normalized. Sampling defaults to key 70% / background 30%, balanced by source and event/phase groups, with separate weights. Centers without valid supervision have weight 0. All same-source segments share a split_group; no automatic random train/test split is performed. If the background pool is absent, the report notes it and falls back to the available pool.

Exports contain all videos without symlinks to original paths and can be copied independently. Native LeRobot reads standard data/meta/videos but **does not automatically apply DSE annotations or loss**. Compatibility was tested with official `lerobot==0.3.3` (whose `CODEBASE_VERSION=v2.1`); the latest library cannot substitute for that specific test.

## DSE Integration

```python
from dse_tool.adapter import DSEDataset, proprioception, training_statistics

reader = DSEDataset('/path/to/export', cache='/path/to/work/cache/dse')
# Inherit the manifest by default; override for an experiment only if
# the export retained at least 5 seconds of history:
# reader = DSEDataset('/path/to/export', cache='/path/to/work/cache/dse',
#     history_config={"horizon_seconds": 5.0, "num_frames": 3})
batch = reader.batch([0, 1])
# images: [B,T,3,3,256,256], default T=4, RGB float32 [0,1]
# image_valid_mask: [B,T,3]
# state: [B,T,D], D from source metadata (18 in the Tron2 sample); history has a validity mask
# ee_pose_feedback: [B,T,2,7], with ee_pose_valid_mask
# labels / loss_masks: arm -> field_id -> integer arrays
# history_target_offsets / history_actual_offsets: [B,T], relative seconds;
# missing actual times are NaN; zero them using the mask before time encoding
# history_frame_indices: [B,T], missing -1; history_config records the experiment
# source_keys / task; no action, ee_pose_cmd, or future images
indices = reader.sample_indices(16, seed=0)  # Raises if no center has valid supervision
```

H=0, T=1 supports current-frame-only ablation. T includes the current frame, with all three cameras at each timestamp. Models must support dynamic T and actual relative-time encoding. Shorter or sparser history may remove observable evidence for existing state labels, requiring review under the new configuration. The tool retains provenance without automatically changing earlier review decisions.

The adapter verifies annotation checksums and rechecks masks with the shared rule engine. Stable category IDs need not be contiguous; classification output order comes from head_spec's class_ids. Use `encode_class_targets` when cross-entropy indices are needed. With PyTorch installed in the training environment, `to_torch_batch` recursively converts NumPy arrays.

Annotation, telemetry, and export follow `features.shape/names`, supporting 16D and higher-dimensional state/action vectors. State and action need not have equal dimensions. Exports preserve all original channels and values. The downstream `dse_model` can exclude fixed head channels with `preprocessing.state_exclude` or select/order channels with `state_fields`, without changing exports. More finger joints do not automatically create finger-level labels; default labels still describe each arm/whole-hand grasp.

Named one-channel auxiliary features declared as `shape=[1]` may be stored as Parquet scalars, such as `complementary_info.is_intervention=0.0`, or as `[0.0]`. Telemetry presents either as a single channel; export preserves the original physical representation. State/action continue to require vectors. See the [real 31D validation report](docs/named-scalar-real-validation.json) covering import, first/middle/last images from three cameras, cropped export, and reopening.

`proprioception(state, ee_pose_feedback, verified_config)` combines D-dimensional state with position3/rotation6 for each arm, producing D+18 features. The original `proprioception36` remains a compatibility interface for 18D state. Configuration must specify `verified=true, unit='m', frame='robot_base', transform=<4×4 rigid transform>`. Unverified canonicalization is rejected; raw feedback remains readable. The tool does not train models, change execution code, or connect to a physical robot. A cropped dataset is not an automatic substitute for complete VLA action chunks.

For pose-only conversion, `ee_pose_features(valid_poses, verified_config)` maps valid `[...,7]` feedback to `[...,9]` independently of state dimensions; model 0.2.0 shares this API. Filter valid poses first so padding quaternions or a missing arm cannot affect another arm. Existing installations need the sync/build steps above and a service restart with the same `--work`; updating this project does not update the model's separate environment or lockfile.

Training integration boundaries: images have not undergone SigLIP processor normalization. Filter valid timestamps before proprioception conversion; padding's zero quaternions are not real inputs. `training_statistics` covers raw D-dimensional state at center frames from supplied training sources, without full D+18 statistics or standardization; `dse_model` separately fits selected channels and history. Networks apply input masks and losses apply label masks. A reader uses uniform H/T and returns all modalities; batches need consistent input layouts. The default DSE adapter and current model still require three cameras and the dual-arm pose layout. See the [Training Specification and Implementation Boundaries (Chinese)](../NOTES-Yann/innovation_research_handoff/DSE_OUTPUT_MODEL_TRAINING_SPEC_V1_2026-10-29.md).

2026-11-01 adviser-candidate correction: significant out-and-back translation/rotation no longer merely sets `needs_review` while retaining a definite net direction/keep. Affected components become unknown; other components remain. The rule version is `approved-displacement-2`. Accepting a candidate still saves only a draft without automatic review; existing drafts/labels are not rewritten.

## Recovery and Validation

The work directory contains `project.sqlite3` (WAL transactions), schemas, cache, backups, and logs. Restart with the same `--work` to recover. Automatic backups run at startup and every 20 edits, retaining 10 files. Before restoring, stop the service, copy the backup to project.sqlite3, and move aside old `-wal/-shm` files. Caches can be deleted and rebuilt. Changed source contents/signatures block old mappings and require re-import. After moving a source directory, enter its new path to rebind; identical content reuses the UUID.

```bash
./scripts/test.sh -q
npm run test:i18n --prefix frontend
# Use a new target name to preserve existing successful acceptance results
DSE_VALIDATION_RUN=my-new-validation PYTHONPATH= .venv/bin/python scripts/validate_real.py
# Optional: isolated official-loader environment, with exact dependencies locked below
uv venv .validation
uv pip install --python .validation/bin/python --no-deps \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  -r requirements/lerobot-validation.lock
PYTHONPATH= .validation/bin/python scripts/validate_lerobot.py /path/to/moved/export
```

See [Configurable History](docs/CONFIGURABLE_HISTORY_2026-10-31_EN.md) for interfaces, experiment conventions, and validation.

See [Workflow Fixes](docs/WORKFLOW_FIXES_2026-10-31_EN.md) for the interaction fixes and their validation.

Real-data acceptance results are in [VALIDATION](docs/VALIDATION_EN.md), `docs/real-validation.json`, and `docs/native-validation.json`. Real sample exports contain no fabricated human semantic labels. Tests involving unknown, conflicts, gates, and masks use synthetic fixtures. Units/calibration not verified on the machine still require user verification. Measurements cover only 14 sample episodes and test fixtures and do not establish large-scale performance.
