// Run against an isolated server: scripts/run.sh --work /tmp/dse-smooth-test --port 8876
// Requires Playwright (or DSE_PLAYWRIGHT_MODULE pointing to its module).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const base = process.env.DSE_BASE_URL || "http://127.0.0.1:8876";
const { chromium } = await import(
  process.env.DSE_PLAYWRIGHT_MODULE || "playwright"
);
const fixture = mkdtempSync(resolve(tmpdir(), "dse-smooth-fixture-"));
const python = spawnSync(
  resolve(root, ".venv/bin/python"),
  [
    "-c",
    `
import json, shutil, sys
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
sys.path.insert(0, 'tests')
from conftest import source
root = source.__wrapped__(Path(sys.argv[1]))
for p in list(root.rglob('episode_000000.mp4')):
    shutil.copyfile(p, p.with_name('episode_000001.mp4'))
p = root / 'data/chunk-000/episode_000000.parquet'
rows = pq.read_table(p).to_pylist()
for r in rows:
    r['episode_index'] = 1
    r['index'] += len(rows)
pq.write_table(pa.Table.from_pylist(rows), p.with_name('episode_000001.parquet'))
p = root / 'meta/episodes.jsonl'
episode = json.loads(p.read_text())
episode['episode_index'] = 1
p.write_text(p.read_text() + json.dumps(episode) + '\\n')
p = root / 'meta/episodes_stats.jsonl'
stats = json.loads(p.read_text())
stats['episode_index'] = 1
p.write_text(p.read_text() + json.dumps(stats) + '\\n')
p = root / 'meta/info.json'
info = json.loads(p.read_text())
info.update(total_episodes=2, total_frames=120, total_videos=6, chunks_size=2, splits={'train': '0:2'})
p.write_text(json.dumps(info))
other = root.with_name('other-source')
shutil.copytree(root, other)
p = other / 'meta/tasks.jsonl'
task = json.loads(p.read_text())
task['task'] = 'Second synthetic test fixture'
p.write_text(json.dumps(task) + '\\n')
`,
    fixture,
  ],
  { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONPATH: "" } },
);
assert.equal(python.status, 0, python.stderr);

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.DSE_BROWSER_EXECUTABLE || "/usr/bin/google-chrome",
  args: ["--no-sandbox"],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
const get = async (path) => {
  const response = await page.request.get(base + "/api" + path);
  assert.ok(response.ok(), await response.text());
  return response.json();
};
const post = async (path, data) => {
  const response = await page.request.post(base + "/api" + path, { data });
  assert.ok(response.ok(), await response.text());
  return response.json();
};
const report = {};
const waits = new Set();
// A gate lets assertions run while exactly one camera or metadata response is late.
function gate() {
  let release, started;
  const ready = new Promise((resolve) => {
    started = resolve;
  });
  const pending = new Promise((resolve) => {
    release = resolve;
  });
  waits.add(release);
  return {
    ready,
    release,
    hold: async () => {
      started();
      await pending;
    },
  };
}
let holdImage = null,
  holdWindow = null,
  failFrame = null;
const imageRequests = [];
page.on("request", (request) => {
  if (request.url().includes("/api/frame/")) imageRequests.push(request.url());
});
await page.route("**/api/frame/**", async (route) => {
  const url = new URL(route.request().url());
  const parts = url.pathname.split("/");
  const frame = Number(parts.at(-1)),
    camera = Number(parts.at(-2));
  if (frame === failFrame && camera === 2)
    return route.fulfill({ status: 503, body: "simulated media error" });
  if (holdImage?.frame === frame && camera === 2) await holdImage.gate.hold();
  await route.continue().catch(() => {});
});
await page.route("**/api/window/**", async (route) => {
  const url = new URL(route.request().url());
  if (holdWindow?.matches(url)) await holdWindow.gate.hold();
  await route.continue().catch(() => {});
});
const sourceFrame = () => page.getByLabel("源帧", { exact: true });
const phase = () => page.getByLabel("left phase", { exact: true });
const waitFrame = async (frame, episode = 0) => {
  await page.waitForFunction(
    ({ frame, episode }) => {
      const images = [...document.querySelectorAll(".current-cameras img")];
      return (
        images.length === 3 &&
        images.every(
          (image) =>
            image
              .getAttribute("src")
              .endsWith(`/${episode}/${images.indexOf(image)}/${frame}`) &&
            image.complete &&
            image.naturalWidth > 0,
        ) &&
        document.querySelector(".camera-region").getAttribute("aria-busy") ===
          "false"
      );
    },
    { frame, episode },
  );
};
const capture = () =>
  page.locator(".camera-region").evaluate((region) => ({
    images: [...region.querySelectorAll("img")].map((image) =>
      image.getAttribute("src"),
    ),
    captions: [...region.querySelectorAll(".camera-label")].map(
      (label) => label.textContent,
    ),
  }));

