# Validation Report · 2026-10-31

English | [简体中文](VALIDATION.md)

2026-11-10 update: Python package 0.2.0 passed **66 tests**, covering variable dimensions, named scalars in 31D data, shared pose conversion, and legacy 36D compatibility. See the [real 31D report](named-scalar-real-validation.json) and [integration contract](STATE_LAYOUT_INTEGRATION.md). The original 2026-10-31 acceptance results below remain historical; prior GPU/official-loader validation was not rerun for this update.

Implementation and validation were performed in the separate `dse_annotation_tool` directory, with `git init` completed. The original dataset directory was read-only. The following records actual execution results, rather than plans or simulated outcomes.

## Automated Validation

- Python: `./scripts/test.sh -q`, **18 passed** at the time of this report. Coverage included three-valued rules, unknown/missing masks, independent arms and explicit cross-arm dependencies, range splitting, per-frame overrides, undo/redo and restart, schema migration and dynamic heads, concurrent APIs, media/version rejection, background-export cancellation, existing-target protection, moving/reopening, ancestral mappings after repeated cropping, checksums, and schema/import undo boundaries.
- Frontend: TypeScript checks and Vite production build passed. npm audit reported 0 vulnerabilities at installation. Production JS was about 593 KB (about 180 KB gzip); the build's single-chunk-over-500-KB warning was a known size warning.
- Official compatibility: unmodified `lerobot.datasets.lerobot_dataset.LeRobotDataset`, with `lerobot=0.3.3`, `torch=2.7.1+cpu`, `datasets=3.6.0`, and `video_backend=pyav`. In an isolated `.validation` environment with Hub network access disabled, it read the moved **2-episode / 105-row** export and checked first/middle/last data and all three 480×640 videos. Exact dependencies are in `requirements/lerobot-validation.lock`; output is in `native-validation.json`. PyTorch/LeRobot are not web-service dependencies.

Official v0.3.3 declares [CODEBASE_VERSION as v2.1](https://github.com/huggingface/lerobot/blob/v0.3.3/src/lerobot/datasets/lerobot_dataset.py). Compatibility here is supported by reading with the actual official loader, rather than replacing that acceptance check with a custom reader.

## Real Data

- Source: `/home/punk/yann_repo/paper_code_01/tron2_dataset/lerobot_v2.1_examples`.
- **14 episodes / 13,432 rows / 3 cameras / 30 FPS**; all 14 episodes were opened. First import indexed packet PTS and decoded the first frame of each camera, without decoding all videos into memory.
- Episode 0: 299 frames. At t=5 seconds, historical indices were 132 / 138 / 144 / 150, corresponding to 4.4 / 4.6 / 4.8 / 5.0 seconds. The browser displayed four timestamps across three cameras.
- There were 98 recorded differences between declared float32 metadata and actual double storage. Exported numeric rows matched their original sources, preserving physical precision and updating metadata.
- Centers `[150,180)` and `[220,245)` totaled 55 center frames. Export produced 2 independent episodes / 105 rows, including 50 context-only rows. History and tails were retained by time without joining the gap.
- First/middle/last frames of both segments were compared across three cameras, totaling 18 comparisons. PTS mapping was correct. Lossy H.264 re-encoding had a maximum mean absolute pixel error of 1.7633/255; pixel identity is not claimed.
- After copying the export elsewhere, the tool reopened it and the DSE wrapper built an image batch of `[2, 4, 3, 3, 256, 256]`. The native loader also read this copy without original video paths.
- All real semantic labels remained **missing**. No fabricated human-reviewed ground truth was generated. Unknown supervision, missing/inapplicable/context-only masking, and sampling were tested on synthetic fixtures.
- The complete original-data content fingerprint matched before and after: `ee5011e1baece32d12286f19e25ca9b9ed69c2470f3245e0621ad6a77f91d9d9`. No cache, work database, or export was written inside the source directory.

## Measured Performance

Machine: Intel(R) Core(TM) Ultra 7 270K Plus, 24 logical cores, Linux-7.0.0-28-generic-x86_64-with-glibc2.39.

| Operation | Time / memory |
|---|---:|
| Complete import into a new work project | 2.563 seconds |
| 12-image history panel, cold JPEG cache | 0.112 seconds |
| Same panel, warm cache | 0.020 seconds |
| Single-camera seek, cold cache | 0.0078 seconds |
| Single-camera seek, warm cache | 0.0019 seconds |
| Export of 2 segments / 105 rows / 6 output videos | 4.475 seconds |
| Peak acceptance-process RSS | 486.5 MiB |

These are service-request/batch-processing times, not end-to-end browser rendering times. Cold cache means the application's JPEG cache; OS page cache was not cleared. Results cover only 14 sample episodes and do not establish large-scale performance. In-memory caches retain at most 3 episode tables and 9 media indices. The JPEG disk cache is 256 MiB, and index cache capacity is 64 MiB per source; evicted data is rebuilt when needed.

## Browser Interaction

The local embedded browser exercised real import → synchronization at t=5 seconds → rapid jumps to frames 100/220/150 → step to 151 → playback/resume → state/behavior switching. Both the default narrow viewport and a 1440×1000 desktop layout were tested. While awaiting earlier requests, editing was locked and old images hidden in that implementation.

Label writes used the explicitly named **Synthetic test fixture**: left grasp_align/right transport → select [25,30) → preview/confirm range overrides → configure visibility_check and gating → publish v2 → inspect dynamic form and needs_review → override frame 27 with unknown → undo to grasp_align/redo unknown → background export → re-import through the UI → verify new segment frame 20 retained source frame 27's unknown and the added field → restart and recover.

The implementation makes import and schema publication undo boundaries. Old revisions remain, but snapshots from before an import cannot replace the newly loaded state. Ordinary edits retain undo/redo and restart recovery.

## Inspectable Artifacts

- `exports/real-acceptance-final/`: real standalone export, 55 centers, all labels missing.
- `exports/real-acceptance-final-moved/`: copy used for relocation and official-loader acceptance.
- `workspaces/real-acceptance-final/`: sample work project; reopen with `./scripts/run.sh --work ./workspaces/real-acceptance-final`.
- `exports/ui-acceptance/` and `workspaces/ui-fixture/`: synthetic fixtures and UI-test artifacts, not real training ground truth.
- `real-validation.json` and `native-validation.json`: raw measurements, mappings, and loader output.
- `workbench.png`: screenshot of the interface displaying real data.

## Tested Scope and Items Requiring Confirmation

Real coordinate units, transforms, and gripper thresholds had not been checked by the user, so automatic event/direction pre-annotation was disabled by default. Manual annotation and features independent of calibration remained available. Testing did not exhaustively decode every frame of all 42 videos, stress-test very large datasets, run long sessions, or inject real power-loss/filesystem failures. DSE/VLA training and physical-robot connections were not performed. Schema publication conservatively migrates old fields to needs_review and leaves new fields missing; user review is required to restore supervision.
