"""Render C-text from current, reviewed annotations."""

import copy

from .schema import digest


C_TEXT_FEATURE = "c_text"
SCOPES = (("left", "Left arm"), ("right", "Right arm"), ("global", "Global"))


def template_skeleton(schema):
    return {
        "schema_hash": digest(schema),
        "template_version": "my-scheme-v1",
        "fields": {
            field["field_id"]: {
                cls["key"]: f"{field['field_id']}={cls['key']}"
                for cls in field["classes"]
                if cls["id"] != field["unknown_id"]
            }
            for field in schema["fields"]
        },
    }


class CTextRenderer:
    def __init__(self, schema, template=None):
        default = template_skeleton(schema)
        if template is None:
            template = {**default, "template_version": "schema-c-text-v1"}
        else:
            self.validate_template(template, default)
        self.template = copy.deepcopy(template)
        self.phrases = {scope: {} for scope, _ in SCOPES}
        for field in schema["fields"]:
            field_id = field["field_id"]
            mapping = self.template["fields"][field_id]
            phrases = {
                cls["id"]: mapping[cls["key"]]
                for cls in field["classes"]
                if cls["id"] != field["unknown_id"]
            }
            scopes = ("global",) if field["scope"] == "global" else ("left", "right")
            for scope in scopes:
                self.phrases[scope][field_id] = phrases

    @staticmethod
    def validate_template(template, expected):
        if not isinstance(template, dict) or template.keys() != expected.keys():
            raise ValueError("C-text 模板需要 schema_hash、template_version 和 fields")
        if template["schema_hash"] != expected["schema_hash"]:
            raise ValueError("C-text 模板的 schema_hash 与当前标注方案不匹配")
        version = template["template_version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("C-text 模板需要非空的 template_version")
        fields = template["fields"]
        if not isinstance(fields, dict) or fields.keys() != expected["fields"].keys():
            raise ValueError("C-text 模板必须覆盖当前方案的全部字段")
        for field_id, classes in expected["fields"].items():
            mapping = fields[field_id]
            if not isinstance(mapping, dict) or mapping.keys() != classes.keys():
                raise ValueError(
                    f"C-text 模板必须覆盖 {field_id} 的全部非 unknown 类别"
                )
            if any(not isinstance(phrase, str) for phrase in mapping.values()):
                raise ValueError(f"C-text 模板中 {field_id} 的文案必须为字符串")

    def render(self, base_task, labels, center):
        sentences = []
        if center:
            for scope, heading in SCOPES:
                phrases = []
                for field_id, mapping in self.phrases[scope].items():
                    record = labels[scope][field_id]
                    phrase = mapping.get(record["value_id"])
                    if record["effective_loss_mask"] and phrase:
                        phrases.append(phrase)
                if phrases:
                    sentences.append(heading + ": " + "; ".join(phrases) + ".")
        suffix = " ".join(sentences)
        fallback = "no_renderable_labels" if center else "context_only"
        return {
            "base_task": base_task,
            "dse_suffix": suffix,
            "combined_prompt": base_task + ("\n" + suffix if suffix else ""),
            "fallback_reason": None if suffix else fallback,
            "template_version": self.template["template_version"],
        }
