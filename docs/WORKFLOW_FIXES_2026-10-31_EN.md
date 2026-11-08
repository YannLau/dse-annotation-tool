# Annotation Workflow Fixes · 2026-10-31

English | [简体中文](WORKFLOW_FIXES_2026-10-31.md)

## 1. Fill Continuously, Confirm Once

Batch mode uses a group draft. Selecting a phase immediately updates the dropdown and recalculates gates for the draft, updating each arm's required fields. Other fields can be filled without separate override-confirmation dialogs; click **确认保存全部 N 项** (Confirm and save all N items) once at the end.

Preview uses a SQLite savepoint to compute actual affected ranges, skipped/undetermined entries, and per-frame exceptions, then rolls everything back without writing formal labels or revision history. Submission writes in dependency order within one transaction. Any failure rolls back the group, and one undo/redo handles the entire group. Per-frame overrides retain priority in this workflow, with exception counts shown explicitly. Dropdowns show assignment targets; actual submission follows each frame's applicability.

Pending group drafts persist in browser storage and SQLite preferences and can be restored after refresh. Individual items can be removed, or the whole group canceled. Drafts from an old schema cannot be accidentally submitted against a new version. Single-frame mode still saves automatically.

## 2. Future Images with a Fixed Decision Frame

Behavior review adds a future-reference panel displaying three-camera images at t+0.1, t+0.2, and t+0.25 seconds by default. Images identify actual source frames/timestamps and can be enlarged. Reference horizons of 0.25/0.5/1/2/5 seconds do not move the decision frame or annotation target.

At 30 FPS, t+0.25 seconds uses a real frame no later than the target: decision frame 150 (5 seconds) gives frames 153 / 156 / 157 at 5.1 / 5.2 / 5.233333 seconds. Requests beyond available episode future show missing cards without duplicating the last frame. Longer references are for behavior review only; automatic adviser computation still uses at most 0.25 seconds of continuously approved actions.

Returning to state annotation immediately removes the future panel, and the server returns an empty future list. Future images never enter DSE inputs. Future/history requests share the cursor version, preventing stale responses from replacing newer frames. Behavior-review buttons review only fields for that mode and do not attempt to publish state labels from behavior mode.

## 3. Deletion with Dependency Checks

Fields and categories have delete controls that first list blockers and historical label usage. Checks cover eq/ne/in references nested in all/any/not, explicit arm/global references, independent-review references, unknown_id, and built-in semantic-consistency dependencies. The foundational phase field cannot be removed directly; dependent references must first be removed.

An unreferenced item can be removed from a configuration draft. Publication removes it from active forms, exported schemas, and head_spec. Old schemas and raw annotations remain. Retired identities are registered in retired_fields / retired_classes, preventing reuse of deleted keys/IDs in later versions. Automatic category creation also avoids retired IDs.

Historical values of deleted categories appear as missing/needs_review with mask=0 in the current version, alongside their original category ID. Original values remain in revisions/intervals and can be traced after export/reopening. Unpublished deletion can be undone without overwriting edits made afterward. Schema publication also refreshes episode review progress.

## Validation Results

- Complete Python regression at the time of this report: **26 passed**, the original 18 plus 8 workflow tests.
- TypeScript checks and Vite production build passed; no runtime dependencies were added.
- Browser checks filled 6 items continuously: before confirmation, the formal label table had 0 rows and SQLite draft storage had 6 items. Refresh restored them; one save and one undo/redo handled the whole group.
- Behavior-mode adviser entry/review completed without modifying state fields through behavior review.
- Browser checks blocked phase deletion and allowed deletion of unreferenced categories/fields. After deleting adviser.x category 1, automatic creation chose ID 4. Deleting adviser.y and publishing v2 removed it from the form; historical category 1 appeared as missing with an explanation.
- Undoing unpublished deletion preserved description text added after the deletion.
- On real AV1 data at decision frame 150, the three future references were 153 / 156 / 157. Changing horizon and switching modes preserved decision frame 150; state mode had no future panel.
- Automated tests covered export after deletion, the DSE wrapper, and reopening in the tool, confirming preserved history with effective mask=0. Existing standalone-export and precision tests still passed.

Browser label writes and schema deletion used the Synthetic test fixture in isolated project `workspaces/qa-workflow-20261031`. Real sources were used only for media viewing; real semantic labels were not fabricated.

Screenshots:

- [Group draft](batch-draft.png)
- [Dependency-blocked deletion](schema-delete-dependencies.png)
- [Real future references](future-reference.png)

For the existing installation, restart the service, refresh the page, and continue with the same `--work`; the frontend was already built. When checking these changes out from Git, first run `npm run build --prefix frontend`.
