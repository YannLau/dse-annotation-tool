import React, { useEffect, useRef, useState } from "react";
import { t as tr, useI18n, categoryLabel } from "./i18n";
import "./behavior-calibration.css";

type Obj = Record<string, any>;
export type BehaviorCalibrationProps = {
  datasetKey: string;
  dataset: Obj;
  schema: Obj | null;
  project: Obj | null;
  view: Obj | null;
  episode: number;
  frame: number;
  range: [number, number];
  fps: number;
  frameCount: number;
  disabled?: boolean;
  api: (path: string, body?: any, signal?: AbortSignal) => Promise<any>;
  onChanged: () => void;
  notify: (message: string) => void;
  onError: (error: any) => void;
  onLocate: (frame: number) => void;
  onSelectRange: (start: number, end: number, frame: number) => void;
  taskSettings: Obj;
  onTaskSettingsChange: (value: Obj) => void;
  settings: Obj;
  onSettingsChange: (value: Obj) => void;
};

const identity = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
const coordinateDefaults = {
  verified: false,
  unit: "m",
  frame: "robot_base",
  transform: identity,
  position_noise: 0,
  angle_noise: 0,
};
const eventDefaults = {
  verified: false,
  unit: "",
  closed_threshold: 0.1,
  open_threshold: 0.8,
  closed_below: true,
  min_duration: 0.1,
};
const semanticLabels: Record<string, string> = {
  none: "无显著位移",
  positive: "正方向",
  negative: "负方向",
  keep: "无显著调整",
  adjust: "调整姿态",
  unknown: "无法判断",
};
const reasonLabels: Record<string, string> = {
  missing_field: "方案未定义此字段",
  field_missing: "方案未定义此字段",
  absent: "方案未定义此字段",
  unsupported: "字段类型或证据模式不支持",
  unsupported_field: "字段类型或证据模式不支持",
  unsupported_scope: "字段必须按臂独立",
  invalid_mapping: "请完成类别映射",
  mapping_invalid: "请完成类别映射",
  inapplicable: "当前门控关闭",
  not_applicable: "当前门控关闭",
  undetermined: "门控依赖缺失或无法确定",
  unapproved: "需要连续 approved 示教动作",
  window_too_short: "合格未来窗口不足 0.1 秒",
  missing_pose: "缺少所需实测末端位姿",
  invalid_pose: "实测末端位姿无效",
  invalid_calibration: "请核实当前臂的坐标标定",
  existing_label: "保留现有标签或单帧覆盖",
  existing: "保留现有标签或单帧覆盖",
  event_boundary: "遇已确认的同臂事件",
  dependency_changed: "门控依赖值发生变化",
  gate_changed: "门控依赖值发生变化",
  source_boundary: "源帧不连续",
  episode_end: "到达 episode 末尾",
  horizon: "达到 0.25 秒上限",
  confirmed_event: "遇已确认的同臂事件",
  gate_closed: "当前门控关闭",
  gate_context_changed: "门控依赖值发生变化",
  invalid_timestamp: "时间戳无效",
  suggestion: "建议仍需人工复核",
  max_horizon: "达到 0.25 秒上限",
  ready: "可计算",
  eligible: "可计算",
};
export function canonicalAssistMessage(
  message: string,
  values: readonly unknown[] = [],
) {
  return message.replace(/\{(\d+)\}/g, (_, index: string) =>
    String(values[Number(index)]),
  );
}
export function eventTypeLabel(type: string) {
  return type === "open" ? tr("打开") : type === "closed" ? tr("闭合") : type;
}
const explain = (code?: string, reason?: string) =>
  tr(reasonLabels[code || ""] || reason || "尚未检查");
function parseDetails(event: Obj) {
  if (typeof event.details === "object") return event.details || {};
  try {
    return JSON.parse(event.details || "{}");
  } catch {
    return {};
  }
}
function Scope({ children }: { children: React.ReactNode }) {
  return (
    <div className="assist-scope">
      <span>{tr("本次作用范围")}</span>
      <strong>{children}</strong>
    </div>
  );
}
function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  const control =
    React.isValidElement<Obj>(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type)
      ? React.cloneElement(children, {
          "aria-label": children.props["aria-label"] || tr(label),
        })
      : children;
  return (
    <label className="assist-field">
      <span>{tr(label)}</span>
      {control}
      {help && <small>{tr(help)}</small>}
    </label>
  );
}
function Header({
  number,
  title,
  description,
}: {
  number: string;
  title: string;
  description: string;
}) {
  return (
    <header className="assist-card-header">
      <span className="assist-step">{number}</span>
      <div>
        <h2>{tr(title)}</h2>
        <p>{tr(description)}</p>
      </div>
    </header>
  );
}

