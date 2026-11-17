import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "../frontend/node_modules/esbuild/lib/main.js";

const frontend = fileURLToPath(new URL("../frontend/", import.meta.url));
const reactPath = fileURLToPath(
  new URL("../frontend/node_modules/react/index.js", import.meta.url),
);
const i18nPath = fileURLToPath(
  new URL("../frontend/src/i18n.ts", import.meta.url),
);

// Preserve hook state across renders while replacing effects that would perform
// HTTP requests. The actual event handler, JSX, translation catalog and server
// renderer are used; no browser or production state is involved.
const compiled = await build({
  stdin: {
    contents: `
      import { renderToStaticMarkup } from "react-dom/server";
      import Screen, { canonicalAssistMessage, eventTypeLabel } from "./src/BehaviorCalibration";
      import { setLanguage, t } from "./src/i18n";
      import { begin } from "assist-test-hooks";
      export { setLanguage, t, canonicalAssistMessage, eventTypeLabel };
      export function tree(props) { begin(); return Screen(props); }
      export const html = (tree) => renderToStaticMarkup(tree);
    `,
    resolveDir: frontend,
    loader: "tsx",
  },
  bundle: true,
  platform: "node",
  format: "cjs",
  write: false,
  loader: { ".css": "empty" },
  plugins: [
    {
      name: "isolated-assist-state",
      setup(builder) {
        builder.onResolve({ filter: /^assist-test-hooks$/ }, () => ({
          path: "hooks",
          namespace: "assist-test",
        }));
        builder.onResolve({ filter: /^react$/ }, (args) =>
          args.importer.endsWith("BehaviorCalibration.tsx")
            ? { path: "hooks", namespace: "assist-test" }
            : undefined,
        );
        builder.onResolve({ filter: /^\.\/i18n$/ }, (args) =>
          args.importer.endsWith("BehaviorCalibration.tsx")
            ? { path: "i18n", namespace: "assist-test" }
            : undefined,
        );
        builder.onLoad({ filter: /^hooks$/, namespace: "assist-test" }, () => ({
          contents: `
          import React from ${JSON.stringify(reactPath)};
          export default React;
          const cells = []; let cursor = 0;
          export const begin = () => { cursor = 0; };
          export function useState(initial) {
            const index = cursor++;
            if (!(index in cells)) cells[index] = typeof initial === "function" ? initial() : initial;
            return [cells[index], (value) => { cells[index] = typeof value === "function" ? value(cells[index]) : value; }];
          }
          export const useRef = (initial) => ({ current: initial });
          export const useEffect = () => {};
        `,
          loader: "js",
          resolveDir: frontend,
        }));
        builder.onLoad({ filter: /^i18n$/, namespace: "assist-test" }, () => ({
          contents: `export {t, categoryLabel} from ${JSON.stringify(i18nPath)}; export const useI18n=()=>({});`,
          loader: "js",
          resolveDir: frontend,
        }));
      },
    },
  ],
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(
  createRequire(import.meta.url),
  module,
  module.exports,
);
const { tree, html, setLanguage, t, canonicalAssistMessage, eventTypeLabel } =
  module.exports;
const calls = [],
  notifications = [];
const props = {
  datasetKey: "synthetic-source",
  dataset: { info: { features: {} } },
  schema: { version: 1, semantic_profile: "generic", fields: [] },
  project: { scheme: { name: "Fixture" } },
  view: {
    frame: 10,
    source_key: { frame: 110 },
    revision: 0,
    events: [
      {
        id: 7,
        frame: 115,
        arm: "left",
        type: "contact_test",
        status: "suggestion",
        details: '{"source":"human"}',
      },
    ],
  },
  episode: 0,
  frame: 10,
  range: [10, 20],
  fps: 30,
  frameCount: 60,
  api: async (path, body) => {
    calls.push({ path, body });
    return { saved: 1, skipped: 0 };
  },
  onChanged() {},
  notify(message) {
    notifications.push(message);
  },
  onError(error) {
    throw error;
  },
  onLocate() {},
  onSelectRange() {},
  taskSettings: {},
  onTaskSettingsChange() {},
  settings: {},
  onSettingsChange() {},
};
function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  return text(node.props?.children);
}
function buttons(node, result = []) {
  if (Array.isArray(node)) node.forEach((child) => buttons(child, result));
  else if (node && typeof node === "object") {
    if (node.type === "button") result.push(node);
    buttons(node.props?.children, result);
  }
  return result;
}

setLanguage("zh-CN");
let current = tree(props);
let markup = html(current);
assert(
  markup.includes("contact_test"),
  "Custom legacy event type must be preserved",
);
assert(
  markup.includes("当前片段帧 #15"),
  "Cropped events must use the converted local frame",
);
assert(
  markup.includes('aria-label="目标标签范围"'),
  "Field controls need a stable accessible name",
);
const create = buttons(current).find((button) =>
  text(button).includes("打开事件"),
);
assert(create, "Manual open-event action must exist");
await create.props.onClick();
assert.equal(calls.at(-1).path, "/events");
assert.equal(calls.at(-1).body.frame, 10);
assert.equal(
  notifications.at(-1),
  "人工事件已保存，可通过撤销恢复。",
  "Parent notifications must remain canonical",
);
assert(html(tree(props)).includes("人工事件已保存，可通过撤销恢复。"));

setLanguage("en");
markup = html(tree(props));
assert(
  markup.includes("Manual event saved. Undo is available."),
  "A saved Chinese success state must rerender in English",
);
assert(!markup.includes("人工事件已保存"));
assert(
  markup.includes("contact_test"),
  "English switching must preserve custom event data",
);
assert(markup.includes('aria-label="Target label scope"'));
assert.equal(
  eventTypeLabel("设备复位"),
  "设备复位",
  "Custom Chinese event data must not be translated",
);
assert.equal(eventTypeLabel("open"), "Open");
assert.equal(eventTypeLabel("closed"), "Closed");

const stateMessage = canonicalAssistMessage(
  "已保存 {0} 项草稿，跳过 {1} 项；仍需人工审核。",
  [3, 2],
);
assert.equal(
  t(stateMessage),
  "Saved 3 draft entries; skipped 2. Human review is still required.",
);
setLanguage("zh-CN");
assert.equal(t(stateMessage), "已保存 3 项草稿，跳过 2 项；仍需人工审核。");
assert(
  html(tree(props)).includes("人工事件已保存，可通过撤销恢复。"),
  "Switching back must retain canonical success state",
);
console.log(
  "Validated behavior calibration custom event types, cropped source frames, accessible fields, real save callback and persistent bilingual success states.",
);
