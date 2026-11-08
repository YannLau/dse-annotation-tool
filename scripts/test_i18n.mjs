import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "../frontend/node_modules/esbuild/lib/main.js";

const catalog = JSON.parse(
  readFileSync(new URL("../frontend/src/locales/en.json", import.meta.url)),
);
const compiled = await build({
  entryPoints: [
    fileURLToPath(new URL("../frontend/src/i18nCore.ts", import.meta.url)),
  ],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const { translate, categoryLabel } = await import(
  "data:text/javascript;base64," +
    Buffer.from(compiled.outputFiles[0].text).toString("base64")
);

const parameters = (text) =>
  [...new Set([...text.matchAll(/\{(\d+)\}/g)].map((m) => m[1]))].sort();
for (const [source, target] of Object.entries(catalog)) {
  assert.ok(target.trim(), `Empty translation: ${source}`);
  assert.deepEqual(
    parameters(target),
    parameters(source),
    `Changed interpolation: ${source}`,
  );
  assert.ok(
    !/\p{Script=Han}/u.test(target),
    `Untranslated catalog entry: ${source}`,
  );
  assert.equal(translate(source, "zh-CN"), source);
  assert.equal(translate(source, "en"), target);
}

assert.equal(
  translate("已导入 {0} 个 episode；{1} 项物理类型差异已记录", "en", [2, 7]),
  "Imported 2 episodes; recorded 7 physical-type differences",
);
assert.equal(
  translate("已导入 2 个 episode；7 项物理类型差异已记录", "en"),
  "Imported 2 episodes; recorded 7 physical-type differences",
);
assert.equal(
  translate("left: 缺少必填 phase；right: 缺少必填 holding", "en"),
  "left: missing required field phase; right: missing required field holding",
);
assert.equal(
  translate(
    "不能删除 phase: phase 是帧游标、双臂阶段与导出的基础字段，不能删除",
    "en",
  ),
  "Cannot delete phase: phase is foundational to frame navigation, arm phases, and export, and cannot be deleted",
);
assert.equal(
  translate("holding.applicable_when 引用 self.phase 的类别 2", "en"),
  "holding.applicable_when references self.phase category 2",
);

// Paths, custom labels, IDs, braces and replacement tokens are data, not phrases.
const path = "/tmp/中文 folder/字段 7/$&/{0}";
assert.equal(translate(`文件不存在: ${path}`, "en"), `File not found: ${path}`);
assert.equal(translate(path, "en"), path);
assert.equal(
  translate("文件不存在: {0}", "en", [path]),
  `File not found: ${path}`,
);
assert.equal(translate("用户自定义任务内容", "en"), "用户自定义任务内容");
assert.equal(translate(undefined, "en"), "");

const category = { id: 8, key: "unknown", label_zh: "无法判断" };
const before = JSON.stringify(category);
assert.equal(categoryLabel(category, "en"), "Unknown");
assert.equal(categoryLabel(category, "zh-CN"), "无法判断");
assert.equal(JSON.stringify(category), before);
assert.equal(
  categoryLabel(
    { key: "custom", label_zh: "自定义内容", label_en: "My label" },
    "en",
  ),
  "My label",
);
assert.equal(
  categoryLabel({ key: "custom", label_zh: "自定义内容" }, "en"),
  "custom",
);
assert.equal(
  categoryLabel({ key: "custom", label_zh: "空闲" }, "en"),
  "custom",
);
console.log(
  `Validated ${Object.keys(catalog).length} bilingual messages, dynamic errors, interpolation, and data preservation.`,
);