export default function BehaviorCalibration(props: BehaviorCalibrationProps) {
  useI18n();
  const {
    datasetKey,
    dataset,
    view,
    episode,
    frame,
    range,
    fps,
    frameCount,
    api,
    onChanged,
    notify,
    onError,
    onLocate,
    onSelectRange,
    settings,
    onSettingsChange,
    taskSettings,
  } = props;
  const schema: Obj = props.schema || { version: 0, fields: [] };
  const project: Obj = props.project || {};
  const [arm, setArm] = useState("left");
  const [channels, setChannels] = useState<Obj[]>([]);
  const [eventScope, setEventScope] = useState("episode");
  const [adviserScope, setAdviserScope] = useState("frame");
  const [eventPreview, setEventPreview] = useState<Obj | null>(null);
  const [adviserPreview, setAdviserPreview] = useState<Obj | null>(null);
  const [capability, setCapability] = useState<Obj | null>(null);
  const [working, setBusy] = useState(false);
  const busy = working || !!props.disabled;
  const [checking, setChecking] = useState(false);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [eventFilter, setEventFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [localMessage, setLocalMessage] = useState("");
  const scopeKey = datasetKey + ":" + arm;
  const profile: Obj = settings.profiles?.[scopeKey] || {};
  const coordinate: Obj = {
    ...coordinateDefaults,
    feature: "observation.ee_pose_" + arm,
    ...profile.coordinate,
  };
  const eventConfig: Obj = { ...eventDefaults, ...profile.event };
  const mappings =
    profile.mapping_schema_version === schema.version
      ? profile.mappings || {}
      : {};
  const task: Obj = settings.datasets?.[datasetKey]?.taskSettings || {};
  const [matrixDraft, setMatrixDraft] = useState(
    JSON.stringify(coordinate.transform),
  );
  const revision = view?.revision ?? 0;
  const aStart = adviserScope === "frame" ? frame : range[0];
  const aEnd = adviserScope === "frame" ? frame + 1 : range[1];
  const eStart =
    eventScope === "episode" ? 0 : eventScope === "frame" ? frame : range[0];
  const eEnd =
    eventScope === "episode"
      ? frameCount
      : eventScope === "frame"
        ? frame + 1
        : range[1];
  const activeChannel =
    channels.find(
      (c) => c.feature === profile.feature && c.dimension === profile.dimension,
    ) ||
    channels.find((c) => c.arm_hint === arm && /gripper|夹爪/i.test(c.name)) ||
    channels.find((c) => /gripper|夹爪/i.test(c.name)) ||
    channels[0];
  const feature = activeChannel?.feature || "";
  const dimension = activeChannel?.dimension ?? 0;
  const role = profile.role || activeChannel?.role || "unspecified";
  const eventPayload = {
    dataset: datasetKey,
    episode,
    arm,
    feature,
    dimension,
    role,
    calibration: eventConfig,
    start: eStart,
    end: eEnd,
  };
  const adviserPayload = {
    dataset: datasetKey,
    episode,
    arm,
    calibration: coordinate,
    mappings,
    start: aStart,
    end: aEnd,
  };
  const eventSignature = JSON.stringify([
    datasetKey,
    episode,
    arm,
    schema.version,
    revision,
    feature,
    dimension,
    role,
    eventConfig,
    eStart,
    eEnd,
  ]);
  const adviserSignature = JSON.stringify([
    datasetKey,
    episode,
    arm,
    schema.version,
    revision,
    coordinate,
    mappings,
    aStart,
    aEnd,
  ]);
  const eventVersion = useRef(eventSignature);
  const adviserVersion = useRef(adviserSignature);
  eventVersion.current = eventSignature;
  adviserVersion.current = adviserSignature;
  const sourceOffset =
    (view?.source_key?.frame ?? frame) - (view?.frame ?? frame);
  const sourceFrame = frame + sourceOffset;
  const visibleEvents = (view?.events || []).filter(
    (ev: Obj) => eventFilter === "all" || ev.arm === eventFilter,
  );
  const validRange = (start: number, end: number) =>
    Number.isInteger(start) &&
    Number.isInteger(end) &&
    start >= 0 &&
    start < end &&
    end <= frameCount;
  const fieldStates: Obj[] = capability?.fields || [];
  const adviserFields: Obj[] = (schema.fields || []).filter((f: Obj) =>
    ["adviser.x", "adviser.y", "adviser.z", "adviser.orientation"].includes(
      f.field_id,
    ),
  );
  const poseFeatures = Object.entries(dataset.info.features)
    .filter(
      ([key, value]) =>
        key === "observation.ee_pose_" + arm && (value as Obj).shape?.[0] === 7,
    )
    .map(([key]) => key);

  function patchProfile(patch: Obj) {
    onSettingsChange({
      ...settings,
      profiles: { ...settings.profiles, [scopeKey]: { ...profile, ...patch } },
    });
  }
  function patchCoordinate(patch: Obj, verify = false) {
    patchProfile({
      coordinate: {
        ...coordinate,
        ...patch,
        verified: verify,
        binding: verify
          ? {
              dataset: datasetKey,
              arm,
              feature: patch.feature || coordinate.feature,
            }
          : undefined,
      },
    });
  }
  function patchEvent(patch: Obj, verify = false) {
    patchProfile({
      event: {
        ...eventConfig,
        ...patch,
        verified: verify,
        binding: verify
          ? { dataset: datasetKey, arm, feature, dimension }
          : undefined,
      },
    });
  }
  function patchTask(patch: Obj) {
    onSettingsChange({
      ...settings,
      datasets: {
        ...settings.datasets,
        [datasetKey]: {
          ...settings.datasets?.[datasetKey],
          taskSettings: { ...task, ...patch },
        },
      },
    });
  }
  async function perform(
    operation: () => Promise<any>,
    success?: (result: any) => string,
  ) {
    setBusy(true);
    setLocalMessage("");
    try {
      const result = await operation();
      if (success) {
        const message = success(result);
        setLocalMessage(message);
        notify(message);
      }
      onChanged();
      return result;
    } catch (error) {
      onError(error);
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    setMatrixDraft(JSON.stringify(coordinate.transform));
  }, [scopeKey, JSON.stringify(coordinate.transform)]);
  useEffect(() => {
    const abort = new AbortController();
    setChannels([]);
    api("/event-channels/" + datasetKey, undefined, abort.signal)
      .then((result) => setChannels(result.channels || []))
      .catch((error) => {
        if (error.name !== "AbortError") onError(error);
      });
    return () => abort.abort();
  }, [datasetKey]);
  useEffect(() => {
    setEventPreview(null);
  }, [eventSignature]);
  useEffect(() => {
    setAdviserPreview(null);
    setPage(0);
  }, [adviserSignature]);
  useEffect(() => {
    const abort = new AbortController();
    setChecking(true);
    setCapability(null);
    api(
      "/adviser/capabilities",
      {
        dataset: datasetKey,
        episode,
        frame,
        arm,
        calibration: coordinate,
        mappings,
      },
      abort.signal,
    )
      .then((result) => {
        if (!abort.signal.aborted) setCapability(result);
      })
      .catch((error) => {
        if (error.name !== "AbortError") onError(error);
      })
      .finally(() => {
        if (!abort.signal.aborted) setChecking(false);
      });
    return () => abort.abort();
  }, [
    datasetKey,
    episode,
    frame,
    arm,
    schema.version,
    revision,
    JSON.stringify(coordinate),
    JSON.stringify(mappings),
  ]);

  function chooseChannel(featureKey: string, dim?: number) {
    const candidates = channels.filter((c) => c.feature === featureKey);
    const chosen =
      candidates.find((c) => c.dimension === dim) ||
      candidates.find((c) => c.arm_hint === arm && /gripper/i.test(c.name)) ||
      candidates[0];
    if (chosen)
      patchProfile({
        feature: chosen.feature,
        dimension: chosen.dimension,
        role: chosen.role,
        event: { ...eventConfig, verified: false, binding: undefined },
      });
  }
  const eventBindingValid =
    eventConfig.verified &&
    eventConfig.binding?.dataset === datasetKey &&
    eventConfig.binding?.arm === arm &&
    eventConfig.binding?.feature === feature &&
    eventConfig.binding?.dimension === dimension;
  const coordinateBindingValid =
    coordinate.verified &&
    coordinate.binding?.dataset === datasetKey &&
    coordinate.binding?.arm === arm &&
    coordinate.binding?.feature === coordinate.feature;
  const rangeLabel = (start: number, end: number) =>
    "[" + start + ", " + end + ") · " + (end - start) + " " + tr("帧");
  const statusText = (status: string) =>
    tr(
      (
        {
          confirmed: "已确认",
          suggestion: "待复核候选",
          rejected: "已拒绝",
          approved: "认可示教",
          unreviewed: "未审核",
          missing: "缺失",
          skipped: "跳过",
        } as Obj
      )[status] || status,
    );
  const eventOrigin = (ev: Obj) => {
    const d = parseDetails(ev);
    return tr(
      d.role === "command" || d.feature === "action"
        ? "命令事件"
        : d.role === "feedback" || d.feature === "observation.state"
          ? "实测反馈事件"
          : d.source === "human"
            ? "人工事件"
            : "其他来源事件",
    );
  };
  const roleLabel = (value: string) =>
    tr(
      value === "feedback"
        ? "实测反馈"
        : value === "command"
          ? "动作命令"
          : "尚未指定",
    );
  const adviserRows: Obj[] = adviserPreview?.results || [];
  const canCompute =
    !!capability?.supported &&
    !!capability?.calibration_valid &&
    validRange(aStart, aEnd) &&
    adviserFields.some((field: Obj) => field.applicable_when !== false) &&
    (adviserScope !== "frame" || !!capability?.available);

  return (
    <section className="behavior-calibration">
      <div className="assist-hero">
        <div>
          <span className="eyebrow">BEHAVIOR & CALIBRATION</span>
          <h1>{tr("行为与校准")}</h1>
          <p>{tr("核实示教、定位事件，再为当前方案生成可追溯的标签草稿。")}</p>
        </div>
        <div className="assist-context">
          <span>
            EP {episode} · {frameCount} {tr("帧")} · {fps} FPS
          </span>
          <strong>
            {tr("当前帧")} #{frame}{" "}
            <small>
              {tr("源帧")} #{sourceFrame}
            </small>
          </strong>
          <span>
            {tr("当前选区")} {rangeLabel(range[0], range[1])}
          </span>
        </div>
      </div>
      <div className="assist-workflow" aria-label={tr("建议操作顺序")}>
        <span>01 · {tr("保存门控标签")}</span>
        <span>02 · {tr("认可可信示教")}</span>
        <span>03 · {tr("核实通道与标定")}</span>
        <span>04 · {tr("预览、接受并人工复核")}</span>
      </div>
      <div className="assist-toolbar">
        <nav>
          <a href="#assist-quality">{tr("示教动作质量")}</a>
          <a href="#assist-events">{tr("事件与候选")}</a>
          <a href="#assist-adviser">{tr("adviser 建议")}</a>
          <a href="#assist-protocol">{tr("任务协议")}</a>
        </nav>
        <label>
          {tr("当前操作手臂")}
          <select
            aria-label={tr("当前操作手臂")}
            value={arm}
            disabled={busy}
            onChange={(e) => {
              setArm(e.target.value);
              setDeleteId(null);
            }}
          >
            <option value="left">{tr("左臂")}</option>
            <option value="right">{tr("右臂")}</option>
          </select>
        </label>
      </div>
      {localMessage && (
        <div className="assist-success" role="status">
          {tr(localMessage)}
        </div>
      )}

      <article id="assist-quality" className="assist-card">
        <Header
          number="01"
          title="示教动作质量"
          description="用于后续 VLA 筛选及自动 adviser 候选；与语义标签审核分别保存。"
        />
        <Scope>
          EP {episode} · {tr("当前选区")} {rangeLabel(range[0], range[1])}
        </Scope>
        <p className="assist-help">
          {tr("示教动作质量作用于双臂共享的动作向量，不随当前操作手臂变化。")}
        </p>
        <div className="assist-quality-layout">
          <div>
            <p className="assist-help">
              {tr(
                "approved 表示这段示教适合模仿。自动 adviser 只使用连续 approved 动作，未审核与 rejected 片段会截断计算窗口。",
              )}
            </p>
            <p className="assist-help">
              {tr(
                "这里不会审核语义标签，也不会直接过滤当前 DSE 训练样本。后续 VLA 需读取审核记录实施筛选。",
              )}
            </p>
          </div>
          <div>
            <Field
              label="审核原因"
              help="写明错误操作、恢复动作或认可依据，便于后续追溯。"
            >
              <input
                value={task.review_reason || ""}
                onChange={(e) => patchTask({ review_reason: e.target.value })}
              />
            </Field>
            <div className="assist-actions">
              {["approved", "rejected", "unreviewed"].map((status) => (
                <button
                  key={status}
                  className={status === "approved" ? "primary" : ""}
                  disabled={busy || !validRange(range[0], range[1])}
                  onClick={() =>
                    perform(
                      () =>
                        api("/reviews", {
                          dataset: datasetKey,
                          episode,
                          start: range[0],
                          end: range[1],
                          status,
                          reason: task.review_reason || "",
                        }),
                      () =>
                        canonicalAssistMessage(
                          "示教动作质量已保存，可通过撤销恢复。",
                        ),
                    )
                  }
                >
                  {status === "rejected" ? tr("拒绝示教") : statusText(status)}{" "}
                  <code>{status}</code>
                </button>
              ))}
            </div>
          </div>
        </div>
      </article>

      <article id="assist-events" className="assist-card">
        <Header
          number="02"
          title="事件与候选"
          description="记录夹爪开合的变化边界。事件本身不会自动填写 holding、readiness 或其他状态标签。"
        />
        <div className="assist-two-columns">
          <section className="assist-subsection">
            <h3>{tr("夹爪事件候选")}</h3>
            <p className="assist-help">
              {tr(
                "先用画面与曲线核对明显打开、闭合时的读数，再设置阈值。预填阈值没有经过你的数据标定。",
              )}
            </p>
            <div className="assist-fields">
              <Field label="数据来源">
                <select
                  value={feature}
                  disabled={busy || !channels.length}
                  onChange={(e) => chooseChannel(e.target.value)}
                >
                  {Array.from(new Set(channels.map((c) => c.feature))).map(
                    (key) => (
                      <option key={key} value={key}>
                        {key}
                      </option>
                    ),
                  )}
                </select>
              </Field>
              <Field
                label="具名通道"
                help="仅分析所选通道；切换手臂后请再次核对。"
              >
                <select
                  value={dimension}
                  disabled={busy || !channels.length}
                  onChange={(e) => chooseChannel(feature, +e.target.value)}
                >
                  {channels
                    .filter((c) => c.feature === feature)
                    .map((c) => (
                      <option key={c.dimension} value={c.dimension}>
                        {c.dimension} · {c.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field
                label="来源属性"
                help="命令变化不证明夹爪已经实际完成开合。"
              >
                <select
                  value={role}
                  disabled={busy}
                  onChange={(e) =>
                    patchProfile({
                      role: e.target.value,
                      event: {
                        ...eventConfig,
                        verified: false,
                        binding: undefined,
                      },
                    })
                  }
                >
                  <option value="unspecified">{tr("尚未指定")}</option>
                  <option value="feedback">{tr("实测反馈")}</option>
                  <option value="command">{tr("动作命令")}</option>
                </select>
              </Field>
              <Field
                label="读数单位"
                help="只作记录，不换算数据；阈值必须与原始读数同单位。"
              >
                <input
                  value={eventConfig.unit}
                  placeholder={tr("例如 mm、rad 或归一化值")}
                  onChange={(e) => patchEvent({ unit: e.target.value })}
                />
              </Field>
              <Field label="闭合阈值">
                <input
                  type="number"
                  step="any"
                  value={eventConfig.closed_threshold}
                  onChange={(e) =>
                    patchEvent({ closed_threshold: +e.target.value })
                  }
                />
              </Field>
              <Field label="打开阈值">
                <input
                  type="number"
                  step="any"
                  value={eventConfig.open_threshold}
                  onChange={(e) =>
                    patchEvent({ open_threshold: +e.target.value })
                  }
                />
              </Field>
              <Field
                label="最短持续秒"
                help="读数连续满足阈值达到此时长才认可变化；短暂跳动会被忽略。"
              >
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={eventConfig.min_duration}
                  onChange={(e) =>
                    patchEvent({ min_duration: +e.target.value })
                  }
                />
              </Field>
              <Field label="计算范围">
                <select
                  value={eventScope}
                  disabled={busy}
                  onChange={(e) => setEventScope(e.target.value)}
                >
                  <option value="episode">{tr("整个当前 episode")}</option>
                  <option value="range">{tr("当前选区")}</option>
                  <option value="frame">{tr("当前帧")}</option>
                </select>
              </Field>
            </div>
            <label className="assist-check">
              <input
                type="checkbox"
                checked={eventConfig.closed_below}
                onChange={(e) => patchEvent({ closed_below: e.target.checked })}
              />
              {tr("较小数值为闭合")}
            </label>
            <p className="assist-help">
              {tr(
                eventConfig.closed_below
                  ? "读数≤闭合阈值判闭合，读数≥打开阈值判打开；中间滞回区不触发切换。"
                  : "读数≥闭合阈值判闭合，读数≤打开阈值判打开；中间滞回区不触发切换。",
              )}
            </p>
            <Scope>
              {arm === "left" ? tr("左臂") : tr("右臂")} · EP {episode} ·{" "}
              {rangeLabel(eStart, eEnd)}
            </Scope>
            <p className="assist-help">
              {tr(
                "程序读取 episode 前序读数建立稳定状态，只保留所选范围内的变化；事件落点为连续满足阈值的起始帧。",
              )}
            </p>
            <label className="assist-check">
              <input
                type="checkbox"
                checked={!!eventBindingValid}
                disabled={!feature || role === "unspecified"}
                onChange={(e) => patchEvent({}, e.target.checked)}
              />
              {tr("已核对当前臂的通道、单位、方向和阈值")}
            </label>
            <div className="assist-actions">
              <button
                className="primary"
                disabled={
                  busy ||
                  !eventBindingValid ||
                  !eventConfig.unit ||
                  !validRange(eStart, eEnd)
                }
                onClick={async () => {
                  const version = eventVersion.current;
                  setBusy(true);
                  try {
                    const result = await api("/events/preview", eventPayload);
                    if (version === eventVersion.current)
                      setEventPreview(result);
                  } catch (error) {
                    onError(error);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {tr("预览事件候选")}
              </button>
            </div>
            {eventPreview && (
              <div className="assist-preview">
                <strong>
                  {tr("候选 {0} 项，可新增 {1} 项，重复 {2} 项", [
                    eventPreview.summary?.candidates || 0,
                    eventPreview.summary?.savable || 0,
                    eventPreview.summary?.duplicates || 0,
                  ])}
                </strong>
                <div className="assist-event-chips">
                  {(eventPreview.candidates || [])
                    .slice(0, 16)
                    .map((ev: Obj, i: number) => (
                      <span key={i}>
                        #{ev.frame} {eventTypeLabel(ev.type)}
                      </span>
                    ))}
                </div>
                <button
                  disabled={busy || !eventPreview.summary?.savable}
                  onClick={() =>
                    perform(
                      () =>
                        api("/events/apply", {
                          ...eventPayload,
                          plan_id: eventPreview.plan_id,
                        }),
                      (r) =>
                        canonicalAssistMessage(
                          "新增 {0} 项事件候选，跳过 {1} 项；仍需人工确认。",
                          [r.saved, r.skipped],
                        ),
                    )
                  }
                >
                  {tr("保存候选，稍后复核")}
                </button>
              </div>
            )}
          </section>
          <section className="assist-subsection">
            <h3>{tr("手动事件与复核")}</h3>
            <Scope>
              {arm === "left" ? tr("左臂") : tr("右臂")} · {tr("当前帧")} #
              {frame} · {tr("源帧")} #{sourceFrame}
            </Scope>
            <p className="assist-help">
              {tr(
                "在当前帧新增人工事件会直接设为 confirmed。确认候选只核实事件，不认可示教动作、不审核语义标签。",
              )}
            </p>
            <div className="assist-actions">
              {["open", "closed"].map((type) => (
                <button
                  key={type}
                  disabled={busy}
                  onClick={() =>
                    perform(
                      () =>
                        api("/events", {
                          dataset: datasetKey,
                          episode,
                          frame,
                          arm,
                          type,
                        }),
                      () =>
                        canonicalAssistMessage(
                          "人工事件已保存，可通过撤销恢复。",
                        ),
                    )
                  }
                >
                  + {tr(type === "open" ? "打开事件" : "闭合事件")} #{frame}
                </button>
              ))}
            </div>
            <p className="assist-help">
              {tr(
                "已确认事件附近约 ±0.25 秒会进入关键采样池；选择附近 ±0.5 秒只是设置选区，不自动保留训练片段。",
              )}
            </p>
            <div className="assist-list-heading">
              <strong>
                {tr("当前来源的事件")} · {visibleEvents.length}
              </strong>
              <select
                aria-label={tr("筛选事件手臂")}
                value={eventFilter}
                onChange={(e) => setEventFilter(e.target.value)}
              >
                <option value="all">{tr("全部手臂")}</option>
                <option value="left">{tr("左臂")}</option>
                <option value="right">{tr("右臂")}</option>
              </select>
            </div>
            {!visibleEvents.length && (
              <div className="assist-empty">
                {tr("暂无事件。可以手动新增，或先预览夹爪事件候选。")}
              </div>
            )}
            <div className="assist-event-list">
              {visibleEvents.map((ev: Obj) => {
                const local = ev.frame - sourceOffset;
                const available = local >= 0 && local < frameCount;
                return (
                  <div className="assist-event" key={ev.id}>
                    <div className="assist-event-heading">
                      <strong>
                        {tr("源帧")} #{ev.frame} ·{" "}
                        {tr(ev.arm === "left" ? "左臂" : "右臂")} ·{" "}
                        {eventTypeLabel(ev.type)}
                      </strong>
                      <span className={"assist-status " + ev.status}>
                        {statusText(ev.status)}
                      </span>
                    </div>
                    <span className="assist-help">
                      {eventOrigin(ev)} ·{" "}
                      {available
                        ? tr("当前片段帧 #{0}", [local])
                        : tr("位于当前片段之外")}
                    </span>
                    <div className="assist-actions compact">
                      <button
                        disabled={busy || !available}
                        onClick={() => onLocate(local)}
                      >
                        {tr("定位")}
                      </button>
                      <button
                        disabled={busy || !available}
                        onClick={() =>
                          onSelectRange(
                            Math.max(0, local - Math.round(fps * 0.5)),
                            Math.min(
                              frameCount,
                              local + Math.round(fps * 0.5) + 1,
                            ),
                            local,
                          )
                        }
                      >
                        {tr("选择附近 ±0.5秒")}
                      </button>
                      <button
                        disabled={
                          busy || ev.status === "confirmed" || !available
                        }
                        onClick={() =>
                          perform(
                            () =>
                              api("/events", {
                                dataset: datasetKey,
                                episode,
                                id: ev.id,
                                frame: local,
                                arm: ev.arm,
                                type: ev.type,
                                status: "confirmed",
                              }),
                            () => canonicalAssistMessage("事件已确认。"),
                          )
                        }
                      >
                        {tr("确认事件")}
                      </button>
                      <button
                        disabled={busy || ev.status === "rejected"}
                        onClick={() =>
                          perform(
                            () =>
                              api("/events/reject", {
                                dataset: datasetKey,
                                episode,
                                id: ev.id,
                              }),
                            () => canonicalAssistMessage("事件已拒绝。"),
                          )
                        }
                      >
                        {tr("拒绝")}
                      </button>
                      <button
                        disabled={busy || ev.frame === sourceFrame}
                        onClick={() =>
                          perform(
                            () =>
                              api("/events", {
                                dataset: datasetKey,
                                episode,
                                id: ev.id,
                                frame,
                                arm: ev.arm,
                                type: ev.type,
                                status: "confirmed",
                              }),
                            () =>
                              canonicalAssistMessage(
                                "事件已移到当前帧并确认。",
                              ),
                          )
                        }
                      >
                        {tr("移到当前帧并确认")}
                      </button>
                      <button
                        className="assist-danger"
                        disabled={busy}
                        onClick={() => setDeleteId(ev.id)}
                      >
                        {tr("删除")}
                      </button>
                    </div>
                    {deleteId === ev.id && (
                      <div className="assist-delete">
                        <span>{tr("删除这条事件？可通过撤销恢复。")}</span>
                        <button
                          className="assist-danger"
                          disabled={busy}
                          onClick={async () => {
                            await perform(
                              () =>
                                api("/events/delete", {
                                  dataset: datasetKey,
                                  episode,
                                  id: ev.id,
                                }),
                              () =>
                                canonicalAssistMessage(
                                  "事件已删除，可通过撤销恢复。",
                                ),
                            );
                            setDeleteId(null);
                          }}
                        >
                          {tr("确认删除")}
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => setDeleteId(null)}
                        >
                          {tr("取消")}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </article>

      <article id="assist-adviser" className="assist-card">
        <Header
          number="03"
          title="adviser 方向建议"
          description="根据已认可示教中的实测末端运动，为当前方案适用的 adviser 字段生成草稿；不是纠偏目标，也不证明已经对准。"
        />
        <div className="assist-prerequisites">
          <strong>{tr("计算前需要")}</strong>
          <span>{tr("已保存且明确开启的字段门控")}</span>
          <span>{tr("连续 0.1–0.25 秒 approved 动作")}</span>
          <span>{tr("当前臂的实测位姿和已核实标定")}</span>
          <span>{tr("有效类别映射")}</span>
        </div>
        <div className="assist-adviser-top">
          <Field label="目标标签范围">
            <select
              value={adviserScope}
              disabled={busy}
              onChange={(e) => setAdviserScope(e.target.value)}
            >
              <option value="frame">{tr("当前帧")}</option>
              <option value="range">{tr("当前选区逐帧计算")}</option>
            </select>
          </Field>
          <Scope>
            {arm === "left" ? tr("左臂") : tr("右臂")} · EP {episode} ·{" "}
            {rangeLabel(aStart, aEnd)}
          </Scope>
        </div>
        <p className="assist-help">
          {tr(
            "每个目标帧分别读取其后最多 0.25 秒合格证据，可超出选区末端；只保存目标范围内的标签，不复制整段答案。",
          )}
        </p>
        <div className="assist-two-columns adviser">
          <section className="assist-subsection">
            <div className="assist-list-heading">
              <h3>{tr("当前方案的字段条件")}</h3>
              <span className="assist-help">
                v{schema.version} ·{" "}
                {checking ? tr("正在检查…") : tr("按当前已保存标签计算")}
              </span>
            </div>
            {!adviserFields.length && (
              <div className="assist-empty">
                {tr(
                  "此方案没有 adviser.x / y / z / orientation 字段，无需使用方向建议。",
                )}
              </div>
            )}
            {fieldStates
              .filter(
                (f) =>
                  f.exists ||
                  adviserFields.some((field) => field.field_id === f.field_id),
              )
              .map((f) => (
                <div className="assist-capability" key={f.field_id}>
                  <div>
                    <strong>{f.field_id}</strong>
                    <span
                      className={
                        "assist-status " +
                        (f.supported && f.applicability === "applicable"
                          ? "confirmed"
                          : "suggestion")
                      }
                    >
                      {!f.supported
                        ? tr("不支持")
                        : f.applicability === "applicable"
                          ? tr("适用")
                          : f.applicability === "not_applicable"
                            ? tr("不适用")
                            : tr("适用性未知")}
                    </span>
                    <span className="assist-help">
                      {f.required === true
                        ? tr("必填")
                        : f.required === false
                          ? tr("非必填")
                          : tr("必填条件未知")}
                    </span>
                  </div>
                  <p>{explain(f.code, f.reason)}</p>
                  {f.code === "invalid_mapping" && f.reason && (
                    <p>{tr(f.reason)}</p>
                  )}
                </div>
              ))}
            {adviserFields.some(
              (field: Obj) => field.applicable_when !== false,
            ) && (
              <>
                <details
                  className="assist-details"
                  open={!coordinateBindingValid}
                >
                  <summary>
                    {tr("末端反馈与物理标定")} ·{" "}
                    {coordinateBindingValid
                      ? tr("已核实当前上下文")
                      : tr("需要核实")}
                  </summary>
                  <p className="assist-help">
                    {tr(
                      "原始位置必须已使用米；此处不自动换算。目标 robot_base：+X 前、+Y 机器人左、+Z 上。姿态顺序为 qx/qy/qz/qw。",
                    )}
                  </p>
                  <Field label="当前臂实测末端字段">
                    <select
                      value={coordinate.feature}
                      onChange={(e) =>
                        patchCoordinate({ feature: e.target.value })
                      }
                    >
                      {!poseFeatures.includes(coordinate.feature) && (
                        <option value={coordinate.feature}>
                          {coordinate.feature} · {tr("缺失")}
                        </option>
                      )}
                      {poseFeatures.map((key) => (
                        <option value={key} key={key}>
                          {key}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field
                    label="到规范基座系的 4×4 变换"
                    help="只有核实来源坐标系与机器人基座系一致时，才能使用单位矩阵。"
                  >
                    <textarea
                      rows={4}
                      value={matrixDraft}
                      onChange={(e) => {
                        setMatrixDraft(e.target.value);
                        if (coordinate.verified) patchCoordinate({});
                      }}
                      onBlur={() => {
                        try {
                          const matrix = JSON.parse(matrixDraft);
                          if (
                            JSON.stringify(matrix) !==
                            JSON.stringify(coordinate.transform)
                          )
                            patchCoordinate({ transform: matrix });
                        } catch {
                          onError(Error(tr("变换矩阵需要有效 JSON")));
                        }
                      }}
                    />
                  </Field>
                  <div className="assist-fields">
                    <Field label="静止位置噪声（米）">
                      <input
                        type="number"
                        min="0"
                        step="0.001"
                        value={coordinate.position_noise}
                        onChange={(e) =>
                          patchCoordinate({ position_noise: +e.target.value })
                        }
                      />
                    </Field>
                    <Field label="静止角噪声（弧度）">
                      <input
                        type="number"
                        min="0"
                        step="0.001"
                        value={coordinate.angle_noise}
                        onChange={(e) =>
                          patchCoordinate({ angle_noise: +e.target.value })
                        }
                      />
                    </Field>
                  </div>
                  <p className="assist-help">
                    {tr(
                      "位移死区取 5 mm 与三倍位置噪声的较大值；姿态阈值取 5° 与三倍角噪声的较大值。显著往返运动标为 unknown。",
                    )}
                  </p>
                  <label className="assist-check">
                    <input
                      type="checkbox"
                      checked={!!coordinateBindingValid}
                      onChange={(e) => {
                        try {
                          const transform = JSON.parse(matrixDraft);
                          patchCoordinate({ transform }, e.target.checked);
                        } catch {
                          onError(Error(tr("变换矩阵需要有效 JSON")));
                        }
                      }}
                    />
                    {tr("已核实当前数据集、当前臂的米制单位及变换")}
                  </label>
                  {capability && !capability.calibration_valid && (
                    <p className="assist-warning">
                      {tr(
                        capability.calibration_reason ||
                          "请核实当前臂的坐标标定",
                      )}
                    </p>
                  )}
                </details>
                <details className="assist-details">
                  <summary>{tr("类别语义映射")}</summary>
                  <p className="assist-help">
                    {tr(
                      "标准 key 可自动匹配；自定义 key 请明确选择。unknown 始终使用当前字段的 unknown_id，不按数字或类别顺序猜测。",
                    )}
                  </p>
                  {adviserFields
                    .filter(
                      (f) =>
                        f.scope === "each_arm" && f.evidence === "behavior",
                    )
                    .map((field) => {
                      const roles =
                        field.field_id === "adviser.orientation"
                          ? ["keep", "adjust"]
                          : ["none", "positive", "negative"];
                      const current =
                        mappings[field.field_id] ||
                        fieldStates.find((f) => f.field_id === field.field_id)
                          ?.mapping ||
                        {};
                      return (
                        <fieldset
                          className="assist-mapping"
                          key={field.field_id}
                        >
                          <legend>{field.field_id}</legend>
                          <div className="assist-fields">
                            {roles.map((semantic) => {
                              const inferred =
                                current[semantic] ??
                                field.classes.find(
                                  (c: Obj) =>
                                    c.key === semantic && !c.deprecated,
                                )?.id ??
                                "";
                              return (
                                <Field
                                  key={semantic}
                                  label={semanticLabels[semantic] || semantic}
                                >
                                  <select
                                    value={inferred}
                                    onChange={(e) => {
                                      const complete = Object.fromEntries(
                                        roles.map((role) => [
                                          role,
                                          current[role] ??
                                            field.classes.find(
                                              (c: Obj) =>
                                                c.key === role && !c.deprecated,
                                            )?.id,
                                        ]),
                                      );
                                      patchProfile({
                                        mapping_schema_version: schema.version,
                                        mapping_schema_hash:
                                          capability?.schema_hash,
                                        mappings: {
                                          ...mappings,
                                          [field.field_id]: {
                                            ...complete,
                                            [semantic]:
                                              e.target.value === ""
                                                ? null
                                                : +e.target.value,
                                            unknown: field.unknown_id,
                                          },
                                        },
                                      });
                                    }}
                                  >
                                    <option value="">{tr("请选择类别")}</option>
                                    {field.classes
                                      .filter(
                                        (c: Obj) =>
                                          !c.deprecated &&
                                          c.id !== field.unknown_id,
                                      )
                                      .map((c: Obj) => (
                                        <option key={c.id} value={c.id}>
                                          {c.id} · {c.key} · {categoryLabel(c)}
                                        </option>
                                      ))}
                                  </select>
                                </Field>
                              );
                            })}
                          </div>
                          <small>unknown → {field.unknown_id}</small>
                        </fieldset>
                      );
                    })}
                </details>
              </>
            )}
          </section>
          <section className="assist-subsection">
            <h3>{tr("预览与接受")}</h3>
            <p className="assist-help">
              {tr(
                "预览不写标签。接受只补真正缺失项，保留人工值、已审核值与单帧 missing 覆盖；一次接受可一次撤销。",
              )}
            </p>
            {capability && !capability.approved && (
              <div className="assist-warning">
                {tr(
                  "当前帧尚无 approved 动作。请先在示教动作质量中审核可信范围；选区中的各帧会分别检查。",
                )}
              </div>
            )}
            <div className="assist-actions">
              <button
                className="primary"
                disabled={
                  busy || checking || !canCompute || !adviserFields.length
                }
                onClick={async () => {
                  const version = adviserVersion.current;
                  setBusy(true);
                  try {
                    const result = await api(
                      "/adviser/preview",
                      adviserPayload,
                    );
                    if (version === adviserVersion.current) {
                      setAdviserPreview(result);
                      setPage(0);
                    }
                  } catch (error) {
                    onError(error);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? tr("处理中…") : tr("预览 adviser 建议")}
              </button>
              <button
                disabled={busy || !adviserPreview?.summary?.savable}
                onClick={() =>
                  perform(
                    () =>
                      api("/adviser/apply", {
                        ...adviserPayload,
                        plan_id: adviserPreview?.plan_id,
                      }),
                    (r) =>
                      canonicalAssistMessage(
                        "已保存 {0} 项草稿，跳过 {1} 项；仍需人工审核。",
                        [r.saved, r.skipped],
                      ),
                  )
                }
              >
                {tr("接受缺失项为草稿")}
              </button>
            </div>
            {!adviserPreview && (
              <div className="assist-empty">
                {tr(
                  "完成左侧前置条件后预览。切换目标范围、手臂、标定或修改标签会使旧候选失效。",
                )}
              </div>
            )}
            {adviserPreview && (
              <div className="assist-preview">
                <div className="assist-preview-stats">
                  <div>
                    <span>{tr("目标帧")}</span>
                    <strong>{adviserPreview.summary?.frames || 0}</strong>
                  </div>
                  <div>
                    <span>{tr("候选项")}</span>
                    <strong>{adviserPreview.summary?.candidates || 0}</strong>
                  </div>
                  <div>
                    <span>{tr("可保存")}</span>
                    <strong>{adviserPreview.summary?.savable || 0}</strong>
                  </div>
                  <div>
                    <span>{tr("跳过")}</span>
                    <strong>{adviserPreview.summary?.skipped || 0}</strong>
                  </div>
                </div>
                <div className="assist-reasons">
                  {Object.entries(adviserPreview.summary?.reasons || {}).map(
                    ([code, count]) => (
                      <span key={code}>
                        {explain(code)} · {String(count)}
                      </span>
                    ),
                  )}
                </div>
                <div className="assist-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{tr("帧 / 字段")}</th>
                        <th>{tr("结果")}</th>
                        <th>{tr("证据与原因")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {adviserRows
                        .slice(page * 12, page * 12 + 12)
                        .map((r, i) => (
                          <tr key={i}>
                            <td>
                              #{r.frame}
                              <small>{r.field_id}</small>
                            </td>
                            <td>
                              {r.value != null ? (
                                <>
                                  <strong>
                                    {tr(
                                      semanticLabels[r.semantic] || r.semantic,
                                    )}
                                  </strong>
                                  <small>ID {r.value}</small>
                                </>
                              ) : (
                                statusText(r.status)
                              )}
                            </td>
                            <td>
                              {r.duration != null && (
                                <small>
                                  [{r.start}, {r.end}) ·{" "}
                                  {Number(r.duration).toFixed(3)} s
                                </small>
                              )}
                              {explain(r.code, r.reason)}
                              <small>
                                {tr("源帧")} #{r.source_frame}
                              </small>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {adviserRows.length > 12 && (
                  <div className="assist-pagination">
                    <button
                      disabled={!page}
                      onClick={() => setPage((p) => p - 1)}
                    >
                      {tr("上一页")}
                    </button>
                    <span>
                      {page + 1} / {Math.ceil(adviserRows.length / 12)}
                    </span>
                    <button
                      disabled={(page + 1) * 12 >= adviserRows.length}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      {tr("下一页")}
                    </button>
                  </div>
                )}
                <p className="assist-help">
                  {tr(
                    "所有接受项仍为 draft，人工行为审核前监督 mask 为 0。none / keep 仅表示短窗口内无显著运动。",
                  )}
                </p>
              </div>
            )}
          </section>
        </div>
      </article>

      <article id="assist-protocol" className="assist-card">
        <Header
          number="04"
          title="任务协议与几何参考"
          description="为当前数据集记录一致的判断依据。这些说明随导出协议保存，不会自动推断三维距离或改写原始任务文本。"
        />
        <Scope>
          {tr("当前数据集")} · {datasetKey.slice(0, 12)} · {tr("当前标注方案")}{" "}
          {project.scheme?.name || ""}
        </Scope>
        {!settings.datasets?.[datasetKey]?.taskSettings &&
          Object.values(taskSettings || {}).some(Boolean) && (
            <div className="assist-legacy">
              <span>
                {tr("检测到旧项目级备注；请核对后再绑定到当前数据集。")}
              </span>
              <button onClick={() => patchTask(taskSettings)}>
                {tr("导入旧项目备注")}
              </button>
            </div>
          )}
        <div className="assist-fields">
          <Field label="目标抓取部位">
            <input
              value={task.target || ""}
              onChange={(e) => patchTask({ target: e.target.value })}
            />
          </Field>
          <Field label="承接区域">
            <input
              value={task.support || ""}
              onChange={(e) => patchTask({ support: e.target.value })}
            />
          </Field>
          <Field
            label="夹爪最大开口宽度 W（注明单位）"
            help="仅供人工几何判断；不会自动测距，也不是夹爪开闭阈值。"
          >
            <input
              value={task.gripper_width || ""}
              onChange={(e) => patchTask({ gripper_width: e.target.value })}
            />
          </Field>
          <Field label="协议版本">
            <input
              value={task.protocol_version || ""}
              placeholder={tr("例如 v1")}
              onChange={(e) => patchTask({ protocol_version: e.target.value })}
            />
          </Field>
          <Field label="判定标准与遮挡处理">
            <textarea
              rows={3}
              value={task.criteria || ""}
              onChange={(e) => patchTask({ criteria: e.target.value })}
            />
          </Field>
          <Field label="标定说明">
            <textarea
              rows={3}
              value={task.calibration_notes || ""}
              onChange={(e) => patchTask({ calibration_notes: e.target.value })}
            />
          </Field>
          <Field label="正例说明">
            <textarea
              rows={2}
              value={task.positive_examples || ""}
              onChange={(e) => patchTask({ positive_examples: e.target.value })}
            />
          </Field>
          <Field label="反例说明">
            <textarea
              rows={2}
              value={task.negative_examples || ""}
              onChange={(e) => patchTask({ negative_examples: e.target.value })}
            />
          </Field>
        </div>
      </article>
    </section>
  );
}
