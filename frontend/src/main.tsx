import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import "./style.css";
import ExportInspector from "./ExportInspector";
type Obj = Record<string, any>;
async function api(path: string, body?: any, signal?: AbortSignal) {
  const response = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const value = await response.json();
  if (!response.ok)
    throw Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail),
    );
  return value;
}
const names: Obj = {
  "observation.images.cam_high": "顶视",
  "observation.images.cam_left_wrist": "左腕",
  "observation.images.cam_right_wrist": "右腕",
};
const featureNames = (f: Obj): string[] =>
  Array.isArray(f?.names)
    ? f.names
    : (Object.values(f?.names || {}).flat() as string[]);
const defaultTracks = (info: Obj) =>
  featureNames(info.features["observation.state"])
    .flatMap((name, i) =>
      /gripper/i.test(name) ? ["observation.state:" + i] : [],
    )
    .slice(0, 2);
const display = (c: Obj) => `${c.id} · ${c.key} — ${c.label_zh}`;
function RuleEditor({
  rule,
  onChange,
  fields,
}: {
  rule: any;
  onChange: (v: any) => void;
  fields: Obj[];
}) {
  const kind =
    typeof rule === "boolean"
      ? rule
        ? "true"
        : "false"
      : rule.field
        ? "condition"
        : rule.all
          ? "all"
          : rule.any
            ? "any"
            : "not";
  const refs = fields.flatMap((f) =>
    (f.scope === "global" ? ["global"] : ["self", "left", "right"]).map(
      (a) => a + "." + f.field_id,
    ),
  );
  const field =
    fields.find(
      (f) => f.field_id === (rule?.field || "").split(".").slice(1).join("."),
    ) || fields[0];
  return (
    <div className="rule">
      <select
        aria-label="条件类型"
        value={kind}
        onChange={(e) => {
          const k = e.target.value;
          onChange(
            k === "true"
              ? true
              : k === "false"
                ? false
                : k === "condition"
                  ? { field: refs[0], op: "eq", value: fields[0].classes[0].id }
                  : k === "not"
                    ? { not: true }
                    : { [k]: [true] },
          );
        }}
      >
        {["true", "false", "condition", "all", "any", "not"].map((k) => (
          <option key={k}>{k}</option>
        ))}
      </select>
      {kind === "condition" && (
        <>
          <select
            aria-label="引用字段"
            value={rule.field}
            onChange={(e) => {
              const f = fields.find(
                (f) =>
                  f.field_id === e.target.value.split(".").slice(1).join("."),
              )!;
              onChange({
                ...rule,
                field: e.target.value,
                value: f.classes[0].id,
                values: [f.classes[0].id],
              });
            }}
          >
            {refs.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <select
            value={rule.op}
            onChange={(e) =>
              onChange({
                ...rule,
                op: e.target.value,
                value: field.classes[0].id,
                values: [field.classes[0].id],
              })
            }
          >
            {["eq", "ne", "in"].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          {rule.op === "in" ? (
            <div>
              {field.classes.map((c: Obj) => (
                <label key={c.id}>
                  <input
                    type="checkbox"
                    checked={rule.values?.includes(c.id) || false}
                    onChange={(e) =>
                      onChange({
                        ...rule,
                        values: e.target.checked
                          ? [...(rule.values || []), c.id]
                          : (rule.values || []).filter(
                              (v: number) => v !== c.id,
                            ),
                      })
                    }
                  />
                  {display(c)}
                </label>
              ))}
            </div>
          ) : (
            <select
              value={rule.value}
              onChange={(e) => onChange({ ...rule, value: +e.target.value })}
            >
              {field.classes.map((c: Obj) => (
                <option key={c.id} value={c.id}>
                  {display(c)}
                </option>
              ))}
            </select>
          )}
        </>
      )}
      {(kind === "all" || kind === "any") && (
        <div>
          {rule[kind].map((child: any, i: number) => (
            <div key={i}>
              <RuleEditor
                rule={child}
                fields={fields}
                onChange={(v) =>
                  onChange({
                    [kind]: rule[kind].map((x: any, j: number) =>
                      i === j ? v : x,
                    ),
                  })
                }
              />
              <button
                onClick={() =>
                  onChange({
                    [kind]: rule[kind].filter((_: any, j: number) => i !== j),
                  })
                }
              >
                移除条件
              </button>
            </div>
          ))}
          <button onClick={() => onChange({ [kind]: [...rule[kind], true] })}>
            ＋条件
          </button>
        </div>
      )}
      {kind === "not" && (
        <RuleEditor
          rule={rule.not}
          fields={fields}
          onChange={(v) => onChange({ not: v })}
        />
      )}
    </div>
  );
}
function SchemaEditor({
  schema,
  onPublish,
  notify,
  workspace,
}: {
  schema: Obj;
  onPublish: () => void;
  notify: (s: string) => void;
  workspace: string;
}) {
  const [draft, setDraft] = useState<Obj>(structuredClone(schema)),
    [index, setIndex] = useState(0),
    [preview, setPreview] = useState<Obj | null>(null),
    [newKey, setNewKey] = useState(""),
    [lp, setLp] = useState(2),
    [rp, setRp] = useState(4),
    [deletion, setDeletion] = useState<Obj | null>(null),
    [deletionHistory, setDeletionHistory] = useState<Obj[]>([]);
  useEffect(() => {
    const key = "dse-schema-draft:" + workspace + ":" + schema.version;
    const saved = localStorage.getItem(key);
    if (saved) {
      try {
        setDraft(JSON.parse(saved));
      } catch {
        localStorage.removeItem(key);
      }
    }
  }, [schema.version]);
  useEffect(() => {
    localStorage.setItem(
      "dse-schema-draft:" + workspace + ":" + schema.version,
      JSON.stringify(draft),
    );
    api("/schema/draft", { base_version: schema.version, schema: draft }).catch(
      (e) => notify(e.message),
    );
  }, [draft]);
  const field = draft.fields[index];
  const update = (patch: Obj) => {
    setDraft({
      ...draft,
      fields: draft.fields.map((f: Obj, i: number) =>
        i === index ? { ...f, ...patch } : f,
      ),
    });
    setPreview(null);
  };
  async function check() {
    try {
      setPreview(
        await api("/schema/preview", { schema: draft, left: lp, right: rp }),
      );
    } catch (e: any) {
      notify(e.message);
    }
  }
  async function previewDeletion(fieldId: string, classId?: number) {
    try {
      const plan = await api("/schema/delete-preview", {
        schema: draft,
        field_id: fieldId,
        class_id: classId,
      });
      setDeletion({
        ...plan,
        base: JSON.stringify(draft),
        label:
          classId === undefined
            ? `字段 ${fieldId}`
            : `${fieldId} 的类别 ${classId}`,
      });
    } catch (e: any) {
      notify(e.message);
    }
  }
  function applyDeletion() {
    if (!deletion?.allowed) return;
    if (deletion.base !== JSON.stringify(draft)) {
      setDeletion(null);
      notify("草稿已变化，请重新检查删除依赖");
      return;
    }
    setDeletionHistory((history) => [
      ...history,
      {
        schema: structuredClone(draft),
        index,
        field_id: deletion.field_id,
        class_id: deletion.class_id,
      },
    ]);
    setDraft(deletion.schema_after);
    setIndex(Math.min(index, deletion.schema_after.fields.length - 1));
    setPreview(null);
    setDeletion(null);
    notify("已从草稿删除；发布后生效。历史标签与原 ID 保留且不复用。");
  }
  function undoDeletion() {
    const last = deletionHistory[deletionHistory.length - 1];
    if (!last) return;
    const next = structuredClone(draft),
      original = last.schema.fields.find(
        (f: Obj) => f.field_id === last.field_id,
      );
    if (last.class_id === null || last.class_id === undefined) {
      if (next.fields.some((f: Obj) => f.field_id === last.field_id))
        return notify("字段 key 已被草稿占用，请先修复");
      next.fields.splice(
        Math.min(last.index, next.fields.length),
        0,
        structuredClone(original),
      );
      if (next.retired_fields) delete next.retired_fields[last.field_id];
    } else {
      const field = next.fields.find((f: Obj) => f.field_id === last.field_id),
        category = original.classes.find((c: Obj) => c.id === last.class_id);
      if (!field) return notify("字段已不存在，请先恢复字段");
      if (
        field.classes.some(
          (c: Obj) => c.id === category.id || c.key === category.key,
        )
      )
        return notify("类别 ID/key 已被草稿占用，请先修复");
      const index = original.classes.findIndex(
        (c: Obj) => c.id === last.class_id,
      );
      field.classes.splice(
        Math.min(index, field.classes.length),
        0,
        structuredClone(category),
      );
      if (next.retired_classes?.[last.field_id])
        next.retired_classes[last.field_id] = next.retired_classes[
          last.field_id
        ].filter((c: Obj) => c.id !== last.class_id);
    }
    setDraft(next);
    setIndex(next.fields.findIndex((f: Obj) => f.field_id === last.field_id));
    setDeletionHistory((h) => h.slice(0, -1));
    setPreview(null);
  }
  return (
    <section className="schema-layout">
      <aside>
        <h2>字段与规则</h2>
        {draft.fields.map((f: Obj, i: number) => (
          <button
            className={i === index ? "active" : ""}
            key={f.field_id}
            onClick={() => setIndex(i)}
          >
            {f.field_id}
          </button>
        ))}
        <input
          placeholder="新字段稳定 key"
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
        />
        <button
          onClick={() => {
            if (
              !newKey ||
              draft.fields.some((f: Obj) => f.field_id === newKey) ||
              draft.retired_fields?.[newKey]
            )
              return notify("请输入唯一且未删除过的字段 key");
            setDraft({
              ...draft,
              fields: [
                ...draft.fields,
                {
                  field_id: newKey,
                  label: newKey,
                  description: "",
                  scope: "each_arm",
                  type: "categorical",
                  unknown_id: 1,
                  evidence: "state",
                  applicable_when: true,
                  required_when: false,
                  classes: [
                    {
                      id: 0,
                      key: "value",
                      label_zh: "自定义状态",
                      color: "#34d399",
                      shortcut: "",
                    },
                    {
                      id: 1,
                      key: "unknown",
                      label_zh: "无法判断",
                      color: "#94a3b8",
                      shortcut: "",
                    },
                  ],
                },
              ],
            });
            setIndex(draft.fields.length);
            setNewKey("");
            setPreview(null);
          }}
        >
          ＋新增字段
        </button>
      </aside>
      <article>
        <div className="schema-field-heading">
          <h2>{field.field_id}</h2>
          <button
            className="danger"
            onClick={() => previewDeletion(field.field_id)}
          >
            删除字段
          </button>
          {deletionHistory.length > 0 && (
            <button onClick={undoDeletion}>撤销本次删除</button>
          )}
        </div>
        <p className="footnote">
          删除前检查规则引用与 unknown 关联；已发布 ID
          不回收，旧版本和历史标注保留。
        </p>
        <div className="grid2">
          <label>
            显示名
            <input
              value={field.label}
              onChange={(e) => update({ label: e.target.value })}
            />
          </label>
          <label>
            作用域
            <select
              value={field.scope}
              onChange={(e) => update({ scope: e.target.value })}
            >
              <option value="each_arm">左右臂各自</option>
              <option value="global">全局</option>
            </select>
          </label>
          <label>
            证据模式
            <select
              value={field.evidence || "state"}
              onChange={(e) => update({ evidence: e.target.value })}
            >
              <option value="state">状态观测</option>
              <option value="behavior">行为复核</option>
            </select>
          </label>
          <label>
            unknown ID
            <select
              value={field.unknown_id}
              onChange={(e) => update({ unknown_id: +e.target.value })}
            >
              {field.classes.map((c: Obj) => (
                <option key={c.id} value={c.id}>
                  {display(c)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          说明
          <textarea
            value={field.description}
            onChange={(e) => update({ description: e.target.value })}
          />
        </label>
        <h3>稳定类别 · 拖动或箭头调整显示顺序</h3>
        {field.classes.map((c: Obj, i: number) => (
          <div
            className="class-row"
            key={i}
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", String(i))}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              const cs = [...field.classes];
              const [moved] = cs.splice(
                +e.dataTransfer.getData("text/plain"),
                1,
              );
              cs.splice(i, 0, moved);
              update({ classes: cs });
            }}
          >
            {["id", "key", "label_zh", "color", "shortcut"].map((k) => (
              <input
                aria-label={k}
                title={k}
                key={k}
                type={k === "color" ? "color" : k === "id" ? "number" : "text"}
                value={c[k] ?? ""}
                onChange={(e) =>
                  update({
                    classes: field.classes.map((x: Obj, j: number) =>
                      j === i
                        ? {
                            ...x,
                            [k]: k === "id" ? +e.target.value : e.target.value,
                          }
                        : x,
                    ),
                  })
                }
              />
            ))}
            <label>
              <input
                type="checkbox"
                checked={c.deprecated || false}
                onChange={(e) =>
                  update({
                    classes: field.classes.map((x: Obj, j: number) =>
                      j === i ? { ...x, deprecated: e.target.checked } : x,
                    ),
                  })
                }
              />
              废弃
            </label>
            <button
              onClick={() => {
                if (i === 0) return;
                const cs = [...field.classes];
                [cs[i], cs[i - 1]] = [cs[i - 1], cs[i]];
                update({ classes: cs });
              }}
            >
              ↑
            </button>
            <button
              className="danger"
              aria-label={`删除类别 ${field.field_id} ${c.id}`}
              onClick={() => previewDeletion(field.field_id, c.id)}
            >
              删除
            </button>
          </div>
        ))}
        <button
          onClick={() => {
            const reserved = draft.retired_classes?.[field.field_id] || [];
            const id =
              Math.max(
                -1,
                ...[...field.classes, ...reserved].map((c: Obj) => c.id),
              ) + 1;
            let key = "new_" + id;
            const keys = new Set(
              [...field.classes, ...reserved].map((c: Obj) => c.key),
            );
            while (keys.has(key)) key += "_new";
            update({
              classes: [
                ...field.classes,
                { id, key, label_zh: "新状态", color: "#60a5fa", shortcut: "" },
              ],
            });
          }}
        >
          ＋类别
        </button>
        <h3>适用条件 applicable_when</h3>
        <RuleEditor
          rule={field.applicable_when}
          fields={draft.fields}
          onChange={(v) => update({ applicable_when: v })}
        />
        <h3>必填条件 required_when</h3>
        <RuleEditor
          rule={field.required_when}
          fields={draft.fields}
          onChange={(v) => update({ required_when: v })}
        />
        <div className="toolbar">
          <label>
            左 phase
            <input
              type="number"
              value={lp}
              onChange={(e) => setLp(+e.target.value)}
            />
          </label>
          <label>
            右 phase
            <input
              type="number"
              value={rp}
              onChange={(e) => setRp(+e.target.value)}
            />
          </label>
          <button onClick={check}>校验与迁移预览</button>
          <button
            className="primary"
            disabled={!preview}
            onClick={async () => {
              try {
                await api("/schema", draft);
                onPublish();
                notify("新版本已发布，历史值保留并标记待复核");
              } catch (e: any) {
                notify(e.message);
              }
            }}
          >
            发布不可变版本
          </button>
        </div>
        {preview && (
          <>
            <p>
              变更字段：{preview.migration.changed_fields.join(", ")}；
              {preview.migration.invalidated_labels} 条标签需复核。
            </p>
            <div className="grid2">
              {["left", "right"].map((arm) => (
                <div key={arm}>
                  <h3>{arm}</h3>
                  {Object.entries(preview.labels[arm]).map(
                    ([key, r]: [string, any]) => (
                      <p key={key}>
                        {key} · {r.applicability} ·{" "}
                        {r.required === true
                          ? "必填"
                          : r.required === null
                            ? "必填未知"
                            : "选填"}
                      </p>
                    ),
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </article>
      {deletion && (
        <div className="schema-delete-overlay">
          <div
            className="schema-delete-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="删除依赖检查"
          >
            <h2>
              {deletion.allowed ? "确认从草稿删除" : "无法删除"} ·{" "}
              {deletion.label}
            </h2>
            {deletion.allowed ? (
              <>
                <p>
                  将从待发布版本移出。现有{" "}
                  {deletion.historical_annotation_records}{" "}
                  条相关标注记录及旧版本仍保留；原字段 key / 类别 ID
                  不会分配给其他含义。
                </p>
                <p>点击后可继续编辑，发布新版本才会生效。</p>
              </>
            ) : (
              <>
                <p>请先解除以下依赖，再重新删除：</p>
                <ul>
                  {deletion.blockers.map((reason: string) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </>
            )}
            <div className="toolbar">
              <button
                className="danger"
                disabled={!deletion.allowed}
                onClick={applyDeletion}
              >
                确认从草稿删除
              </button>
              <button onClick={() => setDeletion(null)}>关闭 / 取消</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
function App() {
  const [project, setProject] = useState<Obj | null>(null),
    [schema, setSchema] = useState<Obj | null>(null),
    [dataset, setDataset] = useState<Obj | null>(null),
    [datasetKey, setDatasetKey] = useState(""),
    [episode, setEpisode] = useState(0),
    [frame, setFrame] = useState(0),
    [mode, setMode] = useState("state"),
    [page, setPage] = useState("work"),
    [view, setView] = useState<Obj | null>(null),
    [images, setImages] = useState<Obj>({}),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("就绪"),
    [error, setError] = useState(""),
    [refresh, setRefresh] = useState(0),
    [path, setPath] = useState(
      "/home/punk/yann_repo/paper_code_01/tron2_dataset/lerobot_v2.1_examples",
    ),
    [search, setSearch] = useState(""),
    [range, setRange] = useState<[number, number]>([0, 1]),
    [interval, setBatchInterval] = useState(false),
    [batchDraft, setBatchDraft] = useState<Obj>({ version: 0, changes: [] }),
    [preview, setPreview] = useState<Obj | null>(null),
    [previewLoading, setPreviewLoading] = useState(false),
    [futureHorizon, setFutureHorizon] = useState(0.25),
    [historyConfig, setHistoryConfig] = useState({
      horizon_seconds: 0.6,
      num_frames: 4,
    }),
    [historyDraft, setHistoryDraft] = useState({ horizon: "0.6", frames: "4" }),
    [historyCapacity, setHistoryCapacity] = useState(0.6),
    [confirmed, setConfirmed] = useState(false),
    [reviewScope, setReviewScope] = useState("frame"),
    [reviewReport, setReviewReport] = useState<Obj | null>(null),
    [reviewLoading, setReviewLoading] = useState(false),
    [completionResult, setCompletionResult] = useState<Obj | null>(null),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [tracks, setTracks] = useState<string[]>([]),
    [zoom, setZoom] = useState<[number, number] | null>(null),
    [modal, setModal] = useState<string | null>(null),
    [scale, setScale] = useState(1),
    [pan, setPan] = useState([0, 0]),
    [job, setJob] = useState<Obj | null>(null),
    [destination, setDestination] = useState(""),
    [inspectPath, setInspectPath] = useState(""),
    [ratio, setRatio] = useState(0.7),
    [cal, setCal] = useState<Obj>({
      verified: false,
      unit: "",
      closed_threshold: 0.1,
      open_threshold: 0.8,
      closed_below: true,
      min_duration: 0.1,
    }),
    [assistArm, setAssistArm] = useState("left"),
    [assistFeature, setAssistFeature] = useState("observation.state"),
    [assistDim, setAssistDim] = useState(7),
    [suggestion, setSuggestion] = useState<Obj | null>(null),
    [coordinate, setCoordinate] = useState<Obj>({
      verified: false,
      unit: "m",
      frame: "robot_base",
      position_noise: 0,
      angle_noise: 0,
      transform: [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
      ],
    }),
    [taskSettings, setTaskSettings] = useState<Obj>({
      target: "",
      support: "",
      gripper_width: "",
      calibration_notes: "",
    });
  const playbackClock = useRef({ frame: 0, time: 0 }),
    requestVersion = useRef(0),
    pendingWrites = useRef(0),
    initial = useRef(false),
    latestFrame = useRef(frame);
  latestFrame.current = frame;
  const notify = (s: string) => {
    setError("");
    setMessage(s);
  };
  const fail = (e: any) => {
    setError(e.message || String(e));
    setPlaying(false);
  };
  const n =
    dataset?.episodes.find((e: Obj) => e.episode_index === episode)?.length ||
    1;
  const fps = dataset?.info.fps || 30;
  const timelineStart = zoom?.[0] ?? 0;
  const timelineEnd = zoom?.[1] ?? n - 1;
  const timelineSpan = Math.max(1, timelineEnd - timelineStart);
  const timelinePosition = (value: number) =>
    (100 * (value - timelineStart)) / timelineSpan + "%";
  const timelineInterval = (start: number, end: number) => ({
    left: timelinePosition(start),
    width: (100 * (end - start)) / timelineSpan + "%",
  });
  const timelineVisible = (start: number, end: number) =>
    end > timelineStart && start <= timelineEnd;
  useEffect(() => {
    if (zoom && (frame < zoom[0] || frame > zoom[1])) setZoom(null);
  }, [frame, zoom]);
  const aligned =
    !!view &&
    !loading &&
    view.frame === frame &&
    view.evidence_mode === mode &&
    view.history_config?.horizon_seconds === historyConfig.horizon_seconds &&
    view.history_config?.num_frames === historyConfig.num_frames;
  const completionContext = JSON.stringify([
    datasetKey,
    episode,
    frame,
    mode,
    refresh,
    schema?.version,
    view?.revision,
    busy,
    batchDraft,
  ]);
  const completionRequest = useRef(0);
  const completionAbort = useRef<AbortController | null>(null);
  const completionPanel = useRef<HTMLDivElement | null>(null);
  const visibleCompletion =
    completionResult?.context === completionContext ? completionResult : null;
  useEffect(() => {
    ++completionRequest.current;
    completionAbort.current?.abort();
    return () => completionAbort.current?.abort();
  }, [completionContext]);
  useEffect(() => {
    if (!visibleCompletion || visibleCompletion.state === "checking") return;
    const id = requestAnimationFrame(() => {
      completionPanel.current?.focus({ preventScroll: true });
      completionPanel.current?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
    });
    return () => cancelAnimationFrame(id);
  }, [visibleCompletion]);
  async function checkCompleteness() {
    setPlaying(false);
    completionAbort.current?.abort();
    const controller = new AbortController();
    completionAbort.current = controller;
    const requestId = ++completionRequest.current;
    const context = completionContext;
    setCompletionResult({
      context,
      frame,
      episode,
      state: "checking",
      errors: [],
    });
    try {
      const result = await api(
        `/completeness/${datasetKey}/${episode}/${frame}`,
        undefined,
        controller.signal,
      );
      if (requestId !== completionRequest.current || controller.signal.aborted)
        return;
      setCompletionResult({
        context,
        frame,
        episode,
        state: result.complete ? "passed" : "issues",
        errors: [...new Set((result.errors || []).map(String))],
      });
    } catch (e: any) {
      if (e.name !== "AbortError" && requestId === completionRequest.current)
        setCompletionResult({
          context,
          frame,
          episode,
          state: "error",
          errors: [e.message || String(e)],
        });
    }
  }
  function closeCompletion() {
    ++completionRequest.current;
    completionAbort.current?.abort();
    setCompletionResult(null);
  }
  const historySteps = Array.from(
    { length: historyConfig.num_frames },
    (_, i) => i,
  );
  const historyOffsets = historySteps.map((i) =>
    historyConfig.num_frames === 1
      ? 0
      : (historyConfig.horizon_seconds * (historyConfig.num_frames - 1 - i)) /
        (historyConfig.num_frames - 1),
  );
  const currentStep = historyConfig.num_frames - 1;
  function applyHistory() {
    const horizon = Number(historyDraft.horizon),
      frames = Number(historyDraft.frames);
    if (
      !historyDraft.horizon.trim() ||
      !historyDraft.frames.trim() ||
      !Number.isFinite(horizon) ||
      horizon < 0 ||
      horizon > 3600 ||
      !Number.isInteger(frames) ||
      frames < 1 ||
      frames > 32 ||
      (frames === 1) !== (horizon === 0)
    )
      return fail(
        Error(
          "跨度0–3600秒，时刻数1–32（含当前）；单时刻须设跨度0，多时刻须设正跨度",
        ),
      );
    setPlaying(false);
    setConfirmed(false);
    setHistoryConfig({ horizon_seconds: horizon, num_frames: frames });
    setHistoryCapacity((value) => Math.max(value, horizon));
  }
  async function loadProject() {
    const p = await api("/project");
    setProject(p);
    setSchema(p.schema);
    return p;
  }
  async function choose(key: string, e = 0, f = 0, restoreHistory = false) {
    try {
      setLoading(true);
      setPlaying(false);
      const d = await api("/datasets/" + key);
      setDataset(d);
      if (d.history_sampling && !restoreHistory) {
        const config = {
          horizon_seconds: d.history_sampling.horizon_seconds,
          num_frames: d.history_sampling.num_frames,
        };
        setHistoryConfig(config);
        setHistoryDraft({
          horizon: String(config.horizon_seconds),
          frames: String(config.num_frames),
        });
        setHistoryCapacity(d.history_context_seconds);
      }
      if (d.root) setPath(d.root);
      if (key !== datasetKey) {
        setTracks(defaultTracks(d.info));
        setAssistDim(
          Math.max(
            0,
            featureNames(d.info.features["observation.state"]).findIndex((s) =>
              /gripper/i.test(s),
            ),
          ),
        );
        setCal((c) => ({ ...c, verified: false }));
      }
      setDatasetKey(key);
      setEpisode(e);
      setFrame(f);
      setZoom(null);
      setRange([f, f + 1]);
    } catch (e) {
      fail(e);
      setLoading(false);
    }
  }
  useEffect(() => {
    loadProject()
      .then((p) => {
        const pref = p.preferences;
        if (pref.tracks) setTracks(pref.tracks);
        if (pref.mode) setMode(pref.mode);
        if (pref.taskSettings) setTaskSettings(pref.taskSettings);
        if (pref.coordinate) setCoordinate(pref.coordinate);
        if (pref.futureHorizon) setFutureHorizon(pref.futureHorizon);
        if (pref.historyConfig) {
          setHistoryConfig(pref.historyConfig);
          setHistoryDraft({
            horizon: String(pref.historyConfig.horizon_seconds),
            frames: String(pref.historyConfig.num_frames),
          });
        }
        setHistoryCapacity(
          pref.historyCapacity ?? pref.historyConfig?.horizon_seconds ?? 0.6,
        );
        try {
          const saved = localStorage.getItem("dse-batch-draft:" + p.work_root);
          const restored = saved ? JSON.parse(saved) : pref.batchDraft;
          if (restored?.changes?.length) setBatchDraft(restored);
        } catch {
          /* Invalid local drafts never change saved annotations. */
        }
        setDestination(
          p.work_root.replace(/\/[^/]+$/, "") + "/export-" + Date.now(),
        );
        if (p.datasets.length)
          choose(
            pref.datasetKey || p.datasets[0].fingerprint,
            pref.episode || 0,
            pref.frame || 0,
            !!pref.historyConfig,
          ).then(() => {
            if (pref.tracks) setTracks(pref.tracks);
            if (pref.range) setRange(pref.range);
            if (pref.zoom) setZoom(pref.zoom);
            if (pref.speed) setSpeed(pref.speed);
            if (pref.interval) setBatchInterval(pref.interval);
          });
        initial.current = true;
      })
      .catch(fail);
  }, []);
  useEffect(() => {
    if (!initial.current) return;
    const id = setTimeout(
      () =>
        api("/preferences", {
          datasetKey,
          episode,
          frame,
          mode,
          tracks,
          taskSettings,
          coordinate,
          range,
          zoom,
          speed,
          interval,
          batchDraft,
          futureHorizon,
          historyConfig,
          historyCapacity,
        }).catch(fail),
      400,
    );
    return () => clearTimeout(id);
  }, [
    datasetKey,
    episode,
    frame,
    mode,
    tracks,
    taskSettings,
    coordinate,
    range,
    zoom,
    speed,
    interval,
    batchDraft,
    futureHorizon,
    historyConfig,
    historyCapacity,
  ]);
  useEffect(() => {
    if (project?.work_root && initial.current)
      localStorage.setItem(
        "dse-batch-draft:" + project.work_root,
        JSON.stringify(batchDraft),
      );
  }, [batchDraft, project?.work_root]);
  useEffect(() => {
    if (!datasetKey) return;
    const version = ++requestVersion.current,
      controller = new AbortController();
    setLoading(true);
    setError("");
    setConfirmed(false);
    let blobs: string[] = [];
    const timer = setTimeout(async () => {
      try {
        const v = await api(
          `/window/${datasetKey}/${episode}/${frame}?mode=${mode}&request_id=${version}&future_horizon=${futureHorizon}&history_horizon=${historyConfig.horizon_seconds}&history_frames=${historyConfig.num_frames}`,
          undefined,
          controller.signal,
        );
        const nextImages: Obj = {};
        await Promise.all(
          [
            ...v.history.map((h: Obj, t: number) => ({
              ...h,
              imageKey: String(t),
            })),
            ...(mode === "behavior"
              ? (v.future || []).map((h: Obj, t: number) => ({
                  ...h,
                  imageKey: "future:" + t,
                }))
              : []),
          ].map(async (h: Obj) => {
            if (h.frame === null) return;
            await Promise.all(
              v.cameras.map(async (_: string, c: number) => {
                const r = await fetch(
                  `/api/frame/${datasetKey}/${episode}/${c}/${h.frame}?request_id=${version}`,
                  { signal: controller.signal },
                );
                if (!r.ok) {
                  const body = await r.json();
                  throw Error(body.detail || "帧解码失败");
                }
                const blob = await r.blob();
                if (
                  controller.signal.aborted ||
                  version !== requestVersion.current
                )
                  return;
                const url = URL.createObjectURL(blob);
                blobs.push(url);
                nextImages[`${h.imageKey}:${c}`] = url;
              }),
            );
          }),
        );
        if (version !== requestVersion.current) return;
        setView(v);
        setImages(nextImages);
        setLoading(false);
        setMessage("已同步 · 所有修改自动保存");
      } catch (e: any) {
        if (e.name !== "AbortError" && version === requestVersion.current) {
          setImages({});
          setView(null);
          setLoading(false);
          fail(e);
        }
      }
    }, 70);
    return () => {
      clearTimeout(timer);
      controller.abort();
      blobs.forEach(URL.revokeObjectURL);
    };
  }, [datasetKey, episode, frame, mode, refresh, futureHorizon, historyConfig]);
  useEffect(() => {
    if (playing)
      playbackClock.current = {
        frame: latestFrame.current,
        time: performance.now(),
      };
  }, [playing, speed, datasetKey, episode]);
  useEffect(() => {
    if (!playing || !aligned) return;
    const id = setTimeout(() => {
      if (frame >= n - 1) {
        setPlaying(false);
        return;
      }
      setFrame(
        Math.min(
          n - 1,
          Math.max(
            frame + 1,
            playbackClock.current.frame +
              Math.floor(
                ((performance.now() - playbackClock.current.time) / 1000) *
                  fps *
                  speed,
              ),
          ),
        ),
      );
    }, 80);
    return () => clearTimeout(id);
  }, [playing, aligned, frame, speed, n]);
  useEffect(() => {
    if (!job || !["running", "queued"].includes(job.status)) return;
    const id = setInterval(
      () =>
        api("/jobs/" + job.id)
          .then(setJob)
          .catch(fail),
      700,
    );
    return () => clearInterval(id);
  }, [job?.id, job?.status]);
  async function mutate(path: string, body: any) {
    setReviewReport(null);
    pendingWrites.current++;
    setBusy(true);
    setMessage("正在保存…");
    try {
      const r = await api(path, body);
      setRefresh((x) => x + 1);
      if (datasetKey)
        api("/datasets/" + datasetKey)
          .then(setDataset)
          .catch(fail);
      notify("已保存到 SQLite");
      return r;
    } catch (e) {
      fail(e);
    } finally {
      pendingWrites.current--;
      setBusy(pendingWrites.current > 0);
    }
  }
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => {
      if (pendingWrites.current) {
        e.preventDefault();
        e.returnValue = "仍有修改正在保存";
      }
    };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, []);
  const navigate = (delta: number) => {
    setPlaying(false);
    setFrame((f) => Math.max(0, Math.min(n - 1, f + delta)));
  };
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).matches("input,textarea")) return;
      const label = (e.target as HTMLElement).getAttribute("aria-label");
      if (label && schema && aligned && !busy) {
        const [arm, ...parts] = label.split(" ");
        const key = parts.join(" ");
        const f = schema.fields.find((f: Obj) => f.field_id === key);
        const c = f?.classes.find(
          (c: Obj) => c.shortcut && c.shortcut === e.key && !c.deprecated,
        );
        if (c) {
          e.preventDefault();
          changeLabel(arm, key, c.id);
          return;
        }
      }
      if ((e.target as HTMLElement).matches("select")) return;
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
      if (e.key === "ArrowRight") navigate(1);
      if (e.key === "ArrowLeft") navigate(-1);
      if (e.key === "[") setRange([frame, Math.max(frame + 1, range[1])]);
      if (e.key === "]") setRange([Math.min(range[0], frame), frame + 1]);
      if (e.ctrlKey && e.key === "z") {
        e.preventDefault();
        mutate("/history/" + (e.shiftKey ? "redo" : "undo"), {});
      }
      if (e.ctrlKey && e.key === "y") {
        e.preventDefault();
        mutate("/history/redo", {});
      }
      if (e.key === "Tab") {
        e.preventDefault();
        setMode((m) => (m === "state" ? "behavior" : "state"));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [frame, n, range, schema, aligned, busy, mode, interval]);
  function annotation(
    arm: string,
    field: string,
    value: number | null,
    reviewed = false,
  ) {
    return {
      dataset: datasetKey,
      episode,
      start: interval ? range[0] : frame,
      end: interval ? range[1] : frame + 1,
      arm,
      field,
      value,
      mode,
      reviewed,
      confirmed,
      point: !interval,
      history_config: historyConfig,
    };
  }
  const focusFrame =
    interval &&
    range[0] >= 0 &&
    range[0] < range[1] &&
    range[1] <= n &&
    !(range[0] <= frame && frame < range[1])
      ? range[0]
      : frame;
  const draftToken = JSON.stringify([
    batchDraft,
    datasetKey,
    episode,
    focusFrame,
    mode,
    refresh,
  ]);
  const effectivePreview = preview?.token === draftToken ? preview : null;
  useEffect(() => {
    if (!batchDraft.changes.length || !datasetKey) {
      setPreview(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setPreviewLoading(true);
    const timer = setTimeout(() => {
      api(
        "/annotations/batch",
        {
          changes: batchDraft.changes,
          expected_schema_version: batchDraft.version,
          preview: true,
          focus: { dataset: datasetKey, episode, frame: focusFrame, mode },
        },
        controller.signal,
      )
        .then((result) => {
          if (active) {
            setPreview({ ...result, token: draftToken });
            setPreviewLoading(false);
          }
        })
        .catch((e) => {
          if (active && e.name !== "AbortError") {
            setPreview(null);
            setPreviewLoading(false);
            fail(e);
          }
        });
    }, 60);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [draftToken]);
  async function changeLabel(
    arm: string,
    field: string,
    value: number | null,
    reviewed = false,
  ) {
    setReviewReport(null);
    const body = annotation(arm, field, value, reviewed);
    if (interval) {
      const sameTarget = (change: Obj) =>
        [
          "dataset",
          "episode",
          "start",
          "end",
          "arm",
          "field",
          "mode",
          "point",
        ].every((key) => change[key] === body[key as keyof typeof body]);
      setBatchDraft((draft: Obj) => ({
        version: draft.changes.length ? draft.version : schema!.version,
        changes: [...draft.changes.filter((c: Obj) => !sameTarget(c)), body],
      }));
      setPreviewLoading(true);
      notify("草稿已更新，门控自动重算；填写完成后一次确认整组");
    } else await mutate("/annotations", body);
  }
  function cancelBatch() {
    setBatchDraft({ version: schema?.version || 0, changes: [] });
    setPreview(null);
    setPreviewLoading(false);
    notify("已取消整组草稿，已保存标签不变");
  }
  async function commitBatch() {
    const result = await mutate("/annotations/batch", {
      changes: batchDraft.changes,
      expected_schema_version: batchDraft.version,
    });
    if (result) {
      setBatchDraft({ version: schema?.version || 0, changes: [] });
      setPreview(null);
      notify("整组标签已一次保存，可一次撤销整组");
    }
  }
  async function importSource() {
    setBusy(true);
    setMessage("校验内容指纹、数据与媒体 PTS…");
    setError("");
    try {
      const r = await api("/import", { path });
      await loadProject();
      await choose(r.fingerprint);
      notify(
        `已导入 ${r.episodes.length} 个 episode；${r.report.warnings.length} 项物理类型差异已记录`,
      );
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    setConfirmed(false);
    setReviewReport(null);
  }, [
    reviewScope,
    range[0],
    range[1],
    datasetKey,
    episode,
    frame,
    mode,
    historyConfig,
  ]);
  async function reviewCurrent(preview = false) {
    if (batchDraft.changes.length)
      return fail(Error("请先确认整组草稿，再审核已保存字段"));
    if (!preview && !confirmed && mode === "state")
      return fail(Error("先确认整个审核范围的允许观测支持标签"));
    const start = reviewScope === "range" ? range[0] : frame;
    const end = reviewScope === "range" ? range[1] : frame + 1;
    if (!(0 <= start && start < end && end <= n))
      return fail(Error("请先设置有效审核选区"));
    setPlaying(false);
    setReviewLoading(true);
    const body = {
      dataset: datasetKey,
      episode,
      start,
      end,
      mode,
      confirmed,
      preview,
      expected_schema_version: schema!.version,
      expected_revision: view?.revision,
      history_config: historyConfig,
    };
    try {
      const result = preview
        ? await api("/annotations/review-range", body)
        : await mutate("/annotations/review-range", body);
      if (result) {
        setReviewReport(result);
        notify(
          preview
            ? `预检查：${result.eligible_pairs}个字段×帧可审核，尚未修改标签`
            : `已审核 ${result.reviewed_pairs} 个字段×帧，覆盖 ${result.reviewed_frames} 帧；跳过 ${result.skipped_pairs} 项，可一次撤销`,
        );
      }
    } catch (e) {
      fail(e);
    } finally {
      setReviewLoading(false);
    }
  }
  const scopeFields = (arm: string) =>
    schema?.fields.filter(
      (f: Obj) =>
        (arm === "global") === (f.scope === "global") && f.evidence === mode,
    ) || [];
  const visibleTracks = tracks.filter(
    (t) =>
      mode === "behavior" ||
      (t.startsWith("observation.") && !t.includes("cmd")),
  );
  const chartData = (view?.telemetry || [])
    .filter(
      (_: any, i: number, a: any[]) =>
        i % Math.max(1, Math.floor(a.length / 900)) === 0 || i === a.length - 1,
    )
    .map((r: Obj) =>
      Object.assign(
        { frame: r.frame, timestamp: r.timestamp },
        ...visibleTracks.map((t) => {
          const [key, dim] = t.split(":");
          return { [t]: r[key]?.[+dim] };
        }),
      ),
    );
  const telemetryFeatures = Object.entries(dataset?.info.features || {}).filter(
    ([k, f]: [string, any]) =>
      f.names &&
      f.dtype !== "video" &&
      (mode === "behavior" ||
        (k.startsWith("observation.") && !k.includes("cmd"))),
  );
  const descriptor = (t: string) => {
    const [key, dim] = t.split(":");
    return `${key === "action" ? "指令 action" : key.includes("cmd") ? "末端命令" : key === "observation.state" ? "实测 state" : "末端反馈"} · ${featureNames(dataset?.info.features[key])[+dim]}`;
  };
  function fieldPanel(arm: string) {
    return (
      <div className="arm-panel" key={arm}>
        <h3>
          <span className={"arm-dot " + arm} />
          {arm === "left"
            ? "左臂 LEFT"
            : arm === "right"
              ? "右臂 RIGHT"
              : "全局 GLOBAL"}
        </h3>
        {scopeFields(arm).map((f: Obj) => {
          const r =
            (interval
              ? effectivePreview?.form_labels[arm]?.[f.field_id]
              : null) || view?.labels[arm]?.[f.field_id];
          const staged = interval
            ? [...batchDraft.changes]
                .reverse()
                .find(
                  (c: Obj) =>
                    c.dataset === datasetKey &&
                    c.episode === episode &&
                    c.start <= focusFrame &&
                    focusFrame < c.end &&
                    c.arm === arm &&
                    c.field === f.field_id &&
                    c.mode === mode,
                )
            : undefined;
          return (
            <div
              className={
                "label-field " +
                (r?.applicability === "not_applicable" ? "dim" : "")
              }
              key={f.field_id}
            >
              <label title={f.description}>
                {f.label || f.field_id}
                {r?.required === true && <em>必填</em>}
                <small>
                  {r?.review_status || "draft"} · mask{" "}
                  {r?.effective_loss_mask || 0}
                </small>
              </label>
              <select
                aria-label={`${arm} ${f.field_id}`}
                disabled={
                  !aligned ||
                  busy ||
                  (interval && previewLoading) ||
                  r?.applicability === "not_applicable"
                }
                value={(staged ? staged.value : r?.value_id) ?? ""}
                onChange={(e) =>
                  changeLabel(
                    arm,
                    f.field_id,
                    e.target.value === "" ? null : +e.target.value,
                  )
                }
              >
                <option value="">— missing / 未标注 —</option>
                {f.classes
                  .filter((c: Obj) => !c.deprecated || c.id === r?.value_id)
                  .map((c: Obj) => (
                    <option key={c.id} value={c.id}>
                      {display(c)}
                    </option>
                  ))}
              </select>
              {r?.reason && <small>{r.reason}</small>}
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <>
      <header>
        <div className="brand">
          <span className="logo">D</span>
          <div>
            DSE <b>ANNOTATION</b>
            <small>双臂观测 · 本地工作台</small>
          </div>
        </div>
        <nav>
          {[
            ["work", "标注工作台"],
            ["schema", "字段与规则"],
            ["assist", "行为与校准"],
            ["export", "导出数据集"],
            ["inspect", "导出检查器"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={page === key ? "active" : ""}
              onClick={() => {
                setPage(key);
                if (key === "inspect") setPlaying(false);
                if (key === "assist") {
                  setMode("behavior");
                  setPlaying(false);
                }
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <span className={"save-dot " + (busy ? "saving" : "")} />
        <small>{busy ? "保存中…" : "本地自动保存"}</small>
      </header>
      {page !== "inspect" && (
        <div className="import-bar">
          <span>数据源</span>
          <input
            aria-label="本地数据路径"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <button className="primary" disabled={busy} onClick={importSource}>
            导入 / 重新绑定
          </button>
          {project?.datasets.length > 0 && (
            <select
              aria-label="已登记数据集"
              value={datasetKey}
              onChange={(e) => choose(e.target.value)}
            >
              <option value="">选择项目来源</option>
              {project!.datasets.map((d: Obj) => (
                <option key={d.fingerprint} value={d.fingerprint}>
                  {d.root.split("/").slice(-1)[0]} · {d.fingerprint.slice(0, 7)}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      {error ? (
        <div role="alert" className="notice error">
          {error}
          <button onClick={() => setError("")}>关闭</button>
        </div>
      ) : (
        <div className="notice">{message}</div>
      )}
      {!dataset && page !== "inspect" && (
        <div className="empty">
          <span className="eyebrow">LOCAL DATA, REAL EVIDENCE</span>
          <h1>从真实观测，建立可靠标签。</h1>
          <p>
            通过本机目录导入 LeRobot
            v2.1。三视角同步、左右臂独立标注，所有工作保存在本地 SQLite。
          </p>
          <p>原始数据只读。上方填入目录后开始。</p>
          {busy && <div className="loader">正在逐项校验…页面仍可操作</div>}
        </div>
      )}
      {dataset && page === "work" && (
        <main className="workspace">
          <aside className="episodes">
            <div className="section-heading">
              EPISODES <span>{dataset.episodes.length}</span>
            </div>
            <input
              placeholder="搜索任务 / episode"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="toolbar">
              <button
                onClick={() => {
                  const i = dataset.episodes.findIndex(
                    (e: Obj) => e.episode_index === episode,
                  );
                  if (i > 0)
                    choose(datasetKey, dataset.episodes[i - 1].episode_index);
                }}
              >
                ↑ 上一条
              </button>
              <button
                onClick={() => {
                  const i = dataset.episodes.findIndex(
                    (e: Obj) => e.episode_index === episode,
                  );
                  if (i < dataset.episodes.length - 1)
                    choose(datasetKey, dataset.episodes[i + 1].episode_index);
                }}
              >
                ↓ 下一条
              </button>
            </div>
            {dataset.episodes
              .filter((e: Obj) =>
                (String(e.episode_index) + " " + e.tasks.join(" "))
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((e: Obj) => (
                <button
                  className={
                    "episode " + (episode === e.episode_index ? "active" : "")
                  }
                  key={e.episode_index}
                  onClick={() => choose(datasetKey, e.episode_index)}
                >
                  <b>EP {String(e.episode_index).padStart(3, "0")}</b>
                  <span>
                    {(e.length / fps).toFixed(2)}s · {e.length} 帧
                  </span>
                  <small>{e.tasks.join(" ")}</small>
                  <small>
                    {e.selections.length} 个选区 · {e.reviewed_records}{" "}
                    条审核记录 · {e.reviewed_frames} 帧含已审核字段
                  </small>
                  <div className="progress">
                    <i
                      style={{
                        width:
                          Math.min(100, (e.reviewed_frames / e.length) * 100) +
                          "%",
                      }}
                    />
                  </div>
                </button>
              ))}
            <div className="footnote">
              校验：已通过
              <br />
              精度差异记录：{dataset.report?.warnings.length || 0} 项
              <br />
              {dataset.info.features["observation.state"]?.shape[0]}D state ·{" "}
              {dataset.info.fps} FPS
              <br />
              工作库：{project?.work_root}
            </div>
          </aside>
          <section className="viewer">
            <div className="viewer-heading">
              <div>
                <span className="eyebrow">
                  EPISODE {String(episode).padStart(3, "0")}
                </span>
                <h2>{view?.task || "加载任务…"}</h2>
              </div>
              <div className="mode-switch">
                <button
                  className={mode === "state" ? "active" : ""}
                  onClick={() => {
                    setMode("state");
                    setPlaying(false);
                  }}
                >
                  状态标注
                </button>
                <button
                  className={mode === "behavior" ? "active" : ""}
                  onClick={() => setMode("behavior")}
                >
                  行为复核
                </button>
              </div>
            </div>
            <div className="evidence-note">
              {mode === "state"
                ? `观测窗口 [t−${historyConfig.horizon_seconds}, t] · ${historyConfig.num_frames} 个时刻（含当前）· 只显示实测反馈 · 不显示动作指令及未来标签`
                : "整段行为证据 · 可审核动作、事件与 adviser · 查看行为模式将记录未来证据暴露"}
            </div>
            <div className="history-controls">
              <label>
                历史跨度（秒）
                <input
                  aria-label="历史跨度（秒）"
                  type="number"
                  min="0"
                  max="3600"
                  step="0.1"
                  value={historyDraft.horizon}
                  onChange={(e) =>
                    setHistoryDraft({
                      ...historyDraft,
                      horizon: e.target.value,
                    })
                  }
                />
              </label>
              <label>
                时刻数（含当前）
                <input
                  aria-label="历史时刻数（含当前）"
                  type="number"
                  min="1"
                  max="32"
                  step="1"
                  value={historyDraft.frames}
                  onChange={(e) =>
                    setHistoryDraft({ ...historyDraft, frames: e.target.value })
                  }
                />
              </label>
              <button onClick={applyHistory}>应用历史设置</button>
              <small>
                每个时刻含三相机；均匀采样到当前 t。5秒 / 3时刻 =
                t−5、t−2.5、t。改变证据窗口后请复核状态标签。
              </small>
            </div>
            <div className={"camera-region " + (!aligned ? "unaligned" : "")}>
              <div className="current-cameras">
                {(view?.cameras || Object.keys(names)).map(
                  (cam: string, c: number) => (
                    <div className="camera" key={cam}>
                      <div className="camera-label">
                        <span>{names[cam] || cam}</span>
                        <span>t · #{frame}</span>
                      </div>
                      {images[`${currentStep}:${c}`] && aligned ? (
                        <img
                          src={images[`${currentStep}:${c}`]}
                          alt={`${names[cam] || cam} 当前帧 ${frame}`}
                          onClick={() => {
                            setModal(images[`${currentStep}:${c}`]);
                            setScale(1);
                            setPan([0, 0]);
                          }}
                        />
                      ) : (
                        <div className="placeholder">
                          {loading ? "精确同步中…" : "画面缺失"}
                        </div>
                      )}
                    </div>
                  ),
                )}
              </div>
              <div className="history-scroll">
                <div
                  className="history-grid"
                  style={{
                    gridTemplateColumns: `55px repeat(${historyConfig.num_frames}, minmax(75px, 1fr))`,
                  }}
                >
                  <div className="history-label">历史参考</div>
                  {historySteps.map((t: number) => (
                    <div className="history-time" key={t}>
                      {t === currentStep
                        ? "当前 t"
                        : `t − ${Number(historyOffsets[t].toFixed(3))}`}
                      <small>
                        {aligned && view?.history[t]?.frame != null
                          ? `#${view?.history[t].frame} · ${view?.history[t].timestamp.toFixed(3)}s`
                          : "缺失 / mask 0"}
                      </small>
                    </div>
                  ))}
                  {(view?.cameras || Object.keys(names)).map(
                    (cam: string, c: number) => (
                      <React.Fragment key={cam}>
                        <div className="history-label">{names[cam] || cam}</div>
                        {historySteps.map((t) => (
                          <div className="history-cell" key={t}>
                            {aligned && images[`${t}:${c}`] ? (
                              <img
                                alt={`${cam} 历史 ${t}`}
                                src={images[`${t}:${c}`]}
                                onClick={() => {
                                  setModal(images[`${t}:${c}`]);
                                  setScale(1);
                                  setPan([0, 0]);
                                }}
                              />
                            ) : (
                              <span>{loading ? "同步中…" : "无有效历史"}</span>
                            )}
                          </div>
                        ))}
                      </React.Fragment>
                    ),
                  )}
                </div>
              </div>
              {loading && (
                <div className="loading-overlay">
                  正在将相机对齐到源帧 #{frame} · 标签编辑暂时锁定
                </div>
              )}
            </div>
            {mode === "behavior" && (
              <section className="future-reference" aria-label="未来帧参考">
                <div className="future-heading">
                  <h3>未来动作参考 · 决策帧固定 #{frame}</h3>
                  <label>
                    参考跨度
                    <select
                      aria-label="未来参考跨度"
                      value={futureHorizon}
                      onChange={(e) => setFutureHorizon(+e.target.value)}
                    >
                      {[0.25, 0.5, 1, 2, 5].map((s) => (
                        <option key={s} value={s}>
                          {s} 秒
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p>
                  用于 adviser / 动作复核；查看这些画面不会移动当前标签帧。自动
                  adviser 窗口仍最多0.25秒，切回状态标注立即隐藏未来证据。
                </p>
                <div className="future-grid">
                  <div className="history-label">相机</div>
                  {[0, 1, 2].map((t) => (
                    <div className="history-time" key={t}>
                      t +{" "}
                      {(
                        view?.future?.[t]?.offset_seconds ??
                        futureHorizon * [0.4, 0.8, 1][t]
                      ).toFixed(3)}
                      <small>
                        {aligned &&
                        view?.future?.[t]?.frame !== null &&
                        view?.future?.[t]
                          ? `#${view.future[t].frame} · ${view.future[t].timestamp.toFixed(3)}s`
                          : "未来不足 / mask 0"}
                      </small>
                    </div>
                  ))}
                  {(view?.cameras || Object.keys(names)).map(
                    (cam: string, c: number) => (
                      <React.Fragment key={cam}>
                        <div className="history-label">{names[cam] || cam}</div>
                        {[0, 1, 2].map((t) => (
                          <div className="history-cell" key={t}>
                            {aligned && images[`future:${t}:${c}`] ? (
                              <img
                                alt={`${names[cam] || cam} 未来帧 ${view?.future?.[t]?.frame}`}
                                src={images[`future:${t}:${c}`]}
                                onClick={() => {
                                  setModal(images[`future:${t}:${c}`]);
                                  setScale(1);
                                  setPan([0, 0]);
                                }}
                              />
                            ) : (
                              <span>
                                {loading ? "同步中…" : "无有效未来帧"}
                              </span>
                            )}
                          </div>
                        ))}
                      </React.Fragment>
                    ),
                  )}
                </div>
              </section>
            )}
            <div className="timeline">
              <div className="transport">
                <button aria-label="上一帧" onClick={() => navigate(-1)}>
                  ◀
                </button>
                <button
                  className="primary"
                  onClick={() => setPlaying((p) => !p)}
                >
                  {playing ? "暂停" : "播放"}
                </button>
                <button aria-label="下一帧" onClick={() => navigate(1)}>
                  ▶
                </button>
                <select
                  value={speed}
                  onChange={(e) => setSpeed(+e.target.value)}
                >
                  {[0.25, 0.5, 1, 2].map((s) => (
                    <option key={s} value={s}>
                      {s}×
                    </option>
                  ))}
                </select>
                <b>
                  {(frame / fps).toFixed(3)}{" "}
                  <small>/ {((n - 1) / fps).toFixed(3)} s</small>
                </b>
                <label>
                  源帧
                  <input
                    className="frame-input"
                    aria-label="源帧"
                    type="number"
                    min={0}
                    max={n - 1}
                    value={frame}
                    onChange={(e) =>
                      setFrame(Math.max(0, Math.min(n - 1, +e.target.value)))
                    }
                  />
                </label>
                <button
                  onClick={() =>
                    setZoom([
                      Math.max(0, frame - fps),
                      Math.min(n - 1, frame + fps),
                    ])
                  }
                >
                  放大时间轴
                </button>
                <button onClick={() => setZoom(null)}>全段</button>
              </div>
              <div className="scrubber-row">
                <input
                  className="scrubber"
                  aria-label="帧游标"
                  type="range"
                  min={timelineStart}
                  max={timelineEnd}
                  value={frame}
                  onChange={(e) => {
                    setPlaying(false);
                    setFrame(+e.target.value);
                  }}
                />
              </div>
              <div className="track training-centers">
                <span>训练中心</span>
                <div>
                  {view?.selections
                    .filter((s: Obj) => timelineVisible(s.start, s.end))
                    .map((s: Obj) => (
                      <button
                        title={`[${s.start},${s.end})`}
                        key={s.id}
                        aria-label={`训练中心区间 [${s.start},${s.end})`}
                        style={timelineInterval(s.start, s.end)}
                        onClick={() => {
                          setPlaying(false);
                          setRange([s.start, s.end]);
                          setFrame(s.start);
                        }}
                      />
                    ))}
                </div>
              </div>
              {mode === "behavior" &&
                ["left", "right"].map((arm) => (
                  <div className="track" key={arm}>
                    <span>{arm === "left" ? "左臂" : "右臂"}阶段</span>
                    <div>
                      {view?.tracks
                        .filter(
                          (r: Obj) =>
                            r.arm === arm &&
                            r.field === "phase" &&
                            timelineVisible(
                              r.start - (view.source_key.frame - view.frame),
                              r.end - (view.source_key.frame - view.frame),
                            ),
                        )
                        .map((r: Obj) => {
                          const offset = view.source_key.frame - view.frame;
                          const c = schema?.fields
                            .find((f: Obj) => f.field_id === "phase")
                            .classes.find((c: Obj) => c.id === r.value);
                          return (
                            <button
                              key={r.id}
                              title={c ? display(c) : "missing"}
                              style={{
                                ...timelineInterval(
                                  r.start - offset,
                                  r.end - offset,
                                ),
                                background: c?.color,
                              }}
                              onClick={() =>
                                setFrame(Math.max(0, r.start - offset))
                              }
                            />
                          );
                        })}
                    </div>
                  </div>
                ))}
              {mode === "behavior" && (
                <div className="track">
                  <span>事件 / 动作</span>
                  <div>
                    {view?.reviews
                      .filter((r: Obj) =>
                        timelineVisible(
                          r.start - (view.source_key.frame - view.frame),
                          r.end - (view.source_key.frame - view.frame),
                        ),
                      )
                      .map((r: Obj) => (
                        <i
                          key={"r" + r.id}
                          style={{
                            ...timelineInterval(
                              r.start - (view.source_key.frame - view.frame),
                              r.end - (view.source_key.frame - view.frame),
                            ),
                            background:
                              r.status === "approved"
                                ? "#34d399"
                                : r.status === "rejected"
                                  ? "#f87171"
                                  : "#64748b",
                          }}
                        />
                      ))}
                    {view?.events
                      .filter((e: Obj) => {
                        const value =
                          e.frame - (view.source_key.frame - view.frame);
                        return timelineStart <= value && value <= timelineEnd;
                      })
                      .map((e: Obj) => (
                        <button
                          className="event-marker"
                          title={`${e.type} · ${e.status}`}
                          key={"e" + e.id}
                          style={{
                            left: timelinePosition(
                              e.frame - (view.source_key.frame - view.frame),
                            ),
                          }}
                          onClick={() =>
                            setFrame(
                              e.frame - (view.source_key.frame - view.frame),
                            )
                          }
                        >
                          ◆
                        </button>
                      ))}
                  </div>
                </div>
              )}
              {mode === "behavior" && (
                <details className="extra-tracks">
                  <summary>
                    展开字段与审核轨 · 实线已审 / 淡色草稿或待复核
                  </summary>
                  {["left", "right", "global"].flatMap(
                    (arm) =>
                      schema?.fields
                        .filter(
                          (f: Obj) =>
                            (arm === "global") === (f.scope === "global") &&
                            f.field_id !== "phase",
                        )
                        .map((f: Obj) => (
                          <div className="track" key={arm + f.field_id}>
                            <span title={arm + "." + f.field_id}>
                              {arm === "left"
                                ? "左"
                                : arm === "right"
                                  ? "右"
                                  : "全局"}{" "}
                              {f.field_id}
                            </span>
                            <div>
                              {view?.tracks
                                .filter(
                                  (r: Obj) =>
                                    r.arm === arm &&
                                    r.field === f.field_id &&
                                    timelineVisible(
                                      r.start -
                                        (view.source_key.frame - view.frame),
                                      r.end -
                                        (view.source_key.frame - view.frame),
                                    ),
                                )
                                .map((r: Obj) => {
                                  const offset =
                                    view.source_key.frame - view.frame;
                                  const c = f.classes.find(
                                    (c: Obj) => c.id === r.value,
                                  );
                                  return (
                                    <button
                                      key={r.id}
                                      title={`${c ? display(c) : "missing"} · ${r.status}`}
                                      style={{
                                        ...timelineInterval(
                                          r.start - offset,
                                          r.end - offset,
                                        ),
                                        background: c?.color,
                                        opacity:
                                          r.status === "reviewed" ? 1 : 0.4,
                                        border:
                                          r.status === "needs_review"
                                            ? "1px solid #f87171"
                                            : undefined,
                                      }}
                                      onClick={() =>
                                        setFrame(
                                          Math.max(
                                            0,
                                            Math.min(n - 1, r.start - offset),
                                          ),
                                        )
                                      }
                                    />
                                  );
                                })}
                            </div>
                          </div>
                        )) || [],
                  )}
                </details>
              )}
              <div className="selection-row">
                <label>
                  首帧（含）
                  <input
                    aria-label="选区首帧"
                    type="number"
                    value={range[0]}
                    min={0}
                    max={n - 1}
                    onChange={(e) => setRange([+e.target.value, range[1]])}
                  />
                </label>
                <button
                  onClick={() =>
                    setRange([frame, Math.max(frame + 1, range[1])])
                  }
                >
                  [ 设起点
                </button>
                <label>
                  末帧（不含）
                  <input
                    aria-label="选区末帧不含"
                    type="number"
                    value={range[1]}
                    min={1}
                    max={n}
                    onChange={(e) => setRange([range[0], +e.target.value])}
                  />
                </label>
                <button
                  onClick={() =>
                    setRange([Math.min(range[0], frame), frame + 1])
                  }
                >
                  设终点 ]
                </button>
                <button
                  onClick={() =>
                    mutate("/selections", {
                      dataset: datasetKey,
                      episode,
                      start: range[0],
                      end: range[1],
                    })
                  }
                >
                  ＋保留训练区间
                </button>
                <button
                  onClick={() =>
                    mutate("/selections", {
                      dataset: datasetKey,
                      episode,
                      start: range[0],
                      end: range[1],
                      remove: true,
                    })
                  }
                >
                  移除区间
                </button>
              </div>
              <small>
                当前选区 [{range[0]}, {range[1]}) · 包含帧 {range[0]}–
                {range[1] - 1} · 导出前史 {historyCapacity}{" "}
                秒，尾部复核最多0.25秒另行保留。
              </small>
            </div>
            <section className="telemetry">
              <div className="section-heading">
                TELEMETRY <span>实测 / 命令具名轨道</span>
              </div>
              <details>
                <summary>选择轨道（{visibleTracks.length}）</summary>
                <div className="track-picker">
                  {telemetryFeatures.map(([key, f]: [string, any]) => (
                    <div key={key}>
                      <b>{key}</b>
                      {featureNames(f).map((name: string, d: number) => {
                        const id = key + ":" + d;
                        return (
                          <label key={id}>
                            <input
                              type="checkbox"
                              checked={tracks.includes(id)}
                              onChange={(e) =>
                                setTracks(
                                  e.target.checked
                                    ? [...tracks, id]
                                    : tracks.filter((t) => t !== id),
                                )
                              }
                            />
                            {d} · {name}
                          </label>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </details>
              <ResponsiveContainer width="100%" height={190}>
                <LineChart data={chartData}>
                  <CartesianGrid stroke="#253446" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="timestamp"
                    type="number"
                    domain={["dataMin", "dataMax"]}
                    tickFormatter={(v) => v.toFixed(2)}
                    stroke="#8191a7"
                  />
                  <YAxis
                    domain={["auto", "auto"]}
                    width={60}
                    stroke="#8191a7"
                  />
                  <Tooltip
                    labelFormatter={(v) => `${Number(v).toFixed(3)}s`}
                    contentStyle={{
                      background: "#132033",
                      border: "1px solid #35465b",
                    }}
                  />
                  <ReferenceLine x={view?.timestamp} stroke="#f8fafc" />
                  {visibleTracks.map((t, i) => (
                    <Line
                      key={t}
                      dataKey={(r: Obj) => r[t]}
                      name={descriptor(t)}
                      dot={false}
                      isAnimationActive={false}
                      stroke={
                        t.startsWith("action") || t.includes("cmd")
                          ? "#fb923c"
                          : ["#34d399", "#60a5fa", "#c084fc", "#fbbf24"][i % 4]
                      }
                      strokeDasharray={
                        t.startsWith("action") || t.includes("cmd")
                          ? "5 4"
                          : undefined
                      }
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
              <div className="current-values">
                {visibleTracks.map((t) => {
                  const [k, d] = t.split(":");
                  const r = view?.telemetry.find((r: Obj) => r.frame === frame);
                  return (
                    <small key={t}>
                      {descriptor(t)} <b>{r?.[k]?.[+d]?.toFixed(5) ?? "—"}</b>
                    </small>
                  );
                })}
              </div>
            </section>
          </section>
          <aside className="annotation">
            <div className="section-heading">
              ANNOTATION <span>v{schema?.version}</span>
            </div>
            <div className="toolbar">
              <button
                disabled={busy}
                onClick={() => mutate("/history/undo", {})}
              >
                撤销
              </button>
              <button
                disabled={busy}
                onClick={() => mutate("/history/redo", {})}
              >
                重做
              </button>
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={interval}
                onChange={(e) => {
                  setBatchInterval(e.target.checked);
                  if (
                    e.target.checked &&
                    range[0] >= 0 &&
                    range[0] < range[1] &&
                    range[1] <= n &&
                    !(range[0] <= frame && frame < range[1])
                  ) {
                    setPlaying(false);
                    setFrame(range[0]);
                  }
                }}
              />
              将下次赋值应用到明确选区
            </label>
            <div className="scope-note">
              {interval
                ? `批量区间 [${range[0]}, ${range[1]})`
                : `逐帧覆盖 · #${frame}`}
            </div>
            {[
              "left",
              "right",
              ...(schema?.fields.some((f: Obj) => f.scope === "global")
                ? ["global"]
                : []),
            ].map(fieldPanel)}
            {batchDraft.changes.length > 0 && (
              <div className="batch-preview" aria-label="整组标注草稿">
                <b>整组草稿 · {batchDraft.changes.length} 项待确认</b>
                <p>
                  选择值已显示；阶段与必填关系自动预览。
                  {interval && focusFrame !== frame
                    ? `门控参考选区首帧 #${focusFrame}。`
                    : ""}
                </p>
                {batchDraft.changes.map((c: Obj, i: number) =>
                  c.mode === mode ? (
                    <div className="draft-change" key={i}>
                      <span>
                        EP {c.episode} [{c.start},{c.end}) · {c.arm}.{c.field} →{" "}
                        {c.value ?? "missing"}
                      </span>
                      <button
                        aria-label={`移除草稿 ${i + 1}`}
                        disabled={busy}
                        onClick={() =>
                          setBatchDraft((draft: Obj) => ({
                            ...draft,
                            changes: draft.changes.filter(
                              (_: Obj, j: number) => i !== j,
                            ),
                          }))
                        }
                      >
                        ×
                      </button>
                    </div>
                  ) : null,
                )}
                {batchDraft.changes.some((c: Obj) => c.mode !== mode) && (
                  <p>另有其他证据模式草稿；切换后可查看其内容。</p>
                )}
                {previewLoading ? (
                  <p role="status">正在更新整组门控与覆盖范围…</p>
                ) : (
                  effectivePreview && (
                    <p>
                      字段帧合计：影响{" "}
                      {effectivePreview.reports.reduce(
                        (n: number, r: Obj) => n + r.affected,
                        0,
                      )}{" "}
                      · 跳过{" "}
                      {effectivePreview.reports.reduce(
                        (n: number, r: Obj) => n + r.skipped,
                        0,
                      )}{" "}
                      · 适用未知{" "}
                      {effectivePreview.reports.reduce(
                        (n: number, r: Obj) => n + r.needs_review,
                        0,
                      )}{" "}
                      · 重叠{" "}
                      {effectivePreview.reports.reduce(
                        (n: number, r: Obj) => n + r.overlaps,
                        0,
                      )}
                      <br />
                      保留逐帧覆盖{" "}
                      {effectivePreview.reports.reduce(
                        (n: number, r: Obj) => n + r.point_overrides,
                        0,
                      )}{" "}
                      个字段帧；提交后按各帧实际适用性生效。
                    </p>
                  )
                )}
                <button
                  className="primary"
                  disabled={busy || previewLoading || !effectivePreview}
                  onClick={commitBatch}
                >
                  确认保存全部 {batchDraft.changes.length} 项
                </button>
                <button disabled={busy} onClick={cancelBatch}>
                  取消整组草稿
                </button>
              </div>
            )}
            <label className="review-scope">
              标签审核范围
              <select
                aria-label="标签审核范围"
                value={reviewScope}
                onChange={(e) => setReviewScope(e.target.value)}
              >
                <option value="frame">当前帧 #{frame}</option>
                <option value="range">
                  当前选区 [{range[0]}, {range[1]}) · {range[1] - range[0]}帧
                </option>
              </select>
            </label>
            <p className="footnote">
              审核只更新已有标签的审核状态，保留区间内每帧的不同取值与逐帧覆盖；不会把当前帧标签铺满选区。
            </p>
            {mode === "state" && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                我确认
                {reviewScope === "range"
                  ? `选区 [${range[0]},${range[1]}) 内各帧`
                  : "当前帧"}
                的 [t−{historyConfig.horizon_seconds},t]、
                {historyConfig.num_frames} 个采样时刻能够支持已有状态标签。
              </label>
            )}
            <button
              className="primary full"
              disabled={
                !aligned ||
                busy ||
                reviewLoading ||
                batchDraft.changes.length > 0 ||
                (mode === "state" && !confirmed)
              }
              onClick={() => reviewCurrent()}
            >
              {reviewScope === "range"
                ? "审核选区已填写字段"
                : "审核当前已填写字段"}
            </button>
            <button
              className="full"
              disabled={
                !aligned ||
                busy ||
                reviewLoading ||
                batchDraft.changes.length > 0
              }
              onClick={() => reviewCurrent(true)}
            >
              预检查审核范围（不修改）
            </button>
            {reviewReport && (
              <div className="review-report" role="status">
                <b>
                  {reviewReport.saved ? "审核结果" : "审核预检查"} · [
                  {reviewReport.start},{reviewReport.end})
                </b>
                <p>
                  {reviewReport.reviewed_pairs} 项
                  {reviewReport.saved ? "已更新" : "可更新"} ·{" "}
                  {reviewReport.already_reviewed_pairs} 项此前已审 ·{" "}
                  {reviewReport.skipped_pairs} 项跳过（按字段×帧计数）
                </p>
                <small>
                  选区内 {reviewReport.training_center_frames}/
                  {reviewReport.frames}{" "}
                  帧是训练中心；审核不会自动加入训练中心选区。
                </small>
                <details>
                  <summary>查看跳过原因与位置</summary>
                  <p>
                    未填 {reviewReport.missing_pairs} · 不适用{" "}
                    {reviewReport.inapplicable_pairs} · 适用未知{" "}
                    {reviewReport.undetermined_pairs} · 冲突{" "}
                    {reviewReport.conflict_pairs} · 废弃/删除类别{" "}
                    {reviewReport.retired_pairs}
                  </p>
                  {reviewReport.issues.map((issue: Obj, i: number) => (
                    <button key={i} onClick={() => setFrame(issue.start)}>
                      [{issue.start},{issue.end}) {issue.arm}.{issue.field}：
                      {issue.reason}
                    </button>
                  ))}
                  {reviewReport.issue_groups > reviewReport.issues.length && (
                    <small>显示前100组，可修复后重新预检查。</small>
                  )}
                </details>
              </div>
            )}
            <button
              className="full"
              disabled={
                !aligned || busy || visibleCompletion?.state === "checking"
              }
              onClick={checkCompleteness}
              aria-describedby="completion-check-help"
            >
              {visibleCompletion?.state === "checking"
                ? "正在检查本帧…"
                : "检查本帧完成条件"}
            </button>
            {visibleCompletion && (
              <div
                className={"completion-feedback " + visibleCompletion.state}
                ref={completionPanel}
                tabIndex={-1}
                role={
                  visibleCompletion.state === "issues" ||
                  visibleCompletion.state === "error"
                    ? "alert"
                    : "status"
                }
                aria-label="本帧完成条件检查结果"
              >
                <div className="completion-feedback-heading">
                  <span className="completion-feedback-icon" aria-hidden="true">
                    {visibleCompletion.state === "passed"
                      ? "✓"
                      : visibleCompletion.state === "checking"
                        ? "…"
                        : "!"}
                  </span>
                  <strong>
                    {visibleCompletion.state === "passed"
                      ? "检查通过"
                      : visibleCompletion.state === "issues"
                        ? `待完成 · ${visibleCompletion.errors.length} 条提示`
                        : visibleCompletion.state === "error"
                          ? "检查失败"
                          : "正在检查…"}
                  </strong>
                  <button
                    aria-label="关闭本帧检查结果"
                    onClick={closeCompletion}
                  >
                    ×
                  </button>
                </div>
                <p>
                  EP {visibleCompletion.episode} · 帧 #{visibleCompletion.frame}
                </p>
                {visibleCompletion.state === "passed" ? (
                  <p>本帧所有必填与审核条件已完成。</p>
                ) : visibleCompletion.errors.length > 0 ? (
                  <ul>
                    {visibleCompletion.errors.map(
                      (message: string, index: number) => (
                        <li key={index}>{message}</li>
                      ),
                    )}
                  </ul>
                ) : (
                  <p>正在读取本帧已保存的标签…</p>
                )}
                <small>只读检查，不修改标签或 loss mask。</small>
                {batchDraft.changes.length > 0 && (
                  <small className="completion-draft-note">
                    还有 {batchDraft.changes.length}{" "}
                    项未确认草稿，本次检查不包含这些草稿。
                  </small>
                )}
              </div>
            )}
            <p className="footnote" id="completion-check-help">
              审核允许保留部分有效监督；“检查本帧完成条件”只报告本帧全部必填与审核缺口，不修改标签。unknown
              是已填类别；missing、不适用、待复核和仅上下文不计损失。
            </p>
            <div className="shortcuts">
              空格 播放 / 暂停
              <br />← → 逐帧 · [ ] 设置边界
              <br />
              Tab 切换证据模式
              <br />
              Ctrl Z / Shift Z 撤销 / 重做
            </div>
          </aside>
        </main>
      )}
      {page === "schema" && schema && (
        <SchemaEditor
          key={schema.version}
          schema={schema}
          workspace={project?.work_root || ""}
          notify={notify}
          onPublish={() => {
            loadProject().then(() => {
              setRefresh((x) => x + 1);
              if (datasetKey)
                api("/datasets/" + datasetKey)
                  .then(setDataset)
                  .catch(fail);
            });
          }}
        />
      )}
      {dataset && page === "assist" && (
        <section className="settings">
          <span className="eyebrow">BEHAVIOR REVIEW</span>
          <h1>校准与辅助审核</h1>
          <p>
            所有自动输出都保持
            suggestion，须人工核对。状态字段请在状态模式修改。
          </p>
          <div className="grid2">
            <article>
              <h2>任务与几何参考</h2>
              {Object.entries({
                target: "目标抓取部位",
                support: "承接区域",
                gripper_width: "夹爪最大开口宽度 W（注明单位）",
                calibration_notes: "标定说明",
              }).map(([k, label]) => (
                <label key={k}>
                  {label}
                  <input
                    value={taskSettings[k]}
                    onChange={(e) =>
                      setTaskSettings({ ...taskSettings, [k]: e.target.value })
                    }
                  />
                </label>
              ))}
              <h2>动作片段质量</h2>
              <p>
                EP {episode} · [{range[0]}, {range[1]})
              </p>
              {["approved", "rejected", "unreviewed"].map((status) => (
                <button
                  key={status}
                  onClick={() =>
                    mutate("/reviews", {
                      dataset: datasetKey,
                      episode,
                      start: range[0],
                      end: range[1],
                      status,
                      reason: taskSettings.review_reason || "",
                    })
                  }
                >
                  {status}
                </button>
              ))}
              <label>
                原因
                <input
                  value={taskSettings.review_reason || ""}
                  onChange={(e) =>
                    setTaskSettings({
                      ...taskSettings,
                      review_reason: e.target.value,
                    })
                  }
                />
              </label>
              <h2>手动事件</h2>
              <select
                value={assistArm}
                onChange={(e) => setAssistArm(e.target.value)}
              >
                <option value="left">左臂</option>
                <option value="right">右臂</option>
              </select>
              {["open", "closed"].map((type) => (
                <button
                  key={type}
                  onClick={() =>
                    mutate("/events", {
                      dataset: datasetKey,
                      episode,
                      frame,
                      arm: assistArm,
                      type,
                    })
                  }
                >
                  #{frame} {type}
                </button>
              ))}
              <p>在行为复核时间轴点击候选可跳转。当前源帧 #{frame}。</p>
              {view?.events.map((ev: Obj) => (
                <div className="event-row" key={ev.id}>
                  <span>
                    #{ev.frame} {ev.arm} · {ev.type} · {ev.status} ·{" "}
                    {JSON.parse(ev.details || "{}").feature === "action"
                      ? "命令事件"
                      : JSON.parse(ev.details || "{}").feature ===
                          "observation.state"
                        ? "实测反馈事件"
                        : "人工事件"}
                  </span>
                  <button
                    onClick={() => {
                      setFrame(ev.frame - (view.source_key.frame - view.frame));
                      setMode("behavior");
                      setPage("work");
                    }}
                  >
                    定位
                  </button>
                  <button
                    onClick={() => {
                      const f = ev.frame - (view.source_key.frame - view.frame);
                      setRange([
                        Math.max(0, f - Math.round(fps * 0.5)),
                        Math.min(n, f + Math.round(fps * 0.5) + 1),
                      ]);
                      setFrame(f);
                      setPage("work");
                    }}
                  >
                    选择附近 ±0.5秒
                  </button>
                  <button
                    onClick={() =>
                      mutate("/events", {
                        dataset: datasetKey,
                        episode,
                        frame: ev.frame - (view.source_key.frame - view.frame),
                        arm: ev.arm,
                        type: ev.type,
                        id: ev.id,
                        status: "confirmed",
                      })
                    }
                  >
                    确认
                  </button>
                  <button
                    onClick={() =>
                      mutate("/events", {
                        dataset: datasetKey,
                        episode,
                        frame,
                        arm: ev.arm,
                        type: ev.type,
                        id: ev.id,
                        status: "confirmed",
                      })
                    }
                  >
                    边界改为当前帧
                  </button>
                </div>
              ))}
            </article>
            <article>
              <h2>夹爪事件候选</h2>
              <label>
                来源
                <select
                  value={assistFeature}
                  onChange={(e) => setAssistFeature(e.target.value)}
                >
                  <option value="observation.state">
                    实测反馈 observation.state
                  </option>
                  <option value="action">动作命令 action</option>
                </select>
              </label>
              <label>
                具名通道
                <select
                  value={assistDim}
                  onChange={(e) => setAssistDim(+e.target.value)}
                >
                  {featureNames(dataset.info.features[assistFeature]).map(
                    (s: string, i: number) => (
                      <option key={i} value={i}>
                        {i} · {s}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                单位
                <input
                  value={cal.unit}
                  onChange={(e) => setCal({ ...cal, unit: e.target.value })}
                />
              </label>
              {[
                ["closed_threshold", "闭合阈值"],
                ["open_threshold", "打开阈值"],
                ["min_duration", "最短持续秒"],
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type="number"
                    step="0.001"
                    value={cal[key]}
                    onChange={(e) => setCal({ ...cal, [key]: +e.target.value })}
                  />
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={cal.closed_below}
                  onChange={(e) =>
                    setCal({ ...cal, closed_below: e.target.checked })
                  }
                />
                较小数值为闭合
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={cal.verified}
                  onChange={(e) =>
                    setCal({ ...cal, verified: e.target.checked })
                  }
                />
                已核对单位、方向与滞回配置
              </label>
              <button
                className="primary"
                onClick={() => {
                  setMode("behavior");
                  mutate("/events/suggest", {
                    dataset: datasetKey,
                    episode,
                    arm: assistArm,
                    feature: assistFeature,
                    dimension: assistDim,
                    calibration: cal,
                  });
                }}
              >
                生成事件建议
              </button>
              <h2>adviser 方向预标注</h2>
              <p>
                规范基座系 +X前 / +Y左 /
                +Z上；使用已审核、连续0.1–0.25秒实测位移。
              </p>
              <label>
                坐标变换 4×4（JSON）
                <textarea
                  defaultValue={JSON.stringify(coordinate.transform)}
                  onBlur={(e) => {
                    try {
                      setCoordinate({
                        ...coordinate,
                        transform: JSON.parse(e.target.value),
                      });
                    } catch {
                      fail(Error("变换矩阵需要有效 JSON"));
                    }
                  }}
                />
              </label>
              <label>
                静止位置噪声（米）
                <input
                  type="number"
                  step="0.001"
                  value={coordinate.position_noise}
                  onChange={(e) =>
                    setCoordinate({
                      ...coordinate,
                      position_noise: +e.target.value,
                    })
                  }
                />
              </label>
              <label>
                静止角噪声（弧度）
                <input
                  type="number"
                  step="0.001"
                  value={coordinate.angle_noise}
                  onChange={(e) =>
                    setCoordinate({
                      ...coordinate,
                      angle_noise: +e.target.value,
                    })
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={coordinate.verified}
                  onChange={(e) =>
                    setCoordinate({ ...coordinate, verified: e.target.checked })
                  }
                />
                已验证米制单位与 robot_base 变换
              </label>
              <button
                onClick={async () => {
                  try {
                    const r = await api("/adviser/suggest", {
                      dataset: datasetKey,
                      episode,
                      frame,
                      arm: assistArm,
                      calibration: coordinate,
                    });
                    setSuggestion(r);
                  } catch (e) {
                    fail(e);
                  }
                }}
              >
                计算当前帧建议
              </button>
              {suggestion && (
                <div className="batch-preview">
                  <pre>{JSON.stringify(suggestion, null, 2)}</pre>
                  <button
                    disabled={!suggestion.values}
                    onClick={async () => {
                      setMode("behavior");
                      for (const [key, value] of Object.entries(
                        suggestion.values || {},
                      ))
                        await mutate("/annotations", {
                          dataset: datasetKey,
                          episode,
                          start: frame,
                          end: frame + 1,
                          arm: assistArm,
                          field: "adviser." + key,
                          value,
                          mode: "behavior",
                          point: true,
                          label_source: "rule_suggestion",
                          provenance: suggestion,
                        });
                      notify("候选已保存为草稿，仍需人工审核");
                    }}
                  >
                    接受为草稿
                  </button>
                </div>
              )}
            </article>
          </div>
        </section>
      )}
      {page === "inspect" && (
        <ExportInspector
          initialPath={inspectPath}
          exports={(project?.jobs || []).filter(
            (j: Obj) => j.status === "complete",
          )}
        />
      )}
      {dataset && page === "export" && (
        <section className="settings">
          <span className="eyebrow">PORTABLE LEROBOT v2.1</span>
          <h1>导出独立片段数据集</h1>
          <p>
            每个不连续中心区间成为独立 episode，携带真实历史、复核上下文、H.264
            视频与分离标签。
          </p>
          <div className="grid2">
            <article>
              <h2>发布设置</h2>
              <p>
                训练默认历史：{historyConfig.horizon_seconds} 秒 /{" "}
                {historyConfig.num_frames} 个时刻（含当前），可在主页修改。
              </p>
              <label>
                保留前史（秒）
                <input
                  aria-label="导出保留前史（秒）"
                  type="number"
                  min={historyConfig.horizon_seconds}
                  max="3600"
                  step="0.1"
                  value={historyCapacity}
                  onChange={(e) => setHistoryCapacity(+e.target.value)}
                />
              </label>
              <p>
                可预留更长前史供实验使用。例如保留5秒后，训练可切换0.6秒或5秒并改变帧数；仅保留0.6秒的旧导出需重新导出才能实验5秒。
              </p>
              <label>
                新目标目录
                <input
                  aria-label="导出目录"
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                />
              </label>
              <label>
                关键区域初始采样比例（不改变 loss mask）
                <input
                  type="number"
                  min={0}
                  max={1}
                  step=".05"
                  value={ratio}
                  onChange={(e) => setRatio(+e.target.value)}
                />
              </label>
              <p>
                关键区域 {Math.round(ratio * 100)}% / 普通背景{" "}
                {Math.round((1 - ratio) * 100)}%。同源片段保持
                split_group；无有效标签的中心不抽样。
              </p>
              <button
                className="primary"
                disabled={
                  historyCapacity < historyConfig.horizon_seconds ||
                  (!!job && ["running", "queued"].includes(job.status))
                }
                onClick={async () => {
                  try {
                    const j = await api("/export", {
                      dataset: datasetKey,
                      destination,
                      key_ratio: ratio,
                      history_config: historyConfig,
                      history_context_seconds: historyCapacity,
                    });
                    setJob(j);
                  } catch (e) {
                    fail(e);
                  }
                }}
              >
                开始后台导出
              </button>
              {job && (
                <div className="job">
                  <h3>
                    {job.status} · {Math.round(job.progress * 100)}%
                  </h3>
                  <progress max={1} value={job.progress} />
                  <p>{job.destination}</p>
                  {job.error && <p className="error">{job.error}</p>}
                  {["running", "queued"].includes(job.status) && (
                    <button
                      onClick={() =>
                        api("/jobs/" + job.id + "/cancel").catch(fail)
                      }
                    >
                      取消导出
                    </button>
                  )}
                  {job.status === "complete" && (
                    <>
                      <button
                        onClick={() => {
                          setPath(job.destination);
                          notify("导出路径已填入顶部，可导入重开");
                        }}
                      >
                        填入导入路径
                      </button>
                      <button
                        className="primary"
                        onClick={() => {
                          setInspectPath(job.destination);
                          setPage("inspect");
                        }}
                      >
                        可视化检查此导出
                      </button>
                      <pre>{JSON.stringify(job.summary, null, 2)}</pre>
                    </>
                  )}
                </div>
              )}
            </article>
            <article>
              <h2>选区与真实上下文</h2>
              {dataset.episodes.map((e: Obj) => (
                <div key={e.episode_index}>
                  <h3>EP {e.episode_index}</h3>
                  {(e.episode_index === episode
                    ? view?.selections
                    : e.selections
                  )?.map((s: Obj) => (
                    <p key={s.id}>
                      中心 [{s.start}, {s.end}) · {s.end - s.start} 帧<br />
                      <small>
                        预计保留{" "}
                        {Math.max(0, s.start / fps - historyCapacity).toFixed(
                          3,
                        )}
                        –
                        {Math.min(
                          (e.length - 1) / fps,
                          (s.end - 1) / fps + 0.25,
                        ).toFixed(3)}{" "}
                        秒（按源时间最终确定）
                      </small>
                    </p>
                  ))}
                </div>
              ))}
              <p>
                草稿可增量导出；缺失值保留为 null，mask
                0。验证报告列出完整、部分和无监督中心数量。
              </p>
              <p>
                新增分类头见 annotations/head_spec.json。原生 LeRobot loader
                读取标准数据，DSE wrapper 显式 join 标签。
              </p>
            </article>
          </div>
        </section>
      )}
      {modal && (
        <div className="modal" onClick={() => setModal(null)}>
          <button className="modal-close" onClick={() => setModal(null)}>
            关闭 ×
          </button>
          <div className="modal-tools" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => setScale((s) => Math.max(1, s - 0.5))}>
              −
            </button>
            <span>{scale}× · 拖动平移</span>
            <button onClick={() => setScale((s) => Math.min(6, s + 0.5))}>
              ＋
            </button>
          </div>
          <img
            draggable={false}
            src={modal}
            style={{
              transform: `translate(${pan[0]}px,${pan[1]}px) scale(${scale})`,
            }}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (e.buttons === 1)
                setPan((p) => [p[0] + e.movementX, p[1] + e.movementY]);
            }}
          />
        </div>
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
