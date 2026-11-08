# DSE-Annotated LeRobot v2.1 Dataset Manual

English | [简体中文](ANNOTATED_DATASET_MANUAL_ZH.md)

Date: 2026-11-03; interface language and English display properties updated on 2026-11-08. Covers the `LeRobot-v2.1+DSE-1` export format of this `dse_annotation_tool` project; the original export-protocol baseline checked was `9b1246f`. Actual exported files were also checked. This manual describes the current implementation. Later model, training, and protocol ideas in research documents are not automatically implemented features.

This manual is for annotators, recipients of exported data, and developers building DSE or VLA training programs. It explains what is added to the directory, what each field means, which labels can supervise training, which data is context only, and what training code must still handle.

## Reading Guide

- [1. What the Tool Does and Why](#1-what-the-tool-does-and-why)
- [2. Original Data, Work Projects, and Exported Data](#2-original-data-work-projects-and-exported-data)
- [3. Export Directory and Added Files](#3-export-directory-and-added-files)
- [4. Training Centers and Context](#4-training-centers-and-context)
- [5. Default Annotation Content](#5-default-annotation-content)
- [6. schema.json: Label Dictionary and Rules](#6-schemajson-label-dictionary-and-rules)
- [7. schema_history.json and head_spec.json](#7-schema_historyjson-and-head_specjson)
- [8. labels.parquet: Per-Frame, Per-Arm, Per-Field Labels](#8-labelsparquet-per-frame-per-arm-per-field-labels)
- [9. samples.parquet: Eligibility, Roles, and Sampling](#9-samplesparquet-eligibility-roles-and-sampling)
- [10. source_map.parquet: Source and Media Mapping](#10-source_mapparquet-source-and-media-mapping)
- [11. intervals.parquet: Ranges, Point Overrides, Action Reviews, and Events](#11-intervalsparquet-ranges-point-overrides-action-reviews-and-events)
- [12. manifest.json: Export Delivery Manifest](#12-manifestjson-export-delivery-manifest)
- [13. validation_report.json: Reading the Export Report](#13-validation_reportjson-reading-the-export-report)
- [14. Changes to Standard LeRobot Files](#14-changes-to-standard-lerobot-files)
- [15. What Downstream DSE Loading Actually Returns](#15-what-downstream-dse-loading-actually-returns)
- [16. Explicit Responsibilities of Training Code](#16-explicit-responsibilities-of-training-code)
- [17. Actual Examples and Inspection](#17-actual-examples-and-inspection)
- [18. Misconceptions, Glossary, and Implementation References](#18-misconceptions-glossary-and-implementation-references)

## 1. What the Tool Does and Why

Original LeRobot data records **what the robot saw, its measured state, and the demonstration actions**. It generally does not tell a training program, frame by frame, whether the left arm is grasping or transporting, whether it holds the designated object, whether visible conditions support closing, or how a qualified demonstration adjusts next.

The tool adds human semantic labels and records their applicability, review status, and evidence provenance. Users explicitly select training centers; exports carry their historical images, action-quality reviews, and source-tracing information.

In everyday terms, **the robot recording is the textbook, labels are a teacher's notes at particular moments, the schema defines the annotation vocabulary and rules, and loss masks tell training which notes may currently be graded against.** Writing a note, saving it, and allowing it to count toward training are separate steps.

The four main purposes are:

1. Let the arms occupy different phases and receive supervision independently.
2. Enable grasp/release-readiness questions only in applicable phases, avoiding false negative labels for inapplicable questions.
3. Preserve missing labels, insufficient evidence, and unreviewed labels separately, supporting gradual annotation and partial-field training.
4. Preserve source identity through cropping, relocation, and repeated export, avoiding fictitious timelines and train/test leakage.

DSE is the semantic-estimation module planned for this project: using images, history, and permitted measured information up to the current time to predict phase, holding, and local-adjustment semantics. These predictions may later become textual conditions for a VLA. **The current tool delivers data and reading interfaces; it does not implement a DSE network, complete training loop, or VLA prompt integration.**

## 2. Original Data, Work Projects, and Exported Data

| Object | Typical location | Contents | When it changes |
|---|---|---|---|
| Original LeRobot data | `tron2_dataset/lerobot_v2.1_examples/` | Original Parquet, video, and metadata | Read-only to the tool; annotation adds no files here |
| Annotation work project | Directory selected with `--work` | Saved labels, selections, rules, action reviews, preferences, backups, and caches, with records in SQLite | During editing, review, undo, and schema publication |
| Standalone exported dataset | A new directory selected for export | Cropped standard LeRobot data plus `annotations/` attachments | A snapshot of this export; later edits do not update it |

Typical work-project contents:

```text
my-work/
├── project.sqlite3           # Current project database
├── project.sqlite3-wal       # SQLite transaction file that may exist while running
├── project.sqlite3-shm       # SQLite auxiliary file that may exist while running
├── schemas/                 # Published schema-version files
├── backups/                 # SQLite backups
├── cache/                   # Rebuildable video indices and image caches
└── logs/                    # Runtime logs
```

SQLite is the source of truth during editing. Unconfirmed group-assignment drafts are stored in preferences and browser local storage. **They have not become formal labels and are not exported as formal annotations.** Saved but unreviewed `draft` labels are exported with loss mask=0.

Starting export freezes saved state. The background job produces a snapshot of that state, preventing later edits from mixing into a partially generated export. It writes to a temporary directory and publishes to the target only after validation, without overwriting an existing directory.

**Deliver training data by copying the complete export directory; also back up the work project to restore the full annotation environment.** Export attachments can be re-imported for continued annotation, but do not contain complete undo history, all UI preferences, caches, or logs.

## 3. Export Directory and Added Files

The current export adds **9 files** in `annotations/` to the standard LeRobot v2.1 structure:

```text
export_dataset/
├── meta/
│   ├── info.json
│   ├── tasks.jsonl
│   ├── episodes.jsonl
│   ├── episodes_stats.jsonl
│   └── stats.json
├── data/
│   └── chunk-000/
│       └── episode_000000.parquet
├── videos/
│   └── chunk-000/
│       ├── observation.images.cam_high/episode_000000.mp4
│       ├── observation.images.cam_left_wrist/episode_000000.mp4
│       └── observation.images.cam_right_wrist/episode_000000.mp4
└── annotations/                         # Added DSE attachments
    ├── schema.json
    ├── schema_history.json
    ├── head_spec.json
    ├── labels.parquet
    ├── samples.parquet
    ├── source_map.parquet
    ├── intervals.parquet
    ├── manifest.json
    └── validation_report.json
```

Multiple segments produce multiple episode files. Exceeding a chunk's episode capacity creates `chunk-001`, and so on. Camera names and path templates come from `meta/info.json`; the example above uses this project's sample names.

| Added file | Purpose | Plain-language explanation | Main consumers |
|---|---|---|---|
| `schema.json` | Current fields, categories, applicability, and requiredness rules | How labels are filled and what each number means | UI, rule engine, training integration |
| `schema_history.json` | Historical schemas saved in the work project | Earlier versions of the label dictionary | Tracing and re-import |
| `head_spec.json` | Current classification heads and output order | Which answer slots a model needs and their options | Model implementation and category encoding |
| `labels.parquet` | Effective labels/masks expanded per frame and field | This arm's answer at this frame and whether it can supervise training | DSE training, statistics, inspection |
| `samples.parquet` | Frame roles, center eligibility, and sampling weights | Which frames are questions and which merely supply context | Sample indexing, sampling, dataset splits |
| `source_map.parquet` | New/original indices, source identity, and media mapping | Where each cropped frame originally came from | Alignment, deduplication, leakage prevention, debugging |
| `intervals.parquet` | Range/point records, action reviews, and event snapshots | Underlying annotation intervals and review notes | Re-editing, evidence tracing, later BC filtering |
| `manifest.json` | Format, schema, sources, settings, and checksums | The delivery packing list | Integrity checks and reproducibility |
| `validation_report.json` | Center supervision, category counts, and crop summaries | How much supervision is available and what is missing | Acceptance and annotation planning |

Formats differ: JSON stores a complete object or list; JSONL stores one independent JSON object per line; Parquet is a binary table with column types and nullability. Use the UI's export inspector to inspect Parquet instead of opening binary files directly.

**The standard `data/*.parquet` files do not gain DSE category columns such as `phase` or `holding`.** Annotations are separate attachments joined to standard data by frame keys, so native LeRobot can still read the standard portion.

## 4. Training Centers and Context

### 4.1 Training Centers: When the Model Must Answer

Selecting `[150, 180)` includes frames 150–179, a total of 30 frames. This is the **training-center range**, rather than the entire retained data range.

To label a center, the model may need earlier images. To review adviser, annotators may need a short window of subsequent motion. Export therefore includes history and a review tail.

| Position | Reason for retention | Independent DSE sample in this segment? | Input to the current center's DSE? |
|---|---|---|---|
| Training center | Predict labels for this moment | Yes; usable supervision still depends on each field's mask | The current frame can enter; earlier centers can also be history for later centers |
| Continuous history before the centers | Supply past evidence | Context-only positions cannot | Past frames selected by history sampling can |
| Review tail after the last center | Let humans inspect adviser evidence | No | No |

To ask what happens at frame 150, earlier images can form the question's context. Frame 155's outcome cannot become online input. Context images are not extra questions, and future images available to reviewers are not answers the model may peek at.

### 4.2 History Span, Timestamp Count, and Retained Capacity

| Parameter | Default | Meaning |
|---|---:|---|
| `H = history_sampling.horizon_seconds` | 0.6 seconds | How far back the model requests history |
| `T = history_sampling.num_frames` | 4 | Timestamps per camera, **including current** |
| `C = context_seconds.history` | Equal to H by default | Extra continuous history actually retained; may exceed H |
| `context_seconds.review` | 0.25 seconds | Maximum review tail after the last center |

Default targets are `t−0.6, t−0.4, t−0.2, t`, with three cameras each, totaling 12 images. `H=5, T=3` gives `t−5, t−2.5, t`, totaling 9 images. `H=0, T=1` uses only the current frame.

An export may retain 5 seconds of continuous history while keeping 0.6 seconds / 4 timestamps as the default for later experiments. An older export retaining only 0.6 seconds cannot supply 5 seconds; re-export from the original data.

History sampling uses actual timestamps and the **nearest data row no later than each target**, without crossing episode boundaries. Irregular timestamps may require retaining an additional floor row before a target; `H×FPS` is not always the exact row count. At a real episode's beginning, unavailable history becomes padding plus existence masks, without duplicating current images as fake past observations.

Behavior review can show longer future references, such as 0.5, 1, 2, or 5 seconds. **This changes neither the export's fixed 0.25-second review tail nor automatic adviser candidates' maximum 0.25-second window.**

### 4.3 Discontinuous Segments and Duplicate Frames

Adjacent or overlapping center ranges are merged. Same-source ranges separated by gaps become independent episodes; removing a gap and concatenating the remainder as continuous motion is not allowed.

Independent segments may have overlapping context, saving the same source frame twice. It can be a center in A and history in B, with identical label values but different masks. Both copies must share source identity and `split_group` and must not be split between training and test sets.

## 5. Default Annotation Content

There are 8 default fields, each annotated separately for the left and right arm: 16 arm×field combinations. Fields are configurable. Training must read **the specific export's** `schema.json` and `head_spec.json`, rather than hard-coding the table below.

### 5.1 phase: Current Manipulation Phase

| Default ID | key | Meaning | Common ambiguity |
|---:|---|---|---|
| 0 | `idle` | Idle / not participating | Stillness does not imply idle; a paused transport can remain transport |
| 1 | `approach` | Approaching the target | Not yet in the local grasp-alignment region |
| 2 | `grasp_align` | Grasp alignment | Local adjustments before closing, including misalignment and not-ready cases |
| 3 | `grasp_check` | Closing and checking the grasp | Closing has started; establishing/checking holding, possibly including a small test lift |
| 4 | `transport` | Transporting | Evidence of holding; moving toward the placement target before local alignment |
| 5 | `place_align` | Placement alignment | Holding in the local placement region, adjusting position, orientation, or support |
| 6 | `withdraw` | Withdrawing after release | The object visibly remains in its designated receiving area; gripper disengages and retracts |
| 7 | `recover` | Recovery / retry after an abnormality | Current/permitted past evidence shows an abnormality and reopening, retreating, or retrying |
| 8 | `unknown` | Insufficient evidence to identify phase | A meaningful answer, not a blank |

Arms are independent; the maximum of their phase IDs is not a shared phase. Phases may move backward or recur and need not follow a complete 0→7 sequence.

Phase boundaries also depend on the task's target grasp part, receiving region, and maximum gripper opening width W. TCP is the agreed reference point of the end effector/gripper. The default research protocol describes the local grasp region as TCP-to-target-part distance no greater than W, and the local placement region as designated object-support-part-to-receiving-region distance no greater than W. W is the calibrated maximum opening scale, not this frame's opening. The current tool does not automatically measure these distances from uncalibrated RGB; insufficient boundary evidence permits unknown.

### 5.2 holding: Is the Designated Target Held?

Defaults: `0=empty, 1=holding, 2=unknown`.

This concerns visible grasping or common motion with the gripper for the **designated target**. A closed gripper does not prove that it holds an object; grasping something else does not count as holding the target. This is neither a grasp-success probability nor a measurement of hidden frictional stability.

### 5.3 close_readiness: Would Closing at the Current Pose Meet Visible Conditions?

Defaults: `0=not_ready, 1=ready, 2=unknown`. Applicable only during grasp alignment.

Assume the arm stays at its current pose and the gripper closes now. Judge whether visible evidence supports doing so: the designated part's position in the effective grasp region, opening, orientation/height, closing-path obstructions, and relative motion.

Use not_ready when conditions visibly fail, and unknown when a crucial view or evidence is unavailable. A later demonstration close or eventual grasp success does not prove that this earlier frame was ready.

### 5.4 release_readiness: Would Opening at the Current Pose Meet Gentle-Placement Conditions?

Defaults: `0=not_ready, 1=ready, 2=unknown`. Applicable during transport and placement alignment.

Assume the arm does not move and opens the gripper now. Judge whether the designated object can remain in the correct receiving region under the task's gentle-placement protocol, considering holding, receiving location, support geometry, motion, and disengagement obstructions.

Default consistency rules require `ready` to have both `phase=5 · place_align` and `holding=1`. The fact that the demonstration releases next does not justify ready. This is a task-conditioned label, not a universal physical-safety guarantee across robots, objects, and contact forces.

### 5.5 adviser: Local Adjustments Used by a Qualified Demonstration

| Field | Default categories | Meaning |
|---|---|---|
| `adviser.x` | 0 none / 1 positive / 2 negative / 3 unknown | Subsequent qualified-demonstration adjustment along the forward/back axis |
| `adviser.y` | Same as above | Adjustment along the left/right axis |
| `adviser.z` | Same as above | Adjustment along the up/down axis |
| `adviser.orientation` | 0 keep / 1 adjust / 2 unknown | Whether gripper orientation changes significantly; no precise axis, angle, or direction |

Directions use the verified canonical robot-base frame: `+X` forward, `+Y` to the robot's left, `+Z` up. Left/right refers to the robot, not the screen.

**adviser describes one short-term adjustment used by a qualified demonstration, not the uniquely correct action for the state or a precise action magnitude.** `none/keep` only means no significant motion in that component; it does not imply alignment, no need to move another axis, permission to close/release, or a requirement for the whole action chunk to remain still.

Automatic candidates require verified metric units, a rigid transform to the canonical base frame, and action-quality review. A continuous approved prefix starts at the current frame, extends at most 0.25 seconds, and stops at a phase change or event boundary. Less than 0.1 seconds produces no candidate. Significant out-and-back translation/rotation that cancels net change makes affected components unknown rather than inventing a definite direction from endpoints. The current rule version is `approved-displacement-2`.

Accepting an automatic suggestion still produces a draft. Human label review and marking an action segment approved are separate steps. Manual adviser labels do not automatically receive the same complete coordinate configuration and window provenance as generated candidates; data recipients must verify their evidence separately.

### 5.6 Default Phase-Dependent Fields

| phase | holding | close_readiness | release_readiness | Four adviser components |
|---|---|---|---|---|
| idle | Inapplicable | Inapplicable | Inapplicable | Inapplicable |
| approach | Applicable, required | Inapplicable | Inapplicable | Inapplicable |
| grasp_align | Applicable, required | Applicable, required | Inapplicable | Applicable, required |
| grasp_check | Applicable, required | Inapplicable | Inapplicable | Inapplicable |
| transport | Applicable, required | Inapplicable | Applicable, required | Inapplicable |
| place_align | Applicable, required | Inapplicable | Applicable, required | Applicable, required |
| withdraw / recover | Applicable, required | Inapplicable | Inapplicable | Inapplicable |
| unknown | Applicable, required; holding itself may be unknown | Undetermined | Undetermined | Undetermined |
| phase unfilled | Independently supervised when the review exception applies | Undetermined | Undetermined | Undetermined |

phase itself is always applicable and required. Section 8 explains unknown versus unfilled. These are defaults; the exported current schema is the machine-readable authority.

## 6. schema.json: Label Dictionary and Rules

### 6.1 Top-Level Fields

| Field | Contents | Purpose |
|---|---|---|
| `version` | Positive integer version | Published rule version, corresponding to label-review and model-head versions |
| `fields` | Active field list | Determines fields shown and exported |
| `retired_fields` | Optional `field_id → original field object` | Registers deleted fields, preserves definitions, and prevents identity reuse |
| `retired_classes` | Optional `field_id → deleted category list` | Registers deleted categories so old IDs cannot acquire new meanings |

Versions are local to a project. Two independent projects' v2 schemas need not match; compare `schema_hash` as well.

### 6.2 Properties of Each Field

| Property | Contents | Explanation |
|---|---|---|
| `field_id` | Stable identifier such as `holding` or `adviser.x` | Joins labels and model heads; not freely editable display text |
| `label` | Display name | The field's UI name |
| `label_en` | Optional English display name | Preferred in the English UI; does not change field_id |
| `description` | Semantics and annotation instructions | What question the field asks |
| `description_en` | Optional English description | Description in the English UI; the original Chinese description remains |
| `scope` | `each_arm` or `global` | Separate answers per arm or one answer per frame |
| `type` | Currently `categorical` | A finite category; category IDs are not continuous regression targets |
| `classes` | Category-object list | Permitted answers; order also affects classification-head order |
| `unknown_id` | A stable category ID for this field | The answer meaning reviewed evidence remains insufficient |
| `applicable_when` | Boolean or rule tree | When asking this question is meaningful |
| `required_when` | Boolean or rule tree | When a complete annotation requires an answer |
| `evidence` | `state` or `behavior` | State evidence permission or behavior review that allows future evidence |
| `independent_when_missing` | Optional list of dependency references | Whether independent review can make the field usable with specified dependencies missing |

Applicability and requiredness differ. An optional field can supervise training if filled, applicable, and validly reviewed. An unfilled required field affects completeness without erasing other fields' supervision.

The default use of `independent_when_missing` is holding: when phase is missing but permitted evidence independently supports the holding relationship, holding can be reviewed first. It does not make holding applicable under explicit idle or automatically fill phase.

### 6.3 Properties of Each Category

| Property | Meaning |
|---|---|
| `id` | Stable integer identity within a field; the same number can mean different things in different fields |
| `key` | Stable semantic machine name, such as `ready` or `unknown` |
| `label_zh` | Chinese display text |
| `label_en` | Optional English display text; built-in categories use UI translations when absent, while custom categories fall back to key |
| `color` | UI color, not a training feature |
| `shortcut` | UI shortcut, not a training feature |
| `deprecated` | Optional deprecation flag; blocks new selections and requires checking historical review/version status |

**Deletion and deprecation differ.** Deletion removes a category from active categories/model heads and registers its ID as retired. Deprecated categories stay in `classes` and remain listed by current `head_spec`. Build heads from the actual head_spec; filtering deprecated entries while retaining the old output order is incorrect.

Publishing a new schema sets existing labels to `needs_review`; earlier reviewed versions do not automatically become current. Even a display-description change can require checking the implementation's review impact.

Deleted fields no longer appear among current active labels or heads. Historical values of deleted categories become `value_id=null`, `missing/needs_review`, mask=0 in current per-frame results. Original values may remain in `intervals.parquet`, interpreted through historical schemas and retired registrations.

### 6.4 Reading Rule Trees

Example:

```json
{
  "field": "self.phase",
  "op": "in",
  "values": [2, 5]
}
```

This means that the condition holds when this arm's phase is grasp alignment or placement alignment.

| Rule element | Meaning |
|---|---|
| `true` / `false` | Always true / always false |
| `self.<field_id>` | This arm's field; in `self.adviser.x`, the entire field ID is `adviser.x` |
| `left.<field_id>` / `right.<field_id>` | Explicit left/right-arm reference |
| `global.<field_id>` | Global-field reference |
| `op: eq` + `value` | Equals a category ID |
| `op: ne` + `value` | Does not equal a category ID |
| `op: in` + `values` | Belongs to a set of category IDs |
| `all: [...]` | Every child condition must hold |
| `any: [...]` | At least one child condition must hold |
| `not: {...}` | Negate the child condition |

Rules have three outcomes: true, false, or unknown. Missing parents, or unknown-valued parents when the rule does not explicitly include that unknown ID in the comparison, can produce unknown.

For phase=8, checking whether phase is 2 is unknown rather than explicitly false. The default holding rule explicitly includes phase=8, so holding remains applicable. Any explicit false makes `all` false; any explicit true makes `any` true; otherwise unresolved cases remain unknown.

**Not knowing whether this is grasp alignment does not mean knowing it is not grasp alignment.** Consequently, `undetermined` cannot be converted into a negative example.

State-field rules cannot reference future behavior fields. Cyclic dependencies, invalid category references, and reuse of retired identities are rejected. The foundational phase field cannot be deleted. Categories referenced by built-in consistency checks cannot be deleted while those dependencies remain.

## 7. schema_history.json and head_spec.json

### 7.1 schema_history.json

This is a list of schema objects, each structured like `schema.json`, saved by version. The export carries historical schemas stored in the work project to interpret old interval records and retired identities and to support re-import.

It is **rule-version history**, not a complete audit log of every mouse operation, previous label value, reviewer identity, or timestamp. Current interval state is in intervals; full undo snapshots remain in the work SQLite database.

### 7.2 head_spec.json

| Field | Meaning |
|---|---|
| `schema_version` | Current schema version |
| `schema_hash` | SHA-256 of the normalized schema object, distinguishing differing content with the same version |
| `categorical_heads` | Current classification-head definitions |
| `note` | Explanation of dynamic heads and stable category IDs |

Each `categorical_heads` entry contains:

| Field | Meaning |
|---|---|
| `field_id` | Corresponding label field |
| `scope` | `each_arm` requires outputs for each arm; `global` requires a frame-level output |
| `classes` | Current category objects and display/semantic information |
| `size` | Number of classification outputs |
| `class_ids` | Stable category IDs in logit-position order |

The default file lists 8 field definitions, not 16 separate arm entries. `each_arm` tells the model implementation to produce outputs separately for both arms.

Suppose adviser.x ID=1 is deleted and ID=4 is added, giving:

```text
class_ids = [0, 2, 3, 4]
logit index     0  1  2  3
stable ID       0  2  3  4
```

Stable ID=4 maps to logit index 3, so it cannot be passed directly as target 4 to cross-entropy with four outputs. Reordering categories can also change logit order. Training/checkpoints must preserve schema/hash and class_ids; `encode_class_targets` supplies the mapping.

## 8. labels.parquet: Per-Frame, Per-Arm, Per-Field Labels

### 8.1 What One Row Represents

This is a **long table**. Each row describes one exported frame, one arm, and one field:

```text
Export EP0 / frame 18 / left / phase             → one row
Export EP0 / frame 18 / left / holding           → another row
Export EP0 / frame 18 / right / phase            → another row
```

The default schema produces 16 rows per exported frame, including centers, context, and unfilled fields. Adding an `each_arm` field adds 2 rows per frame; adding a `global` field adds 1.

The unique join key is `(episode_index, frame_index, arm, field_id)`. Do not join only by `frame_index`, since every episode has frame 0. Do not merge by source-frame keys and discard segment identity, since same-source copies may have different masks.

### 8.2 All Columns

These are logical types. Apart from columns explicitly constructed as nullable int64, actual Arrow types also depend on the batch. For example, an all-empty `evidence_mode` column may have Arrow `null` type; consumers must allow nulls.

| Column | Type / values | Meaning |
|---|---|---|
| `episode_index` | Integer | Episode number in this export |
| `frame_index` | Integer | Frame number within the exported episode, starting at 0 |
| `source_episode_uuid` | String | Stable source-episode identity inherited along the export chain |
| `source_frame_index` | Integer | Frame index in the original source episode |
| `arm` | `left` / `right` / `global` | Label scope; the default schema has no global-field rows |
| `field_id` | String | Machine identifier in the schema |
| `value_id` | Nullable int64 | Stable current category ID; `null` means no currently valid category value |
| `applicability` | `applicable` / `not_applicable` / `undetermined` | Explicitly applicable, explicitly inapplicable, or unresolved under current rules |
| `annotation_status` | `labeled` / `missing` | Whether a current category value exists; not a review status |
| `review_status` | `draft` / `reviewed` / `needs_review` | Draft, reviewed, or requiring another review |
| `evidence_mode` | `state` / `behavior` / null | Evidence mode at save/review time; unsaved fields may be null |
| `label_source` | `human` / `rule_suggestion` / `test_fixture` / null | Human, rule candidate, explicit synthetic test, etc.; not a review decision |
| `schema_version` | Nullable int64 | Schema version when the label was last saved/reviewed, not unconditionally the current version |
| `revision` | Nullable int64 | Work-project revision for tracing; not a frame, timestamp, or globally unique identifier |
| `future_seen` | Boolean or null | Whether behavior viewing had been registered for this source episode when saving/reviewing; limitations below |
| `effective_loss_mask` | Integer 0 / 1 | Whether this field contributes classification supervision in this segment |
| `provenance` | JSON string | Evidence configuration, candidate source, review scope, and other information requiring JSON parsing |

Current exported per-frame columns **do not include** `required`, `reason`, or `retired_value_id`. Those are auxiliary outputs of rule resolution/the UI. Recompute them from schemas and raw records if needed; do not assume corresponding on-disk columns. Trace deleted categories' original values through intervals and historical schemas.

### 8.3 Cases That Are Easy to Confuse

| Case | Example | `value_id` | Meaning | Can mask be 1 when reviewed and selected as a center? |
|---|---|---|---|---|
| Explicit ordinary category | close=not_ready | 0 | Evidence shows conditions are not met | Yes |
| Explicit unknown category | close=unknown | Default 2 | Permitted evidence was examined but remains insufficient | Yes, if applicable |
| missing | close unfilled | null | No current answer | No |
| not_applicable | close during transport | Null or a retained old value | This phase should not be asked the question | No |
| undetermined | Missing/unknown phase leaves close applicability unresolved | Null or a retained old value | Whether the question applies is itself unknown | No |

`unknown` is a genuine category only when **the field is applicable**. If an unknown parent phase makes a child undetermined, storing unknown in the child does not make it supervised.

`null` is not ID=0: phase=0 is idle, close=0 is not_ready, and adviser.x=0 is none. Filling null with 0 fabricates many wrong labels. The adapter converts null to training ignore target `-100` with mask=0.

### 8.4 Loss-Mask Conditions

The rule engine computes each field separately:

```text
effective_loss_mask = 1 if and only if:
    the frame is explicitly selected as a training center in this segment
AND applicability == applicable
AND annotation_status == labeled
AND review_status == reviewed
AND label schema_version == current schema.version
```

All five conditions are needed: the question is selected, meaningful, answered, reviewed, and reviewed under the current rules.

There is no condition requiring every field in the frame to be complete. A reviewed phase can supervise training while missing adviser only disables adviser supervision. History/review-tail copies always have mask=0, even if they carry reviewed labels.

Review blocks or skips built-in semantic conflicts, such as release ready without place_align/holding. Parent-gate value changes require dependent labels to be reviewed again. However, the mask is not automatic proof of visual ground truth, calibration quality, or global semantic correctness.

In particular, the mask formula **does not additionally require** an approved action range, `future_seen=false`, `label_source=human`, verified units/coordinates, or complete annotator identity. Training/data-audit workflows must separately check such requirements for their respective purposes.

### 8.5 Reading provenance and future_seen

`provenance` is extensible; its keys vary by source and are not present uniformly.

| Common key | When present | Meaning |
|---|---|---|
| `history_config` | State-mode saving/review | Permitted H/T, sampling rule, offsets, and inclusion of the current frame |
| `review_scope` | Range label review | Confirmed source-frame `start/end` and `mode` |
| `status`, `values` | Accepted automatic adviser candidate | Candidate status/component values, not a substitute for outer review_status |
| `start`, `end`, `duration` | Automatic adviser candidate | Window coordinates in the input dataset used when generating it, exclusive end, and actual duration in seconds |
| `needs_review` | Automatic adviser candidate | Whether reversal ambiguity or another issue was detected; false still means only a candidate |
| `rule_version` | Rule candidate | Version of the generating rule |
| `coordinate_config` | Automatic adviser candidate | Coordinate, unit, transform, noise, and measured end-effector feature configuration at generation |
| `test_fixture`, `description`, etc. | Some test/custom records | Additional caller/test notes, not evidence of real human annotation |

Common `coordinate_config` keys:

| Key | Meaning |
|---|---|
| `verified` | User confirmation of original units/transform, not a measurement made by the tool |
| `unit` | Automatic direction candidates currently require original position units `m` |
| `frame` | Required canonical target frame: `robot_base` |
| `transform` | Source-pose-to-base 4×4 rigid transform; top-left 3×3 is rotation and the first three entries of the last column are translation |
| `position_noise` | User-supplied stationary position-noise scale in meters; significance threshold `max(0.005m, 3×noise)` |
| `angle_noise` | User-supplied stationary angular-noise scale in radians; threshold `max(5°, 3×noise)`, computed internally in radians |
| `feature` | Measured end-effector field, such as `observation.ee_pose_left` |

The rotational part changes displacement directions; translation cancels when endpoint positions are subtracted. Noise values are user supplied; the tool does not automatically estimate a trusted stationary-noise calibration.

Provenance windows are not automatically rewritten into the current export's coordinates. After another crop, their frame numbers cannot directly index the new video. Outer source UUID/frame keys and `source_map` provide stable tracing; the original work project may also be needed.

`future_seen` is registered at source-episode level when entering behavior mode, then copied into later saved/reviewed records. It is neither a full per-field/per-exposure viewing log nor a precise record of future-window length.

Thus, `true` does not prove a state label leaked future information: permitted history may independently support it. `false` or null does not prove the annotator has never seen the future elsewhere. Evidence isolation helps structure annotation but cannot erase human memory.

## 9. samples.parquet: Eligibility, Roles, and Sampling

### 9.1 Rows and Columns

Each exported data frame has one row, including centers, history, and tails. The primary key is `(episode_index, frame_index)`.

| Column | Type / values | Meaning |
|---|---|---|
| `episode_index` | Integer | Current exported episode |
| `frame_index` | Integer | Current exported frame |
| `center_is_selected` | Boolean | Explicitly selected decision center; only true rows enter DSEDataset's sample list |
| `is_history_context` | Boolean | Falls in retained history available to some later center in the segment, not necessarily selected by sparse sampling |
| `is_review_context` | Boolean | After the last center, retained only for future review |
| `source_episode_uuid` | String | Stable source-episode identity |
| `source_frame_index` | Integer | Original source-frame index |
| `split_group` | String | Same-source grouping identity for preventing cross-split leakage, not an assigned split name |
| `event_group` | String | Event/phase grouping for sampling balance, not an event-prediction target |
| `pool` | `key` / `background` | Key or background sampling pool |
| `valid_fields` | Integer | Sum of `effective_loss_mask` across arms/fields; number of supervised fields, not categories |
| `sampling_weight` | Float | Relative sampling weight, not a field loss mask or action-quality score |
| `boundary` | Boolean | Within approximately one frame of the first/last center, including adjacent context; not an extra label |
| `key_zone_left` | 0 / 1 / null | Whether left phase implies a fine-alignment zone |
| `key_zone_right` | 0 / 1 / null | Same for the right arm |

Role flags **are not mutually exclusive**. A center can also supply history to later centers, so `center_is_selected=true` and `is_history_context=true` may coexist. `center_is_selected` determines independent sample eligibility.

### 9.2 key_zone, pool, and event_group

Under current default semantics:

- `key_zone_*`: 1 for phase 2 or 5, 0 for other known phases, null for missing phase or phase 8. Derived gating information, not extra human ground truth or an independent head.
- `pool=key`: either arm's phase is in `[2,3,5,7]`, or a confirmed event is nearby. This is broader than key_zone: grasp checking and recovery also enter the key pool.
- `event_group`: uses the nearest confirmed event's source frame when an event is nearby; otherwise uses both current phase values. Nearby means approximately ±0.25 seconds converted to frame count.

These are concrete exporter policies. Phase sets and key_zone's default unknown ID use built-in conventions. Adding a phase in the editor does not automatically extend sampling policies or built-in consistency checks. Changes to the core phase protocol must be checked against code and experiment settings.

Event-group strings are internal statistics/sampling identifiers rather than stable semantic categories. Derived key_zone/pool values do not imply human review.

### 9.3 How sampling_weight Is Computed and Used

Only **centers with valid_fields>0** are eligible. Context-only records and centers with no valid supervision have weight 0.

By default, probability mass is split 70% key / 30% background, then divided across source groups, event/phase groups, unique source frames within each group, and copies. This prevents long trajectories or duplicates from automatically dominating. If a pool has no eligible records, a warning is reported and the other pool receives the available mass.

The 70%/30% ratio is a **sampling target**. It neither deletes data nor guarantees exactly that ratio in each finite batch. Ordinary random DataLoader iteration does not automatically honor these weights. `DSEDataset.sample_indices` samples with replacement using the weights and raises if all weights are zero.

Sampling weights do not change field validity, replace loss masks, or necessarily need multiplying into the loss again. After splitting data, recheck pool ratios and group balance within the training subset. Weights computed over the entire export do not guarantee the same ratio after filtering sources.

## 10. source_map.parquet: Source and Media Mapping

### 10.1 Current, Parent, and Source Coordinates

If original A is cropped to B, then B to C:

```text
A: original source      →      B: first export      →      C: current export
source_*                      parent_*                    current episode/frame/index
```

Current coordinates locate a frame in C. Parent coordinates locate its direct input in B. Source coordinates locate its origin in A. Parent/source frame indices commonly match on first export and differ after further cropping.

### 10.2 All Columns

There is one row per current exported frame, keyed by `(episode_index, frame_index)`.

| Column | Meaning |
|---|---|
| `episode_index` | Current exported episode |
| `frame_index` | Frame within the current exported episode |
| `index` | Global row index across current exported episodes |
| `timestamp` | Current episode time, reset to 0 at the first retained frame, in seconds |
| `source_episode_uuid` | Stable original-source identity inherited along this tool's export chain |
| `source_frame_index` | Frame index within the original source episode |
| `source_episode_index` | Integer episode index in the original source dataset |
| `source_dataset_fingerprint` | Content fingerprint of the original source dataset |
| `source_timestamp` | Original source-frame time in seconds |
| `source_index` | Global row index in the original source dataset |
| `parent_episode_index` | Episode in the direct input dataset |
| `parent_frame_index` | Frame within the direct input episode |
| `parent_timestamp` | Direct input-frame time in seconds |
| `split_group` | Inherited same-source split group |
| `media_mapping` | JSON string mapping each camera to original video frames |

Parsed `media_mapping` looks like:

```json
{
  "observation.images.cam_high": {
    "source_ordinal": 40,
    "source_pts": 1.333333
  }
}
```

The actual sample includes all three cameras.

| Media key | Meaning |
|---|---|
| `source_ordinal` | Source-video frame ordinal in presentation order, starting at 0; not necessarily identical to the data-row frame index |
| `source_pts` | Source-video frame presentation time, stored in **seconds**, not raw integer codec ticks |

Re-export preferentially preserves ancestral media mapping. Do not use source ordinal directly as a frame index into the current MP4.

### 10.3 Limits of Stable Identity

On initial import, a SHA-256 fingerprint is computed from dataset-relative paths and file contents. Fingerprint plus episode index determines the UUID, and default split_group is that source-episode UUID. Moving a complete directory without changing contents preserves identity; export chains inherit source keys.

This is not automatic recognition of physical collection identity. Saving the same recording in another format, changing file contents, or re-encoding with another tool does not guarantee same-source recognition. Different episodes from one collection session are not automatically combined into a larger session group. Formal splits still need collection-level provenance management.

## 11. intervals.parquet: Ranges, Point Overrides, Action Reviews, and Events

### 11.1 Two Outer Columns

| Column | Meaning |
|---|---|
| `table` | Record type: `annotations` / `selections` / `reviews` / `events` |
| `payload` | JSON string whose internal fields depend on table |

Read table first, then `json.loads(payload)`. Some nested fields, including `provenance` and `details`, are themselves JSON strings requiring another parse.

This file is a **snapshot of current records** in the relevant work tables, not a complete edit log. For source UUIDs included in an export, the implementation carries their ranges/reviews/events and **does not guarantee clipping these records to the exported crop**. Attachments may describe source frames whose video is absent from this export. Preserving those annotation/review records does not also retain their media.

### 11.2 table=annotations Payload

| Key | Meaning |
|---|---|
| `id` | SQLite record number before export, not a cross-project stable key |
| `source` | Source-episode UUID, matching other tables' source_episode_uuid |
| `start`, `end` | Original-source half-open frame range `[start,end)` |
| `arm` | left / right / global |
| `field` | Corresponds to labels.field_id |
| `value` | Originally saved stable category ID or null; deleted IDs can still be traced here |
| `kind` | `range` assignment or `point` single-frame exception; point length is 1 |
| `status` | Label draft / reviewed / needs_review |
| `mode` | state / behavior |
| `label_source` | human / rule_suggestion / test_fixture, etc. |
| `future_seen` | Registered value at save time, generally SQLite integer 0/1 in this raw table |
| `version` | Corresponds to labels.schema_version |
| `revision` | Record revision |
| `provenance` | JSON string with evidence/candidate information at save time |

Single-frame exceptions take priority over ranges by default; **explicit single-frame missing also overrides a range value**. The UI's uniform overwrite option removes point exceptions for specified fields and eligible frames. Export stores the final record state without an additional permanent `point_policy` column.

Per-frame labels are computed from these records and the current schema. Classification training normally uses labels directly; old interval categories must not bypass current rules as valid training answers.

### 11.3 table=selections Payload

| Key | Meaning |
|---|---|
| `id` | Internal table record number |
| `dataset` | Fingerprint of **this export's input dataset** |
| `episode` | Episode in this export's input dataset |
| `start`, `end` | Center range in input-episode coordinates |

These start/end coordinates refer to the direct input and can differ from annotations/reviews' original-source coordinates. Re-import restores selections from the current exported center flags in samples; do not apply old selections' numbers directly to new episodes.

### 11.4 table=reviews Payload: Action Quality

| Key | Meaning |
|---|---|
| `id` | Internal record number |
| `source` | Source-episode UUID |
| `start`, `end` | Original-source half-open frame range |
| `status` | approved / rejected / unreviewed |
| `reason` | Human reason, such as an incorrect close or recovery demonstration; currently free text |

**This review asks whether demonstration actions are suitable imitation targets; labels.review_status asks whether a semantic answer was reviewed.** Neither substitutes for the other.

An absent reviews record does not mean approved. Rejected status does not delete action values, rewrite videos, or automatically invalidate DSE state labels: failure footage can still teach not_ready or recover. The current adapter does not generate BC action-chunk eligibility masks. VLA training must consume these reviews separately.

### 11.5 table=events Payload

| Key | Meaning |
|---|---|
| `id` | Internal record number |
| `source` | Source-episode UUID |
| `frame` | Original source-frame index |
| `arm` | Arm associated with the event |
| `type` | Event type; rule candidates commonly use `open` / `closed`; inspect records for manual types |
| `status` | Commonly suggestion / confirmed; unconfirmed candidates are not verified events |
| `details` | JSON string containing source and candidate-generation settings |

Manual event details commonly contain `{"source":"human"}`. Rule event details may contain:

| Key | Meaning |
|---|---|
| `feature` | Measured `observation.state` or commanded `action` channel |
| `dimension` | Channel index in the vector, interpreted through info.features names |
| `rule_version` | Current hysteresis rule: `hysteresis-1` |
| `calibration.verified` | User confirmation of units, direction, and threshold settings |
| `calibration.unit` | Channel units |
| `calibration.closed_threshold` / `open_threshold` | Separate closing/opening thresholds |
| `calibration.closed_below` | Whether smaller values mean closed |
| `calibration.min_duration` | Minimum duration in seconds for the new state, reducing noise-induced transitions |

Hysteresis uses different opening/closing thresholds to avoid repeated transitions around a single threshold. A command-channel closed event cannot automatically be interpreted as measured closure, holding, or ready.

Confirmed events drive the key sampling pool and event grouping. Automatic adviser currently reads saved source events for window boundaries, so unconfirmed candidates may also truncate its window. Review/clean up candidates; do not assume every use filters to confirmed only.

## 12. manifest.json: Export Delivery Manifest

### 12.1 All Top-Level Keys

| Key | Meaning and use |
|---|---|
| `tool_version` | Declared tool version, currently 0.1.0; also check code revision and manifest content rather than inferring every feature from this value |
| `format` | Current format: `LeRobot-v2.1+DSE-1` |
| `schema_version` | Current active rule version |
| `schema_hash` | SHA-256 of normalized current schema content |
| `source_fingerprint` | Fingerprint of the **direct input dataset**; on re-export this may identify a previous export |
| `source_episode_uuids` | Source-episode UUIDs of frames actually included |
| `source_dataset_fingerprints` | Original-source fingerprints inherited in source mapping; may differ from the direct-input fingerprint |
| `context_seconds` | Continuous-history capacity and review-tail setting |
| `history_sampling` | Default model-history sampling configuration |
| `selections` | Training-center ranges in input-dataset coordinates, including id/dataset/episode/start/end |
| `sampling` | Sampling-pool ratio and group-balancing policy |
| `encoding` | Video re-encoding parameters |
| `checksums` | Map of relative file paths to SHA-256 |
| `source_roots_required` | false means the original directory path is unnecessary for reading; it does not recover absent history or verify calibration |

### 12.2 Nested Configuration

| Path | Meaning |
|---|---|
| `context_seconds.history` | Continuous-history capacity C in seconds; not a guarantee of that much actual history for every center |
| `context_seconds.review` | Review-tail setting, currently 0.25 seconds; trajectory endings may provide less |
| `history_sampling.horizon_seconds` | Default model-history span H |
| `history_sampling.num_frames` | Timestamps per camera T, including current |
| `history_sampling.offsets_seconds` | Positive backward offsets from oldest to current, e.g. `[0.6,0.4,0.2,0]` |
| `history_sampling.sampling` | Currently `at_or_before` |
| `history_sampling.includes_current` | Currently true |
| `sampling.key_ratio` | Target probability mass for the key pool, such as 0.7 |
| `sampling.group_first` | true declares group-balanced export weights; not an automatic trainer-grouping switch |
| `encoding.codec` | Currently libx264, producing H.264 |
| `encoding.crf` | Currently 18, a quality setting rather than losslessness |
| `encoding.preset` | Currently fast, controlling the speed/compression tradeoff |
| `encoding.pix_fmt` | Currently yuv420p |

Manifest history offsets are positive backward distances. The adapter's `history_target_offsets` are negative relative times. Their signs differ while describing the same targets.

### 12.3 What Checksums Establish

Export records SHA-256 for generated standard data, videos, and other attachments. **The manifest does not include itself in checksums**, avoiding self-reference. schema_hash hashes normalized schema-object content; the checksum of schema.json hashes its file bytes. They need not match.

Re-import and the export inspector verify manifest files. `DSEDataset` currently checks only annotations-scope checksums by default, without hashing every large video during initialization. For complete media integrity checking, call `verify_manifest(root)` or open the export inspector.

Checksums detect missing/changed files. They do not prove human labels correct or provide signed identity authentication. Do not hand-edit published Parquet/JSON and treat it as the same snapshot. Edit through the work project and export to a new directory.

## 13. validation_report.json: Reading the Export Report

| Key | Actual computation |
|---|---|
| `center_count` | Selected center records, counted by current exported copy |
| `context_count` | Noncenter records; generally center_count + context_count == total_frames |
| `complete` | Centers with at least one valid field and no explicitly required field with mask=0 |
| `partial` | Centers with at least one valid field and at least one such required-field gap |
| `no_supervision` | Centers with mask=0 for every field |
| `missing_fields` | Field×frame count of explicitly required center fields with mask=0, not just value_id=null |
| `counts` | Center category counts per arm/field, with keys such as `left.phase:8` or `left.phase:missing` |
| `warnings` | Warnings such as an unavailable sampling pool |
| `segments` | Input episode, center range, retained range, and new episode for each segment |
| `elapsed_seconds` | Elapsed time when writing the report; final media validation follows, so not a strict substitute for full job runtime |

Keys within each segments entry:

| Key | Meaning |
|---|---|
| `source_episode` | Episode index in the direct input dataset; on re-export, not necessarily the original-source episode |
| `center` | `[start,end)` center range in input coordinates |
| `retained` | Actual retained `[low,high)` range in input coordinates, including context |
| `new_episode` | Current exported episode |

Four interpretation rules:

1. `complete` means complete under these counting conditions, not 100% semantic accuracy, answers for every head, all dependencies resolved, or approved action quality. For phase=unknown, some child requiredness is unknown and does not count as an explicitly required gap.
2. `missing_fields` may include filled but unreviewed or old-version labels, not just blank entries.
3. `counts` includes saved/resolved center categories without filtering to mask=1 or deduplicating by group. Training class weights need separate counting over valid supervision and deduplicated training sources.
4. The report does not claim a comprehensive human-semantic/BC-quality audit. Successful export does not guarantee trainable labels; unsupervised exports can still validate cropping and media.

## 14. Changes to Standard LeRobot Files

Alongside attachments, the tool generates a new standard portion rather than copying the original directory unchanged.

### 14.1 Data Files: data/chunk-*/episode_*.parquet

Within retained ranges, original observation/action values and physical storage precision are preserved. Episode/frame/global indices, task_index, and time are reorganized for the new dataset. Common sample columns:

| Column | Meaning | Export/training behavior |
|---|---|---|
| `observation.state` | 18D measured state, including both arms, grippers, and head joints | Values retained; read channel meanings from feature names rather than assuming another robot's 16D layout |
| `action` | 18D demonstrated action/command vector | Values retained; rejected status/readiness does not correct them; excluded from DSE inputs |
| `observation.ee_pose_left/right` | Measured 7D end-effector pose per arm | Sample layout x/y/z/qx/qy/qz/qw; verify frame and units |
| `observation.ee_pose_cmd_left/right` | Commanded 7D end-effector pose per arm | Command differs from measurement; excluded from DSE inputs |
| `frame_index` | Frame within the new episode | Starts at 0 |
| `timestamp` | New episode time in seconds | Original time minus the first retained row's time, not the first center's time |
| `episode_index` | New episode index | Contiguous from 0 |
| `index` | New global row index | Contiguous across episodes |
| `task_index` | Entry in tasks.jsonl | May be remapped; original indices cannot directly look up the new task table |

The local original sample declared some fields float32 in info while Parquet stored double/float64. Export preserves actual types and updates feature declarations rather than reducing precision or normalizing values.

Video pixels remain in MP4, not image arrays added to data rows. Actual features and Parquet schemas determine available fields.

### 14.2 Video Files: videos/chunk-*/*/episode_*.mp4

The tool decodes source video, selects frames by exact mapping, and re-encodes H.264/yuv420p at the source resolution and nominal FPS with new video time. The sample's 640×480 export video does not become 256×256 just because the training reader uses that size.

H.264/CRF18 is lossy: it preserves frame correspondence and scene content, not identical AV1 bytes or RGB pixels. Separately encoded copies of one source frame can differ. Use source keys rather than pixel identity to establish common origin.

Causal data-row sampling does not establish physical camera-exposure synchronization. The media layer matches the nearest video PTS with approximately half-frame tolerance. Actual collection clocks, exposure/telemetry synchronization, and stale inputs still require independent quality checks.

### 14.3 meta/info.json

| Key | Meaning and export handling |
|---|---|
| `codebase_version` | Remains v2.1; v3 and unknown versions are rejected |
| `robot_type` | Robot description inherited from the source |
| `fps` | Nominal frame rate |
| `features` | Modality/column dtype, shape, names, plus video resolution/codec/pixel-format info |
| `total_episodes` | New segment count, not the number of complete source demonstrations |
| `total_frames` | All retained rows, including history, review tails, and copies; not the DSE sample count |
| `total_tasks` | Exported task-table entries |
| `total_chunks` | Chunk count under chunks_size |
| `total_videos` | New episodes × camera count |
| `chunks_size` | Episode capacity per chunk, inherited from the source |
| `data_path` | Data-file path template |
| `video_path` | Video-file path template |
| `splits` | `{"train":"0:N"}`, where N is new episode count; standard directory loading only, **not an automatic research train/validation/test split** |

Feature `names` describes vector dimensions. Camera `shape` gives height/width/channels, and `info.video.codec`, `video.pix_fmt`, `video.fps`, etc. describe the exported media. Shape metadata does not substitute for physical units/calibration.

For the project's sample, internal fields of `features[feature_name]` mean:

| Path | Meaning |
|---|---|
| `dtype` | Numeric physical type, or `video` for external video |
| `shape` | Per-frame vector/image shape; scalar metadata commonly uses `[1]` |
| `names` | Vector dimension or image-axis names; scalars may use null |
| `info.video.height` / `info.video.width` | Height/width in pixels |
| `info.video.codec` | Actual output codec, currently h264 |
| `info.video.pix_fmt` | Currently yuv420p, not the model tensor's RGB channel ordering |
| `info.video.is_depth_map` | Whether it is depth data; false for the current RGB sample |
| `info.video.fps` | Nominal video frame rate |
| `info.video.channels` | Channel count, 3 in the current sample |

### 14.4 tasks.jsonl and episodes.jsonl

| File | Keys per row | Meaning |
|---|---|---|
| `tasks.jsonl` | `task_index`, `task` | Task ID and text, e.g. `Put the banana on the plate.` |
| `episodes.jsonl` | `episode_index`, `length`, `tasks` | New episode ID, retained frame count, and task-text list |

Original task text is preserved. **Target grasp part, receiving region, W, and calibration notes entered under 任务与几何参考 (Task and geometry reference) stay in work-project preferences. Export does not automatically put them in tasks.jsonl or create a complete task-protocol file.** Supply a separately versioned task/calibration protocol with training data; the directory does not contain every verbal agreement.

Automatic event details and accepted adviser-candidate provenance can retain their respective configurations. This does not mean every manual label automatically carries a complete task protocol.

### 14.5 episodes_stats.jsonl and stats.json

`episodes_stats.jsonl` records `episode_index` and per-segment `stats`. `stats.json` aggregates the entire export. Common statistics:

| Key | Meaning |
|---|---|
| `min` / `max` | Minimum/maximum per dimension |
| `mean` | Mean per dimension |
| `std` | Population standard deviation, NumPy default ddof=0 |
| `count` | `[N]` contributing frames/rows; video values use channel pixels while count still records frames |

Numeric-vector statistics are per dimension. Video min/max/mean/std are per RGB channel, commonly `[3,1,1]`, over pixel values in `[0,1]`, computed from the **actual published re-encoded video**.

Statistics cover all retained rows/copies without filtering to centers, valid supervision, or eventual train/validation/test splits. They describe the dataset and support standard-format compatibility; **they are not automatically strict training-only normalization statistics**. Category IDs and masks must not be mean/std normalized.

## 15. What Downstream DSE Loading Actually Returns

### 15.1 Native LeRobot and the DSE Adapter

Native LeRobot reads standard `meta/data/videos`. It does not automatically join attachments, apply DSE loss masks, create dynamic heads, or filter rejected actions. The project verified independent v2.1 reading with pinned official `lerobot==0.3.3`; this does not make arbitrary latest trainers support the attachments.

`dse_tool.adapter.DSEDataset` joins observations and attachments:

```python
from dse_tool.adapter import DSEDataset, encode_class_targets

reader = DSEDataset(
    "/path/to/export_dataset",
    cache="/path/to/separate-work/train-cache",  # Keep caches outside the dataset
)
item = reader[0]
batch = reader.batch([0, 1])
encoded = encode_class_targets(batch["labels"], reader.schema)
```

This example requires at least two centers, but not nonzero supervision at both; training still uses masks. Initialization verifies annotation checksums and shared-rule consistency of applicability/masks. Attachment tables currently load into memory; this does not establish large-scale loading performance.

### 15.2 Batch Fields and Shapes

Let B be batch size, T history timestamp count, and S image size, default 256. The adapter requires 3 cameras, sorted by the high/left/right naming convention. Verify other camera layouts explicitly; it does not automatically understand every robot.

| Key | Default batch shape / type | Meaning |
|---|---|---|
| `images` | `[B,T,3,3,S,S]`, float32 | Batch, timestamp, camera, RGB channel, height, width; only resize/divide by 255, range `[0,1]` |
| `image_valid_mask` | `[B,T,3]`, bool | Whether the timestamp/camera has actual input |
| `state` | `[B,T,18]`, currently float64 | Measured state from the same history rows, without model standardization |
| `state_valid_mask` | `[B,T]`, bool | Whether a historical row exists |
| `ee_pose_feedback` | `[B,T,2,7]`, float64 | Left/right measured end-effector x/y/z/qx/qy/qz/qw |
| `ee_pose_valid_mask` | `[B,T,2]`, bool | Whether the arm/time has a feedback field; not calibration/noise-quality certification |
| `history_frame_indices` | `[B,T]`, int64 | Actual row indices in the current exported episode; missing -1 |
| `history_target_offsets` | `[B,T]`, seconds | Target relative times, e.g. `[-0.6,-0.4,-0.2,0]` |
| `history_actual_offsets` | `[B,T]`, seconds | Actual row-relative times, possibly earlier than targets; missing NaN |
| `history_config` | Configuration dictionary | Reader-wide H/T and sampling conventions |
| `task` | List of B strings | Original task text, without automatically appended UI geometry notes |
| `labels` | `arm → field_id → [B]`, integers | Current-center stable category IDs, with null converted to -100 |
| `loss_masks` | Same nested `[B]` integer arrays | Effective per-field supervision at the current center |
| `source_keys` | List of B dictionaries | Source UUID/frame, split_group, and current schema version for grouping/tracing, not network inputs |
| `coordinates_verified` | false | The reader does not certify all raw feedback as canonical verified coordinates |

These are **loading-time batch fields**, not additional exported files named `images.npy`, a 36D Parquet table, or files matching every returned key.

The adapter excludes action, end-effector commands, and future images. Labels concern only the current decision frame; historical timestamps do not each become extra targets for that label.

### 15.3 Three Different Masks and Reviews

| Object | Question | Who applies it |
|---|---|---|
| Input existence masks | Does this timestamp/view/feedback really exist? | Model encoding, attention, and pooling |
| Label loss masks | May this answer count toward training now? | DSE loss function |
| Action-quality review / chunk eligibility | Can these actions serve as imitation targets? | Later VLA/BC sample construction and training |

Returning a mask does not apply it. Merely zeroing absent inputs can still let a network interpret them as real state. Merely saving `effective_loss_mask` does not prevent ordinary cross-entropy from scoring invalid answers.

Input masks currently mainly indicate row/field existence. Decoding/mapping failures generally raise errors rather than producing a low-quality mask. Quaternions, units, and clock quality need independent checks.

### 15.4 The 36D Proprioception Interface

`proprioception36` combines each timestamp's 18D state and both measured end-effector poses:

```text
18D state + left(position3D + rotation6D) + right(position3D + rotation6D) = 36D
```

Rotation6D takes the first two columns of the canonical rotation matrix, not six extra joints. Configuration must explicitly provide `verified=true`, `unit="m"`, `frame="robot_base"`, and a verified 4×4 rigid transform. The function does not discover original units or convert them automatically.

Filter to valid state/end-effector timestamps before conversion; padding's all-zero quaternions cannot be passed as real measurements. The function rejects invalid quaternions. Conversion and standardization are training-side operations; the tool has not rewritten original 18D data as 36D.

## 16. Explicit Responsibilities of Training Code

### 16.1 Split by Source Before Assigning Train, Validation, and Test

At minimum, split by `split_group` so all centers, context copies, and re-exported segments from a source episode remain together. Formal collections should also consider higher-level groups such as sessions, scenes, and objects.

Do not randomly split new episodes, or shuffle neighboring center frames into allegedly independent test sets. Overlapping footage/actions from one recording may otherwise appear in both training and test, inflating metrics. The all-train entry in `meta/info.json` describes standard exported layout, not a research split.

`reader.sample_indices` sees all centers by default and **has no split-filter parameter**. If one export contains future train and test data, construct a training subset before sampling. Minimal example:

```python
import random

train_groups = {"<split_group assigned to training>"}
train_indices = [
    i for i, row in enumerate(reader.centers)
    if row["split_group"] in train_groups and row["sampling_weight"] > 0
]
if not train_indices:
    raise ValueError("No eligible supervised centers in the training sources")
indices = random.Random(0).choices(
    train_indices,
    weights=[reader.centers[i]["sampling_weight"] for i in train_indices],
    k=16,
)
batch = reader.batch(indices)
```

This shows source-isolated relative-weight sampling only. For a strict 70/30 ratio and source-group balance within the training subset, recompute its distribution. Validation/test generally use explicit traversal/reporting rules rather than replacement-based training sampling to estimate overall metrics.

### 16.2 Category IDs, Missing Values, and Masked Loss

Use `encode_class_targets` to map stable IDs to logit indices, then compute cross-entropy only where `loss_masks==1`. IDs are not regression targets. Do not merge unknown into a negative class or index class weights with -100 before multiplying by zero.

With PyTorch installed, a minimal single-head loss example is:

```python
import torch
import torch.nn.functional as F

def masked_head_loss(logits, encoded_targets, loss_mask):
    # Stable IDs have already been mapped to contiguous indices for this head.
    targets = torch.as_tensor(encoded_targets, device=logits.device, dtype=torch.long)
    valid = torch.as_tensor(loss_mask, device=logits.device).bool() & (targets != -100)
    if not valid.any():
        return logits.sum() * 0.0  # Keep the graph; do not average an empty set.
    return F.cross_entropy(logits[valid], targets[valid])
```

This is a training-side illustration, not a complete loss/network already included in the tool. Normalize each head by its own valid count. A head without supervision returns 0 while others can train. Loss applicability comes from **ground truth/schema**, not the model's predicted phase, which must not disable child heads it gets wrong.

### 16.3 Image Processing, Floating-Point Types, and Normalization

The reader only converts images to RGB, resizes, and scales to `[0,1]`. It does not apply the target visual checkpoint's full processor normalization. Training must use consistent model-required processing, avoiding division by 255 twice or different training/deployment pipelines.

State/end-effector outputs are float64 and generally need explicit conversion to the model's floating type without converting category IDs or integer indices. Read names for the 18D layout rather than hard-coding sample gripper positions across robots.

`training_statistics(reader, train_groups)` computes only **18D state at center frames** from the supplied training sources, deduplicated by source UUID+frame. It returns mean/std/count/split_groups:

```python
from dse_tool.adapter import training_statistics
stats = training_statistics(reader, train_groups)
```

It does not filter centers by label masks, cover the full historical input distribution or 36D features, create an image processor, or standardize a batch. Explicitly implement and record any other statistics convention. Training code handles zero-variance protection, derived end-effector statistics, modality selection, and application of normalization.

### 16.4 Review Evidence After Changing History or Modalities

A reader may override manifest H/T within retained capacity. Each reader/export has a uniform default H/T; per-sample random H/T in one batch and variable-length collate are not supported.

Replacing 0.6-second history with only the current frame, or removing measured proprioception and using images alone, may make holding/recover labels unobservable. **The current reader does not automatically clear reviewed status/masks or re-review labels after these changes.** Check provenance, review under the new evidence configuration, and create a separate experimental label version when necessary.

Missing-history actual offsets are NaN. Zero them using existence masks before time encoding rather than feeding NaN to the network. Model sequence lengths/masks must use T rather than a fixed four timestamps. Multiple targets may map to one real data row; represent actual time rather than pretending they are different exposures.

### 16.5 Keep State Targets and Future-Derived adviser Out of Online Inputs

phase/holding/readiness describe states supported by current and permitted past evidence; adviser supervision may be made using future evidence. **Using future evidence to create a target does not permit future evidence as prediction input.**

DSE forward inputs must exclude action, ee_pose_cmd, future images/poses, human phase labels, event_group, split_group, and review decisions. Those can create targets, splits, and audits but must not become online shortcuts.

Future-derived adviser ground truth also cannot be presented in formal tests as a model-predicted prompt. Later VLA conditioning should use the established grouped/out-of-fold prediction scheme and distinguish predicted prompts from ground-truth oracles. The tool does not automatically perform those training/evaluation workflows.

### 16.6 Usable DSE Labels Do Not Make the Original Actions Suitable for BC

For example, a visibly misaligned frame with close=not_ready may be followed by an incorrect demonstration close. The frame can teach DSE to recognize not_ready without making the original wrong action a valid imitation target.

BC sample construction must also consume action reviews. For a chunk covering rejected or protocol-unapproved actions, a conservative first approach excludes the whole chunk from positive BC. Removing only the erroneous action's starting frame is insufficient: a chunk spanning frames 80–129 also covers an incorrect close at frame 100, so its sample starting at 80 needs handling.

The current export retains finite history and a 0.25-second review tail for DSE, without guaranteeing long VLA action chunks. Short crops are not automatic replacements for full action-training data. If needed, use source mapping to read separately retained complete originals and verify eligibility of the entire target chunk.

The tool does not generate `bc_action_mask`, `corrected_rollout_id`, expert-correction trajectories, or corrected action values. Removing wrong supervision stops teaching an error but does not create the correct recovery behavior; that requires real, traceable targets.

### 16.7 Additional Reproducibility Records

Alongside the full dataset, save actual train/validation/test source groups, code revision, schema/hash, class_ids, H/T, modality switches, camera order, preprocessing, canonical-coordinate settings, normalization statistics, sampling configuration, and task protocol. Named reviewer and per-review timestamp fields are not implemented; revision is not reviewer identification.

Some of these records are in export attachments; others need separate management. Target/receiving-region/W/calibration notes stored in UI preferences do not have a complete standalone exported protocol file.

Formal training must also exclude explicit `test_fixture` and synthetic-demo sources. A fixture can have mask=1 to test rules; the mask does not determine whether data was actually collected on a robot.

## 17. Actual Examples and Inspection

### 17.1 Real Robot Export: 160 Frames Are Not 160 Training Samples

An existing real export is at [`exports/real-history-20261031-moved/`](../exports/real-history-20261031-moved/). It validates loading, cropping, moving, and history configuration without fabricated human semantic labels.

| Item | Actual contents |
|---|---|
| Input episode | 0 |
| Selected centers | Input frames `[190,193)`, 3 frames |
| History configuration | H=5 seconds, T=3 |
| Retained range | Input frames `[40,200)`, 160 frames |
| New episode | 0, local frames 0–159 |
| First center | Local frame 150, corresponding to original frame 190 |
| First-center history | Original 40/115/190, local 0/75/150 |
| Label rows | 160×16=2560 |
| DSEDataset length | 3, counting centers |
| Valid supervision | No semantic labels in this real sample; all three centers are no_supervision with weight 0 |

Successful standard media/numeric cropping and available human semantic supervision are separate acceptance criteria. Read batches to check observations; weighted sampling raises because no center has supervision.

### 17.2 Synthetic Overlap: Same Answer, Different Masks

An explicitly marked fixture is at [`workspaces/inspector-overlap-demo/synthetic-overlap-export/`](../workspaces/inspector-overlap-demo/synthetic-overlap-export/). It is not real human-labeled robot data.

Source center ranges `[20,24)` and `[30,34)` produce two episodes, 58 retained rows, 8 centers, 39 unique source frames, and 19 additional copies.

| Current record | Source frame | Role | left.phase | Review | Mask |
|---|---:|---|---|---|---:|
| EP0 / local frame 18 | 20 | Center, also available as later-center history | 8 unknown | reviewed | 1 |
| EP1 / local frame 8 | 20 | History only | 8 unknown | reviewed | 0 |
| EP1 / local frame 0 | 12 | History only | null | draft | 0 |

Despite a missing label, the third row's actual observation.state is `[12,13,…,29]`, not zeros. **Existing but unlabeled history differs from zero padding generated when requested history does not exist.**

### 17.3 Inspect Without Writing Code

Open **导出检查器** (Export inspector) and select the complete export directory:

1. Check centers, history, review tails, and same-source overlaps on the segment map.
2. Select a frame and compare raw observations, nullable labels, review status, and loss masks.
3. Check original UUID/frame/time against current coordinates in source mapping.
4. Open a center's training-input preview to check actual T timestamps, three-camera images, and missing-input masks.
5. Browse actual Parquet columns, paginated records, and JSON without relying on unexported project edits.

The inspector reads the disk snapshot without modifying the dataset; caches go into the work directory. See the [Export Inspector guide](EXPORT_INSPECTOR_2026-10-31_EN.md).

### 17.4 Inspect Actual Files with Python

After installing the project environment, the following example reads the export without writing to it:

```python
import json
from pathlib import Path
import pyarrow.parquet as pq

root = Path("/path/to/export_dataset")
manifest = json.loads((root / "annotations/manifest.json").read_text())
schema = json.loads((root / "annotations/schema.json").read_text())
samples = pq.read_table(root / "annotations/samples.parquet").to_pylist()
labels = pq.read_table(root / "annotations/labels.parquet").to_pylist()

centers = [r for r in samples if r["center_is_selected"]]
print("Default history:", manifest.get("history_sampling"))
print("Active fields:", [f["field_id"] for f in schema["fields"]])
print("Retained frames / centers:", len(samples), len(centers))
print("Supervised field×frame pairs:", sum(r["effective_loss_mask"] for r in labels))

center = centers[0]
records = [
    r for r in labels
    if (r["episode_index"], r["frame_index"])
       == (center["episode_index"], center["frame_index"])
]
for r in records:
    print(r["arm"], r["field_id"], r["value_id"],
          r["applicability"], r["review_status"], r["effective_loss_mask"])

intervals = pq.read_table(root / "annotations/intervals.parquet").to_pylist()
for row in intervals:
    if row["table"] == "reviews":
        print("Action-quality review:", json.loads(row["payload"]))
```

Use Arrow filtering/pagination for larger data. This is a small-data explanatory example; the current UI/reader's full-table memory strategy has not been validated at large scale.

## 18. Misconceptions, Glossary, and Implementation References

### 18.1 Common Questions

| Question | Correct interpretation |
|---|---|
| Does annotation add 9 files to the original directory? | Original data is read-only; attachments are in the new export's annotations directory |
| Does choosing a category make it training ground truth? | Save, review, and satisfy center/applicability/schema conditions before mask can be 1 |
| Do later label edits update an existing export? | No; exports are snapshots and must be regenerated |
| Is unknown missing? | No; unknown is a category meaning insufficient evidence; missing is no current answer |
| Does mask=0 mean category 0? | No; it disables loss while category 0 can be a valid answer |
| Can not_applicable train as not_ready? | No; inapplicable questions cannot be fabricated as negative examples |
| Does unlabeled history zero images/state? | No; existing observations remain, and only absent history is padded while loading |
| Is total frame count the training sample count? | No; samples come from centers, with separate field validity and sampling eligibility |
| Does complete mean the action is approved? | No; completeness counts and action-quality reviews are independent |
| Does a 0.7 sampling ratio change loss masks? | No; which sample is drawn and which of its heads can train are separate |
| Does head_spec automatically update my model? | No; it defines heads/category order, while training code handles models and checkpoint migration |
| Does exported action omit incorrect actions? | No; originals remain, reviews are in intervals, and BC requires chunk filtering |
| Are annotations a full work-project backup? | No; back up --work to restore the complete workflow |
| Does a self-contained directory verify tasks/units? | No; media independence from original paths does not replace calibration/task-protocol delivery |
| Do names such as bc_action_status/reviewer in documents correspond to actual columns? | Early research documents include planned fields. Current action reviews use status/reason in reviews; named reviewer and automatic BC masks are not implemented |

### 18.2 Glossary

| Term | Plain-language explanation |
|---|---|
| episode | Continuous trajectory; an exported episode may be only part of a demonstration |
| center | The moment whose labels the model must answer |
| context | Accompanying past/future reference data, not automatically independent supervision |
| schema | Label dictionary plus annotation/applicability rules |
| stable ID | Category identity that cannot be reassigned after deletion |
| head/logits | A model question's classification output and raw scores for its options |
| applicability | Whether the question is meaningful now |
| required | Whether a complete annotation must answer the question |
| review | Human review, distinguishing semantic labels from action quality |
| loss mask | Switch controlling whether an answer contributes to loss |
| sampling weight | Relative chance of drawing a sample |
| provenance | Recorded evidence, rules, and settings behind a label |
| source key | Original source UUID+frame for tracing/deduplication |
| split group | Sources that must remain in one train/validation/test split |
| fingerprint/checksum | Content-based identity for detecting replacements, omissions, or changes |
| PTS | Video presentation time; exported source_pts is in seconds |
| causal input | Prediction uses only current/permitted past inputs, without future evidence |
| BC | Behavior cloning: teaching a policy to imitate selected demonstration actions |

### 18.3 Implementation and Document References

Paths below are relative to this manual in docs:

| Reference | What it verifies |
|---|---|
| [exporter.py](../backend/dse_tool/exporter.py) | Actual columns in the 9 attachments, cropping, indexing, statistics, sampling weights, and checksums |
| [schema.py](../backend/dse_tool/schema.py) | Three-valued gates, masks, consistency, dynamic heads, and retired identities |
| [store.py](../backend/dse_tool/store.py) | Work tables, point overrides, save/review versions, and snapshot scope |
| [api.py](../backend/dse_tool/api.py) | State/behavior evidence isolation, provenance, re-import, review, and candidate APIs |
| [history.py](../backend/dse_tool/history.py) | H/T and at_or_before sampling |
| [dataset.py](../backend/dse_tool/dataset.py) | Content identity, media mapping, history loading, and source inheritance |
| [adapter.py](../backend/dse_tool/adapter.py) | Actual batches, category encoding, 36D conversion, and statistics boundaries |
| [assist.py](../backend/dse_tool/assist.py), [review.py](../backend/dse_tool/review.py) | Event/adviser candidates, review semantics, and evidence settings |
| [default.json](../schemas/default.json) | Default dictionary and Chinese semantic instructions |
| [main.tsx](../frontend/src/main.tsx) | UI settings, project preferences, and evidence saved when accepting candidates |
| [Training specification (Chinese)](../../NOTES-Yann/innovation_research_handoff/DSE_OUTPUT_MODEL_TRAINING_SPEC_V1_2026-10-29.md) | Label semantics, causal input, and training/model implementation boundaries |
| [Handling incorrect action supervision (Chinese)](../../NOTES-Yann/innovation_research_handoff/SUPERVISION_CONFLICT_RESOLUTION_2026-10-29.md) | Why semantic review cannot replace BC chunk filtering |
| [Configurable History](CONFIGURABLE_HISTORY_2026-10-31_EN.md), [Range Review](RANGE_REVIEW_2026-10-31_EN.md) | Evidence windows, configuration changes, and human review |
| [Export Inspector](EXPORT_INSPECTOR_2026-10-31_EN.md) | Actual-file inspection, overlapping copies, and training input previews |

Existing real exports validate media and file structure. Semantic examples involving unknown, point overrides, reviews, and overlapping masks use explicitly marked synthetic fixtures. This manual neither fabricates human labels for real robot samples nor describes planned complete training functionality as already implemented.
