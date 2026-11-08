# Interface Language Switching · 2026-11-08

English | [简体中文](UI_LANGUAGE_2026-11-08.md)

## Switching and Persistence

The upper-right language selector offers Simplified Chinese and English, with Chinese as the initial default. Switching immediately updates the workbench, fields/rules, behavior/calibration, export page, export inspector, in-page folder browser, and floating timeline. Native folder windows use the language selected when they are opened.

The choice is saved in local storage for the current browser/site and restored after refresh. Other open pages on the same site also synchronize the language. Clearing site data resets the choice. Interface language is neither a project schema nor a training setting and is not written into exported datasets.

Switching preserves the current page, source frame, selection, mode, enlarged images, and pending group drafts. Re-importing or reviewing labels again is unnecessary. Before refreshing, let normal project autosave finish; the language choice itself is saved immediately when selected.

Already displayed system errors, review skip reasons, and frame-completion results are displayed again in the chosen language without rerunning checks. Raw Parquet/JSON in the inspector, user-entered task text, paths, human notes, and stable field/category keys retain their original content.

## English Text for Custom Fields and Categories

Fields & rules provides these optional properties:

| Object | Property | English UI behavior |
|---|---|---|
| Field | `label_en` | Preferred display name; otherwise retain the original label or field_id |
| Field | `description_en` | Preferred description; otherwise translate known default descriptions or retain custom text |
| Category | `label_en` | Preferred label; otherwise unchanged built-in Chinese labels use built-in English translations, and custom categories fall back to their stable keys |

Chinese category text remains in `label_zh`. Like existing display text, these properties are schema configuration. Validate and publish edits before they appear in the active version and later exports. Publication retains the existing migration rules and requires reviewing older labels again. Merely switching languages does not publish schemas or change category IDs, label values, review statuses, or loss masks.

See the [Dataset Manual](ANNOTATED_DATASET_MANUAL_EN.md#6-schemajson-label-dictionary-and-rules) for complete field definitions.

## Updating an Existing Installation

```bash
npm run build --prefix frontend
```

Restart the service with the same `--work` directory, then refresh the browser. An installation already built locally needs only the restart and refresh.

## Validation and Reproduction

Translation and dynamic-message checks:

```bash
npm run test:i18n --prefix frontend
./scripts/test.sh -q
```

`test:i18n` checks all 686 translations, interpolation parameters, composite errors, unchanged Chinese paths, and default/custom category fallbacks. Python regression also checks native English folder-window titles, Chinese defaults, language-parameter validation, and unchanged selected paths.

Browser acceptance uses an isolated temporary workspace and explicitly synthetic data:

```bash
./scripts/run.sh --work /tmp/dse-i18n-test --port 8891
# In another terminal; requires Playwright or DSE_PLAYWRIGHT_MODULE pointing to its module file
node scripts/validate_i18n.mjs
```

Set `DSE_BASE_URL` for the service address and `DSE_BROWSER_EXECUTABLE` for Chrome. The script rejects workspaces outside `/tmp/dse-i18n-*` and prints the temporary directory containing fixtures, screenshots, and its machine report.

Acceptance covers all major pages, retranslation of existing messages, folder browsing/native-picker fallback, language/draft restoration after refresh, frame/selection preservation during switching, inspector tab/frame preservation, and language-control visibility in narrow windows. It compares annotation/selection/review/event/schema tables and content fingerprints of source data and standalone exports to check data integrity through language changes.

Measured results: all 52 Python regression tests, 686 translation checks, the TypeScript/Vite production build, and browser acceptance passed. No page runtime errors occurred; source-data and standalone-export fingerprints were unchanged. See the [machine acceptance record](ui-language-validation.json).
