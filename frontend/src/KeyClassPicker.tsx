import React from "react";
import { t as tr, useI18n, categoryLabel } from "./i18n";

type Field = {
  field_id: string;
  label?: string;
  scope: string;
  classes: { id: number; key: string; label_zh?: string; label_en?: string }[];
};
export type KeyClassRule = {
  arm: string;
  field_id: string;
  class_ids: number[];
};

// Preferences belong to the current scheme. Remove deleted fields/classes and old scopes
// before rendering/sending them; null means schema defaults, [] means event-only sampling.
export function keyClassSelection(
  fields: Field[],
  saved: KeyClassRule[] | null,
  defaults: KeyClassRule[] = [],
): KeyClassRule[] {
  return fields.flatMap((field) =>
    (field.scope === "global" ? ["global"] : ["left", "right"]).flatMap(
      (arm) => {
        const selected = (saved ?? defaults).filter(
          (r) =>
            r &&
            Array.isArray(r.class_ids) &&
            r.arm === arm &&
            r.field_id === field.field_id,
        );
        const ids = field.classes
          .filter((c) => selected.some((r) => r.class_ids.includes(c.id)))
          .map((c) => c.id);
        return ids.length
          ? [{ arm, field_id: field.field_id, class_ids: ids }]
          : [];
      },
    ),
  );
}

export default function KeyClassPicker({
  fields,
  value,
  onChange,
}: {
  fields: Field[];
  value: KeyClassRule[];
  onChange: (value: KeyClassRule[]) => void;
}) {
  useI18n();
  return (
    <div className="key-class-picker">
      {fields.map((field) => (
        <details key={field.field_id}>
          <summary>
            {field.label || field.field_id} · {field.field_id}
            {" — "}
            {tr("已选 {0} 项", [
              value
                .filter((r) => r.field_id === field.field_id)
                .reduce((n, r) => n + r.class_ids.length, 0),
            ])}
          </summary>
          <div className="grid2">
            {(field.scope === "global" ? ["global"] : ["left", "right"]).map(
              (arm) => (
                <fieldset key={arm}>
                  <legend>
                    {tr(
                      arm === "global"
                        ? "全局"
                        : arm === "left"
                          ? "左臂"
                          : "右臂",
                    )}
                  </legend>
                  {field.classes.map((category) => {
                    const rule = value.find(
                      (r) => r.arm === arm && r.field_id === field.field_id,
                    );
                    const selected = rule?.class_ids || [];
                    return (
                      <label key={category.id}>
                        <input
                          type="checkbox"
                          aria-label={`${arm}.${field.field_id}: ${category.id} · ${category.key}`}
                          checked={selected.includes(category.id)}
                          onChange={(e) => {
                            const ids = e.target.checked
                              ? [...selected, category.id]
                              : selected.filter((id) => id !== category.id);
                            onChange([
                              ...value.filter((r) => r !== rule),
                              ...(ids.length
                                ? [
                                    {
                                      arm,
                                      field_id: field.field_id,
                                      class_ids: ids,
                                    },
                                  ]
                                : []),
                            ]);
                          }}
                        />
                        {category.id} · {category.key} —{" "}
                        {categoryLabel(category)}
                      </label>
                    );
                  })}
                </fieldset>
              ),
            )}
          </div>
        </details>
      ))}
    </div>
  );
}
