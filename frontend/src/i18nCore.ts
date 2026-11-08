import english from "./locales/en.json";

export type Language = "zh-CN" | "en";
export type MessageValues = readonly unknown[];
const catalog: Record<string, string> = english;
const han = /\p{Script=Han}/u;
const placeholder = /\{(\d+)\}/g;
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Match complete application messages, never replace words inside user data.
// Capture values (paths, IDs, etc.) are retained verbatim.
const templates = Object.keys(catalog)
  .filter((key) => /\{\d+\}/.test(key))
  .sort(
    (a, b) =>
      b.replace(placeholder, "").length - a.replace(placeholder, "").length,
  )
  .map((key) => {
    const indices: number[] = [];
    let cursor = 0;
    let pattern = "^";
    for (const match of key.matchAll(/\{(\d+)\}/g)) {
      pattern += escape(key.slice(cursor, match.index)) + "([\\s\\S]*?)";
      indices.push(Number(match[1]));
      cursor = match.index! + match[0].length;
    }
    pattern += escape(key.slice(cursor)) + "$";
    return { key, indices, pattern: new RegExp(pattern) };
  });

// Only these captures contain other application messages rather than raw data.
const nested: Record<string, number[]> = {
  "不能删除 {0}: {1}": [1],
  "不能删除类别: {0}": [0],
  "原类别 {0} 已从当前版本删除；旧值保留在历史中，需重新标注{1}": [1],
  "{0}.{1} 引用 {2}{3}": [3],
  "系统选择器暂不可用，可在这里浏览文件夹。{0}": [0],
};

function format(message: string, values: MessageValues) {
  return message.replace(placeholder, (_, index: string) =>
    String(values[Number(index)]),
  );
}

export function translate(
  message: string | null | undefined,
  language: Language,
  values?: MessageValues,
): string {
  const source = message ?? "";
  const known = Object.hasOwn(catalog, source);
  if (values)
    return format(
      language === "en" && known ? catalog[source] : source,
      values,
    );
  if (language !== "en" || !han.test(source)) return source;
  if (known) return catalog[source];

  // A review can report several independently generated errors at once.
  if (source.includes("；")) {
    const pieces = source.split("；");
    const translated = pieces.map((piece) => translate(piece, language));
    if (
      translated.every(
        (piece, i) => piece !== pieces[i] || !han.test(pieces[i]),
      )
    )
      return translated.join("; ");
  }
  for (const template of templates) {
    const match = template.pattern.exec(source);
    if (!match) continue;
    const args: string[] = [];
    template.indices.forEach((index, i) => {
      args[index] = match[i + 1];
    });
    for (const index of nested[template.key] ?? []) {
      const value = args[index];
      args[index] = value.startsWith("；")
        ? "; " + translate(value.slice(1), language)
        : translate(value, language);
    }
    return format(catalog[template.key], args);
  }
  return source;
}

export function categoryLabel(
  category: Record<string, any>,
  language: Language,
) {
  if (language === "zh-CN") return category.label_zh || category.key || "";
  if (category.label_en) return category.label_en;
  const source = category.label_zh || "";
  // Translate unchanged built-in category labels only. A custom label that
  // happens to match a UI phrase must still fall back to its own stable key.
  const builtIn: Record<string, string> = {
    idle: "空闲",
    approach: "接近",
    grasp_align: "抓取对准",
    grasp_check: "抓持检查",
    transport: "搬运",
    place_align: "放置对准",
    withdraw: "撤离",
    recover: "恢复重试",
    unknown: "无法判断",
    empty: "空手",
    holding: "持物",
    not_ready: "条件不具备",
    ready: "条件具备",
    none: "无显著位移",
    positive: "正方向",
    negative: "负方向",
    keep: "无显著调整",
    adjust: "调整姿态",
  };
  return source === builtIn[category.key]
    ? translate(source, language)
    : category.key || source;
}
