export const orientationField = "adviser.orientation";
export const rotationAxes = ["X", "Y", "Z"] as const;
export const rotationFieldIds = rotationAxes.map(
  (axis) => `adviser.rot_${axis.toLowerCase()}`,
);
export const adviserFieldIds = [
  "adviser.x",
  "adviser.y",
  "adviser.z",
  orientationField,
  ...rotationFieldIds,
];

const rotationClasses = [
  {
    key: "none",
    label_zh: "无显著转动",
    label_en: "No significant rotation",
    color: "#718096",
  },
  {
    key: "positive",
    label_zh: "右手正转",
    label_en: "Positive rotation",
    color: "#60a5fa",
  },
  {
    key: "negative",
    label_zh: "右手负转",
    label_en: "Negative rotation",
    color: "#34d399",
  },
  {
    key: "unknown",
    label_zh: "无法判断",
    label_en: "Unknown",
    color: "#94a3b8",
  },
];

export function rotationClass(fieldId: string, semantic: string) {
  if (rotationFieldIds.includes(fieldId))
    return rotationClasses.find((category) => category.key === semantic);
}

type SchemaField = {
  field_id: string;
  scope: string;
  evidence?: string;
  applicable_when: unknown;
};

export function missingRotationFields(
  fields: SchemaField[],
  retiredFields: Record<string, unknown> = {},
) {
  const orientation = fields.find(
    (field) =>
      field.field_id === orientationField &&
      field.scope === "each_arm" &&
      field.evidence === "behavior",
  );
  const currentIds = new Set(fields.map((field) => field.field_id));
  return rotationFieldIds
    .map((field_id, i) => ({
      field_id,
      label: `绕基座 ${rotationAxes[i]} 轴转动`,
      label_en: `Rotation about base ${rotationAxes[i]}`,
      description:
        "已认可示教短窗口内，净旋转向量在机器人基座该轴上的分量。正方向遵循右手定则。",
      description_en:
        "Component of the net rotation vector along this robot-base axis in the approved demonstration window. Positive follows the right-hand rule.",
      scope: "each_arm",
      type: "categorical",
      evidence: "behavior",
      unknown_id: 3,
      applicable_when: structuredClone(orientation?.applicable_when ?? true),
      required_when: false,
      classes: rotationClasses.map((category, id) => ({
        ...category,
        id,
        shortcut: "",
      })),
    }))
    .filter(
      ({ field_id }) =>
        !currentIds.has(field_id) && !Object.hasOwn(retiredFields, field_id),
    );
}
