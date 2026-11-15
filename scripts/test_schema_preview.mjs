import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { build } from "../frontend/node_modules/esbuild/lib/main.js";

const compiled = await build({
  stdin: {
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import Inputs, { schemaPreviewValues } from "./src/SchemaPreviewInputs";
      export { schemaPreviewValues };
      export const render = (fields, values) => renderToStaticMarkup(
        <Inputs fields={fields} values={values} onChange={() => {}} />
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
const { render, schemaPreviewValues } = bundled.exports;
const classes = [
  { id: 7, key: "pass" },
  { id: 99, key: "unsure" },
];
const fields = [
  { field_id: "contact.mode", scope: "each_arm", classes },
  { field_id: "quality", scope: "global", classes },
];
const values = {
  left: { "contact.mode": 7, phase: 2 },
  right: { "contact.mode": 99, phase: 4 },
  global: { quality: 7 },
};
const original = JSON.stringify(values);
const html = render(fields, values);
assert.ok(
  !html.includes("phase"),
  "Generic schema must not render old phase inputs",
);
assert.equal((html.match(/<select/g) || []).length, 3);
assert.equal((html.match(/contact\.mode/g) || []).length, 2);
assert.equal((html.match(/quality/g) || []).length, 1);
assert.ok(html.includes('value="7" selected=""'));
assert.ok(html.includes('value="99" selected=""'));
assert.deepEqual(schemaPreviewValues(fields, values), {
  left: { "contact.mode": 7 },
  right: { "contact.mode": 99 },
  global: { quality: 7 },
});
assert.deepEqual(schemaPreviewValues(fields, {}), {
  left: {},
  right: {},
  global: {},
});
// Deleted categories, deleted fields and changed scopes must drop stale test values.
assert.deepEqual(
  schemaPreviewValues(
    [{ ...fields[0], classes: [{ id: 99, key: "unsure" }] }],
    values,
  ),
  { left: {}, right: { "contact.mode": 99 }, global: {} },
);
assert.deepEqual(
  schemaPreviewValues([{ ...fields[0], scope: "global" }], values),
  { left: {}, right: {}, global: {} },
);
const globalOnly = render([fields[1]], values);
assert.equal((globalOnly.match(/<select/g) || []).length, 1);
assert.ok(!globalOnly.includes("contact.mode"));
assert.ok(!globalOnly.includes("phase"));
const legacy = render([{ ...fields[0], field_id: "phase" }], {});
assert.equal((legacy.match(/phase/g) || []).length, 2);
assert.ok(!legacy.includes('value="7" selected=""'));
assert.equal(JSON.stringify(values), original);
console.log(
  "Validated schema-driven preview inputs, scopes, sparse class IDs and stale-value removal.",
);
