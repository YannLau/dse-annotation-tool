// Use an isolated server: scripts/run.sh --work /tmp/dse-i18n-test --port 8891
// Requires Playwright, or DSE_PLAYWRIGHT_MODULE pointing to its module.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const base = process.env.DSE_BASE_URL || "http://127.0.0.1:8891";
const { chromium } = await import(
  process.env.DSE_PLAYWRIGHT_MODULE || "playwright"
);
const response = await fetch(base + "/api/project");
assert.ok(response.ok);
const project = await response.json();
assert.ok(
  project.work_root.startsWith("/tmp/dse-i18n-"),
  "Only an isolated /tmp/dse-i18n-* workspace is allowed",
);
const fixture = mkdtempSync(resolve(tmpdir(), "dse-i18n-fixture-"));
const python = (code, ...args) => {
  const result = spawnSync(
    resolve(root, ".venv/bin/python"),
    ["-c", code, ...args],
    {
      cwd: root,
      encoding: "utf8",
      env: { ...process.env, PYTHONPATH: "" },
    },
  );
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
};
const data = python(
  `
import json, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from conftest import source
from dse_tool.dataset import Dataset, content_fingerprint
from dse_tool.store import Store
from dse_tool.exporter import Exporter
root = Path(sys.argv[1])
data = source.__wrapped__(root)
renamed = data.with_name('synthetic-中文-source')
data.rename(renamed)
data = renamed
p = data / 'meta/tasks.jsonl'
task = json.loads(p.read_text())
task['task'] = 'Synthetic test fixture · 中文任务保留'
p.write_text(json.dumps(task, ensure_ascii=False) + '\\n')
key = content_fingerprint(data)[0]
dataset = Dataset(data, root / 'cache')
store = Store(root / 'export-work')
source_key = dataset.source_key(0, 0, key)[0]
store.annotate(source_key, 0, 60, 'left', 'phase', 8, 'state', True, True,
               label_source='test_fixture', provenance={'test_fixture': True})
for start, end in ((20, 24), (30, 34)):
    store.select(key, 0, start, end)
exporter = Exporter(store)
destination = root / 'synthetic-export'
job = exporter.start(dataset, key, destination)
exporter.threads[job['id']].join(30)
job = json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
assert job['status'] == 'complete', job
print(json.dumps({'source': str(data), 'source_hash': key, 'export': str(destination),
                  'export_hash': content_fingerprint(destination)[0]}))
`,
  fixture,
);
const snapshot = () =>
  python(
    `
import json, sqlite3, sys
db = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
print(json.dumps({table: [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                  for table in ['annotations', 'selections', 'reviews', 'events', 'schemas']}))
`,
    resolve(project.work_root, "project.sqlite3"),
  );

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.DSE_BROWSER_EXECUTABLE || "/usr/bin/google-chrome",
  args: ["--no-sandbox"],
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
});
const page = await context.newPage();
const errors = [];
const report = {};
const preferenceTrace = [];
page.on("request", (request) => {
  if (request.url().endsWith("/api/preferences")) {
    const p = request.postDataJSON();
    preferenceTrace.push([
      "save",
      p.frame,
      p.range,
      p.interval,
      p.batchDraft?.changes.length,
    ]);
  }
});
page.on("response", async (response) => {
  if (response.url().endsWith("/api/project")) {
    try {
      const p = (await response.json()).preferences;
      preferenceTrace.push([
        "load",
        p.frame,
        p.range,
        p.interval,
        p.batchDraft?.changes.length,
      ]);
    } catch {
      /* A reload can cancel an unneeded project read. */
    }
  }
});
page.on("pageerror", (error) => errors.push(String(error)));
const language = page.locator("#ui-language");
const nav = (name) =>
  page.locator("nav").getByRole("button", { name, exact: true });