try {
  const project = await get("/project");
  assert.match(
    project.work_root,
    /^\/tmp\/dse-smooth-/,
    "Use an isolated --work /tmp/dse-smooth-* project for this validation",
  );
  const dataset = await post("/import", { path: resolve(fixture, "source") });
  const key = dataset.fingerprint;
  const other = await post("/import", {
    path: resolve(fixture, "other-source"),
  });
  assert.notEqual(key, other.fingerprint);
  await post("/preferences", {
    datasetKey: key,
    episode: 0,
    frame: 30,
    mode: "state",
    historyConfig: { horizon_seconds: 0.6, num_frames: 4 },
  });
  await page.goto(base);
  await waitFrame(30);
  const before = await capture();

  // Observe every rendered frame through slow loads and rapid navigation.
  await page.evaluate(() => {
    window.frameProbe = { samples: 0, blank: 0, mixed: 0, overlay: 0 };
    const probe = () => {
      const region = document.querySelector(".camera-region");
      const images = [
        ...(region?.querySelectorAll(".current-cameras img") || []),
      ];
      const p = window.frameProbe;
      p.samples++;
      if (
        images.length !== 3 ||
        images.some((i) => !i.complete || !i.naturalWidth)
      )
        p.blank++;
      if (
        new Set(images.map((i) => i.getAttribute("src").split("/").at(-1)))
          .size > 1
      )
        p.mixed++;
      if (document.querySelector(".loading-overlay")) p.overlay++;
      p.id = requestAnimationFrame(probe);
    };
    probe();
  });
  holdImage = { frame: 31, gate: gate() };
  await sourceFrame().fill("31");
  await holdImage.gate.ready;
  assert.deepEqual(
    await capture(),
    before,
    "Keep all displayed cameras and captions while one image is late",
  );
  assert.ok(
    await phase().isDisabled(),
    "Never edit the requested frame using previous evidence",
  );
  await page.waitForTimeout(150);
  assert.deepEqual(await capture(), before);
  holdImage.gate.release();
  holdImage = null;
  await waitFrame(31);
  report.slow_camera_keeps_snapshot = true;

  const warmCount = imageRequests.length;
  await sourceFrame().fill("30");
  await waitFrame(30);
  assert.equal(
    imageRequests.length,
    warmCount,
    "Revisited frames reuse decoded images",
  );
  // Labels must still be fresh even though media came from cache.
  await post("/annotations", {
    dataset: key,
    episode: 0,
    start: 31,
    end: 32,
    arm: "left",
    field: "phase",
    value: 2,
    mode: "state",
    reviewed: false,
    confirmed: false,
    point: true,
    history_config: { horizon_seconds: 0.6, num_frames: 4 },
  });
  await sourceFrame().fill("31");
  await waitFrame(31);
  assert.equal(await phase().inputValue(), "2");
  await phase().selectOption("4");
  await page.waitForFunction(
    () =>
      document.querySelector('[aria-label="left phase"]').value === "4" &&
      !document.querySelector('[aria-label="left phase"]').disabled,
  );
  assert.equal(
    (await get(`/window/${key}/0/31`)).labels.left.phase.value_id,
    4,
  );
  assert.equal(
    (await get(`/window/${key}/0/30`)).labels.left.phase.value_id,
    null,
  );
  report.cached_images_fresh_labels_correct_write_frame = true;

  holdWindow = { matches: (url) => url.pathname.endsWith("/40"), gate: gate() };
  await sourceFrame().fill("40");
  await holdWindow.gate.ready;
  await sourceFrame().fill("41");
  await waitFrame(41);
  holdWindow.gate.release();
  holdWindow = null;
  await page.waitForTimeout(150);
  await waitFrame(41);
  holdImage = { frame: 37, gate: gate() };
  await sourceFrame().fill("37");
  await holdImage.gate.ready;
  await sourceFrame().fill("38");
  await waitFrame(38);
  holdImage.gate.release();
  holdImage = null;
  await page.waitForTimeout(150);
  await waitFrame(38);
  const probe = await page.evaluate(() => {
    cancelAnimationFrame(window.frameProbe.id);
    const { id, ...result } = window.frameProbe;
    return result;
  });
  assert.equal(probe.blank, 0);
  assert.equal(probe.mixed, 0);
  assert.equal(probe.overlay, 0);
  report.rapid_seek_probe = probe;

  const lastGood = await capture();
  failFrame = 43;
  await sourceFrame().fill("43");
  await page.getByRole("alert").waitFor();
  assert.deepEqual(
    await capture(),
    lastGood,
    "A failed image keeps the previous complete snapshot",
  );
  assert.ok(await phase().isDisabled());
  failFrame = null;
  await sourceFrame().fill("44");
  await waitFrame(44);
  report.media_error_preserves_picture_and_locks_writes = true;

  await page.getByRole("button", { name: "行为复核", exact: true }).click();
  await waitFrame(44);
  await page.locator(".future-reference img").first().click();
  await page.locator(".modal > img").waitFor();
  holdWindow = {
    matches: (url) => url.searchParams.get("mode") === "state",
    gate: gate(),
  };
  await page.keyboard.press("Tab");
  await holdWindow.gate.ready;
  assert.equal(await page.locator(".future-reference, .modal").count(), 0);
  assert.equal(
    await page.locator(".current-cameras img").count(),
    0,
    "A snapshot from a different evidence context must not be reused",
  );
  holdWindow.gate.release();
  holdWindow = null;
  await waitFrame(44);
  report.state_mode_hides_future_and_enlarged_future = true;

  await page.locator(".viewer-heading h2").click();
  await page.keyboard.press("ArrowRight");
  await waitFrame(45);
  await page.keyboard.press("ArrowLeft");
  await waitFrame(44);
  await page.getByLabel("帧游标", { exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await waitFrame(45);
  report.keyboard_and_scrubber_navigation = true;

  await page.getByRole("button", { name: "↓ 下一条", exact: true }).click();
  await waitFrame(0, 1);
  assert.equal(await page.locator(".history-grid img").count(), 3);
  await page.getByLabel("历史跨度（秒）", { exact: true }).fill("0");
  await page.getByLabel("历史时刻数（含当前）", { exact: true }).fill("1");
  await page.getByRole("button", { name: "应用历史设置", exact: true }).click();
  await waitFrame(0, 1);
  assert.equal(await page.locator(".history-time").count(), 1);
  report.episode_switch_missing_history_single_frame_history = true;

  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.waitForFunction(
    () => Number(document.querySelector('[aria-label="源帧"]').value) >= 10,
  );
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const stopped = Number(await sourceFrame().inputValue());
  await waitFrame(stopped, 1);
  report.playback_stops_on_aligned_integer_frame = stopped;
  await page.getByRole("button", { name: "↑ 上一条", exact: true }).click();
  await waitFrame(0);
  holdWindow = {
    matches: (url) => url.pathname.includes(other.fingerprint),
    gate: gate(),
  };
  await page
    .getByLabel("已登记数据集", { exact: true })
    .selectOption(other.fingerprint);
  await holdWindow.gate.ready;
  assert.equal(await page.locator(".current-cameras img").count(), 0);
  assert.ok(await phase().isDisabled());
  holdWindow.gate.release();
  holdWindow = null;
  await waitFrame(0);
  assert.ok(
    (await capture()).images.every((url) => url.includes(other.fingerprint)),
  );
  report.same_frame_different_dataset_isolation = true;
  assert.deepEqual(errors, []);
  report.page_errors = errors;
  console.log(JSON.stringify(report, null, 2));
} finally {
  for (const release of waits) release();
  await browser.close();
  rmSync(fixture, { recursive: true, force: true });
}
