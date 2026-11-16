import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { build } from "../frontend/node_modules/esbuild/lib/main.js";

const compiled = await build({
  stdin: {
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import Picker, { keyClassSelection } from "./src/KeyClassPicker";
      export { keyClassSelection };
      export const render = (fields, value) => renderToStaticMarkup(
        <Picker fields={fields} value={value} onChange={() => {}} />
      );
    `,
    loader: "tsx",
    resolveDir: fileURLToPath(new URL("../frontend/", import.meta.url)),
  },
  bundle: true,
  platform: "node",
  format: "cjs",
  write: false,
});
const bundled = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(
  createRequire(import.meta.url),
  bundled,
  bundled.exports,
);
const { render, keyClassSelection } = bundled.exports;
const classes = [
  { id: 7, key: "pass" },
  { id: 99, key: "unsure" },
];
const fields = [
  { field_id: "contact.mode", scope: "each_arm", classes },
  { field_id: "quality", scope: "global", classes },
];
const defaults = [{ arm: "left", field_id: "contact.mode", class_ids: [7] }];
assert.deepEqual(keyClassSelection(fields, null, defaults), defaults);
assert.deepEqual(keyClassSelection(fields, [], defaults), []);
const saved = [
  ...defaults,
  { arm: "right", field_id: "contact.mode", class_ids: [99, 123] },
  { arm: "left", field_id: "quality", class_ids: [7] },
  { arm: "global", field_id: "quality", class_ids: [7, 99] },
  { arm: "left", field_id: "deleted", class_ids: [7] },
];
const before = JSON.stringify(saved);
const selected = keyClassSelection(fields, saved);
assert.deepEqual(selected, [
  ...defaults,
  { arm: "right", field_id: "contact.mode", class_ids: [99] },
  { arm: "global", field_id: "quality", class_ids: [7, 99] },
]);
const html = render(fields, selected);
assert.equal((html.match(/type="checkbox"/g) || []).length, 6);
assert.equal((html.match(/checked=""/g) || []).length, 4);
assert.ok(html.includes('aria-label="global.quality: 7 · pass"'));
assert.ok(!html.includes("phase"));
assert.deepEqual(
  keyClassSelection([{ ...fields[0], scope: "global" }], saved),
  [],
);
assert.equal(JSON.stringify(saved), before);
console.log(
  "Validated key category defaults, explicit clearing, scopes, stable IDs and schema edits.",
);