const visible = async (locator) => {
  await locator.waitFor({ state: "visible" });
};
const changeLanguage = async (value) => {
  await language.selectOption(value);
  await page.waitForFunction(
    (value) => document.documentElement.lang === value,
    value,
  );
};
try {
  await page.goto(base);
  await visible(language);
  assert.equal(await language.inputValue(), "zh-CN");
  await changeLanguage("en");
  if (!project.datasets.length)
    await visible(
      page.getByRole("heading", {
        name: "Build reliable labels from real observations.",
      }),
    );
  assert.match(await page.title(), /Dual-arm/);
  report.empty_page_and_document_language = true;

  await nav("Fields & rules").click();
  await visible(page.getByPlaceholder("Stable key for the new field"));
  await visible(page.getByLabel("English display name (optional)"));
  await page.getByRole("button", { name: "Delete field", exact: true }).click();
  const deletion = page.getByRole("dialog", {
    name: "Deletion dependency check",
  });
  await visible(deletion.getByText("Cannot delete", { exact: false }));
  assert.match(await deletion.innerText(), /foundational/);
  await changeLanguage("zh-CN");
  await visible(page.getByRole("dialog", { name: "删除依赖检查" }));
  assert.match(
    await page.locator(".schema-delete-dialog").innerText(),
    /基础字段/,
  );
  await changeLanguage("en");
  await deletion
    .getByRole("button", { name: "Close / Cancel", exact: true })
    .click();
  report.schema_and_existing_server_reasons_switch = true;

  await nav("Export inspector").click();
  await visible(
    page.getByRole("heading", { name: "Exported dataset inspector" }),
  );
  await page
    .getByLabel("Export directory to inspect", { exact: true })
    .fill(fixture + "/missing");
  await page
    .getByRole("button", { name: "Open & inspect", exact: true })
    .click();
  await visible(page.locator(".export-inspector [role=alert]"));
  assert.match(
    await page.locator(".export-inspector [role=alert]").innerText(),
    /Select an exported LeRobot/,
  );
  await changeLanguage("zh-CN");
  assert.match(
    await page.locator(".export-inspector [role=alert]").innerText(),
    /请选择已导出/,
  );
  await changeLanguage("en");

  await page
    .getByRole("button", {
      name: "Browse in page: Export inspection directory",
      exact: true,
    })
    .click();
  const folders = page.locator(".directory-dialog");
  await visible(folders);
  await visible(folders.getByText("Use this folder", { exact: true }));
  const browsePath = await folders
    .getByLabel("Folder browser path")
    .inputValue();
  await changeLanguage("zh-CN");
  assert.equal(
    await folders.getByLabel("文件夹浏览路径").inputValue(),
    browsePath,
  );
  await changeLanguage("en");
  await folders.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.route("**/api/filesystem/pick-directory", async (route) => {
    assert.equal(route.request().postDataJSON().language, "en");
    await route.fulfill({
      json: {
        status: "unavailable",
        message: "服务没有桌面环境，请在页面内浏览文件夹",
      },
    });
  });
  await page
    .getByRole("button", {
      name: "Select an exported dataset folder to inspect",
      exact: true,
    })
    .click();
  await visible(folders);
  await visible(
    folders.getByText(
      "The service has no desktop environment. Browse folders in the page.",
    ),
  );
  await folders.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.unroute("**/api/filesystem/pick-directory");
  report.inspector_errors_and_folder_picker = true;

  await nav("Annotation workbench").click();
  const sourcePath = page.getByLabel("Local dataset path", { exact: true });
  await sourcePath.fill(fixture + "/中文-missing");
  await page
    .getByRole("button", { name: "Import / Rebind", exact: true })
    .click();
  await visible(page.locator(".notice.error"));
  assert.match(
    await page.locator(".notice.error").innerText(),
    /File not found:/,
  );
  assert.match(await page.locator(".notice.error").innerText(), /中文-missing/);
  await sourcePath.fill(data.source);
  await page
    .getByRole("button", { name: "Import / Rebind", exact: true })
    .click();
  const phase = page.getByLabel("left phase", { exact: true });
  await phase.waitFor();
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="left phase"]').disabled,
  );
  const oldDrafts = page.getByRole("button", {
    name: "Cancel group draft",
    exact: true,
  });
  if (await oldDrafts.count()) await oldDrafts.click();
  assert.match(
    await phase
      .locator("option")
      .allTextContents()
      .then((x) => x.join(" ")),
    /Idle.*Approach.*Unknown/,
  );
  assert.equal(await sourcePath.inputValue(), data.source);
  assert.match(
    await page.locator(".viewer-heading").innerText(),
    /中文任务保留/,
  );
  await page.getByLabel("Source frame", { exact: true }).fill("25");
  await page.waitForFunction(
    () => !document.querySelector('[aria-label="left phase"]').disabled,
  );
  await page.getByLabel("Selection start frame", { exact: true }).fill("25");
  await page
    .getByLabel("Selection end frame, exclusive", { exact: true })
    .fill("30");
  await page
    .getByLabel("Apply the next assignment to the explicit selection", {
      exact: true,
    })
    .check();
  await phase.selectOption("2");
  const confirm = page.getByRole("button", { name: /Confirm and save all/ });
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (b) => /Confirm and save all/.test(b.textContent) && !b.disabled,
    ),
  );
  await page
    .getByLabel("left close_readiness", { exact: true })
    .selectOption("0");
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (b) => /Confirm and save all 2/.test(b.textContent) && !b.disabled,
    ),
  );
  const before = snapshot();
  const drafts = await page.evaluate(
    () =>
      Object.entries(localStorage).find(([key]) =>
        key.startsWith("dse-batch-draft:"),
      )?.[1],
  );
  await changeLanguage("zh-CN");
  await visible(
    page.getByRole("button", { name: "确认保存全部 2 项", exact: true }),
  );
  assert.equal(
    await page.getByLabel("源帧", { exact: true }).inputValue(),
    "25",
  );
  assert.equal(await phase.inputValue(), "2");
  await changeLanguage("en");
  assert.equal(
    await page
      .getByLabel("Selection start frame", { exact: true })
      .inputValue(),
    "25",
  );
  assert.equal(
    await page
      .getByLabel("Selection end frame, exclusive", { exact: true })
      .inputValue(),
    "30",
  );
  assert.equal(
    await page.evaluate(
      () =>
        Object.entries(localStorage).find(([key]) =>
          key.startsWith("dse-batch-draft:"),
        )?.[1],
    ),
    drafts,
  );
  assert.deepEqual(snapshot(), before);
  report.frame_selection_drafts_schema_and_saved_labels_preserved = true;

  // Allow the ordinary draft autosave to settle before testing a full reload.
  let preferencesSaved = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    const current = await (await fetch(base + "/api/project")).json();
    const preferences = current.preferences;
    if (
      preferences.batchDraft?.changes.length === 2 &&
      preferences.frame === 25 &&
      preferences.interval === true &&
      preferences.range[0] === 25 &&
      preferences.range[1] === 30
    ) {
      preferencesSaved = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(
    preferencesSaved,
    "Workspace preferences must finish saving before reload",
  );
  await page.reload();
  await visible(confirm);
  assert.equal(await language.inputValue(), "en");
  await visible(
    page.getByRole("button", {
      name: "Confirm and save all 2 items",
      exact: true,
    }),
  );
  await page.waitForFunction(
    () => document.querySelector('[aria-label="Source frame"]')?.value === "25",
  );
  assert.equal(
    await page.getByLabel("Source frame", { exact: true }).inputValue(),
    "25",
  );
  report.language_and_pending_drafts_survive_reload = true;
  await page
    .getByRole("button", {
      name: "Check completion conditions for this frame",
      exact: true,
    })
    .click();
  const completion = page.getByLabel("Current-frame completion results", {
    exact: true,
  });
  await visible(completion);
  await page.waitForFunction(() =>
    document
      .querySelector(".completion-feedback")
      ?.textContent.includes("missing required field"),
  );
  assert.ok(!/\p{Script=Han}/u.test(await completion.innerText()));
  await changeLanguage("zh-CN");
  assert.match(
    await page.getByLabel("本帧完成条件检查结果", { exact: true }).innerText(),
    /缺少必填/,
  );
  await changeLanguage("en");
  report.completion_and_dynamic_backend_errors = true;

  await nav("Behavior & calibration").click();
  await visible(
    page.getByRole("heading", { name: "Calibration & assisted review" }),
  );
  const target = page.getByLabel("Target grasp part", { exact: true });
  await target.fill("用户任务 / custom target");
  await changeLanguage("zh-CN");
  assert.equal(
    await page.getByLabel("目标抓取部位", { exact: true }).inputValue(),
    "用户任务 / custom target",
  );
  await changeLanguage("en");
  await visible(
    page.getByRole("heading", { name: "adviser direction suggestions" }),
  );
  await nav("Export dataset").click();
  await visible(
    page.getByRole("heading", { name: "Export a standalone segment dataset" }),
  );
  await visible(
    page.getByRole("button", { name: "Start background export", exact: true }),
  );
  report.calibration_export_and_user_text = true;

  await nav("Export inspector").click();
  await page
    .getByLabel("Export directory to inspect", { exact: true })
    .fill(data.export);
  await page
    .getByRole("button", { name: "Open & inspect", exact: true })
    .click();
  await visible(page.getByText("File checksums verified", { exact: true }));
  await visible(
    page
      .getByText(
        "This field has valid supervision at the current training center",
        { exact: true },
      )
      .first(),
  );
  await page
    .getByRole("tab", { name: "Training input preview", exact: true })
    .click();
  await visible(
    page.getByText("History actually sampled for this training center", {
      exact: true,
    }),
  );
  const inspectorFrame = await page
    .getByLabel("Local frame index to inspect", { exact: true })
    .inputValue();
  await changeLanguage("zh-CN");
  await visible(page.getByText("这个训练中心实际采样的历史", { exact: true }));
  assert.equal(
    await page.getByLabel("检查的本片段帧号", { exact: true }).inputValue(),
    inspectorFrame,
  );
  await changeLanguage("en");
  await page.screenshot({
    path: resolve(fixture, "interface-en.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1000, height: 800 });
  await visible(language);
  const headerOverflow = await page
    .locator("header")
    .evaluate((header) =>
      [...header.children].some(
        (child) => child.getBoundingClientRect().right > innerWidth + 1,
      ),
    );
  assert.equal(
    headerOverflow,
    false,
    "Language selector/header overflows the viewport",
  );
  report.inspector_state_and_responsive_language_selector = true;

  const hashes = python(
    `
import json, sys
from dse_tool.dataset import content_fingerprint
print(json.dumps([content_fingerprint(sys.argv[1])[0], content_fingerprint(sys.argv[2])[0]]))
`,
    data.source,
    data.export,
  );
  assert.deepEqual(hashes, [data.source_hash, data.export_hash]);
  assert.deepEqual(errors, []);
  report.source_and_export_unchanged = true;
  report.browser_errors = errors;
  writeFileSync(
    resolve(fixture, "validation.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify({ ...report, artifacts: fixture }, null, 2));
} catch (error) {
  await page
    .screenshot({ path: resolve(fixture, "failure.png"), fullPage: true })
    .catch(() => {});
  console.error("Browser artifacts:", fixture);
  console.error("Preference trace:", JSON.stringify(preferenceTrace));
  throw error;
} finally {
  await browser.close();
}
