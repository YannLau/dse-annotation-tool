# Smooth Frame Switching (2026-11-01)

English | [简体中文](SMOOTH_FRAME_SWITCHING_2026-11-01.md)

Previously, frame changes immediately removed images and displayed an overlay. Request cleanup also revoked Blob URLs still on screen. Each change waited a fixed 70 milliseconds, and request_id in image URLs prevented browser reuse of identical frames.

A complete snapshot now commits images, labels, and telemetry together. During a change, the previous group remains visible until every new image has decoded, then the new group replaces it at once. Captions identify the displayed frames. Metadata is requested each time to avoid stale labels. Editing is enabled only when the requested frame, source, evidence window, and refresh version all match. Rapid navigation cancels outdated requests; failures preserve the previous group and lock label writes.

Stable HTTP image URLs allow decoded-image reuse. The frontend LRU is limited to an estimated 64 MiB of decoded pixels and at most 128 images; the browser manages its own cache and currently displayed images separately. The fixed 70-millisecond delay is removed, and animation frames coalesce rapid input. Playback uses FPS, speed, and wall-clock scheduling without the former extra 80-millisecond interval. Cold decoding still depends on the video, disk, and machine.

Snapshots are retained only within the same dataset, episode, mode, history configuration, and behavior-reference horizon. Different evidence windows do not reuse old snapshots. Returning to state mode immediately hides future references and their enlarged views. Initial loading and genuinely unavailable frames still show placeholder cards.

The backend API, dataset format, export protocol, and training adapter were unchanged by this update.

## Validation

- `cd frontend && npm run build`.
- `./scripts/test.sh -q`: 41 tests passed at the time of this report.
- Browser regression: artificial delay for one camera, cancellation of outdated metadata/image requests, rapid frame jumps, warm-cache revisits, refreshed labels and write-target checks, image-load failure, mode/enlargement switching, keyboard/cursor operation, episode/dataset changes, missing history, single-timestamp history, and playback/pause.
- Per-render-frame sampling during consecutive changes checked for blanks, mixed camera frames, and loading overlays.
- On the real 640×480 three-camera AV1 sample, changes from source frame 150 to 151/152/153 took about 136/117/134 milliseconds; returning to cached frame 150 took about 33 milliseconds. Playback stopped at 240 after 3 seconds. Across 217 render samples, there were no blanks, mixed frames, or page errors. Timings include browser-automation operations and represent one local run, not performance guarantees for all videos or machines.

Results are in [frame-switching-validation.json](frame-switching-validation.json).

Browser regression uses isolated temporary synthetic data and a work database, without operating on the real annotation project:

```bash
./scripts/run.sh --work /tmp/dse-smooth-test --port 8876
# In another terminal; requires Playwright or DSE_PLAYWRIGHT_MODULE
node scripts/validate_frame_switching.mjs
```

Use `DSE_BASE_URL` to set the address and `DSE_BROWSER_EXECUTABLE` to set the Chrome path. The script refuses to write to work databases outside `/tmp/dse-smooth-*`.
