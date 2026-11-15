import { categoryLabel, t as tr, useI18n } from "./i18n";

type PreviewField = {
  field_id: string;
  scope: string;
  classes: { id: number; key: string; [key: string]: any }[];
};
export type PreviewValues = Record<string, Record<string, number>>;

// 草稿可删除字段、类别或改变作用域；预览请求只携带当前草稿仍然有效的测试值。
export function schemaPreviewValues(
  fields: PreviewField[],
  values: PreviewValues,
): PreviewValues {
  return Object.fromEntries(
    ["left", "right", "global"].map((arm) => [
      arm,
      Object.fromEntries(
        fields
          .filter(
            (field) =>
              (field.scope === "global") === (arm === "global") &&
              field.classes.some((c) => c.id === values[arm]?.[field.field_id]),
          )
          .map((field) => [field.field_id, values[arm][field.field_id]]),
      ),
    ]),
  );
}

export default function SchemaPreviewInputs({
  fields,
  values,
  onChange,
}: {
  fields: PreviewField[];
  values: PreviewValues;
  onChange: (values: PreviewValues) => void;
}) {
  useI18n();
  const current = schemaPreviewValues(fields, values);
  return (
    <details className="schema-preview-inputs">
      <summary>{tr("规则测试值")}</summary>
      <p>
        {tr("选择示例类别，预览适用与必填条件。测试值仅用于本次规则预览。")}
      </p>
      <div className="grid2">
        {["left", "right", "global"].flatMap((arm) =>
          fields
            .filter(
              (field) => (field.scope === "global") === (arm === "global"),
            )
            .map((field) => (
              <label key={`${arm}/${field.field_id}`}>
                {tr(arm === "left" ? "左" : arm === "right" ? "右" : "全局")} ·{" "}
                {field.field_id}
                <select
                  value={current[arm][field.field_id] ?? ""}
                  onChange={(event) => {
                    const next = { ...current[arm] };
                    if (event.target.value === "") delete next[field.field_id];
                    else next[field.field_id] = Number(event.target.value);
                    onChange({ ...current, [arm]: next });
                  }}
                >
                  <option value="">{tr("未选择测试值")}</option>
                  {field.classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.id} · {c.key} — {categoryLabel(c)}
                    </option>
                  ))}
                </select>
              </label>
            )),
        )}
      </div>
    </details>
  );
}
