import React, { useEffect, useRef, useState } from "react";
import "./inspector.css";
import DirectoryPicker from "./DirectoryPicker";

type Obj = Record<string, any>;
const roles: Obj = {
  center: "训练中心",
  history: "历史前缀",
  review: "复核尾部",
  context: "其他上下文",
};
const cameraName = (name: string) =>
  name.includes("high")
    ? "顶视"
    : name.includes("left")
      ? "左腕"
      : name.includes("right")
        ? "右腕"
        : name;
const raw = (value: any) =>
  value === null
    ? "null"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
async function request(path: string, body?: Obj, signal?: AbortSignal) {
  const response = await fetch("/api/inspect" + path, {
    method: body ? "POST" : "GET",
    body: body ? JSON.stringify(body) : undefined,
    headers: body ? { "Content-Type": "application/json" } : undefined,
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
function RawTable({ value }: { value: Obj }) {
  return (
    <div className="inspect-table-scroll">
      <table className="inspect-table">
        <thead>
          <tr>
            <th>Parquet 列名</th>
            <th>实际保存的值</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(value).map(([key, value]) => (
            <tr key={key}>
              <th>{key}</th>
              <td className={value === null ? "inspect-null" : ""}>
                <code>{raw(value)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export default function ExportInspector({
  initialPath,
  exports,
}: {
  initialPath: string;
  exports: Obj[];
}) {
  const [path, setPath] = useState(initialPath),
    [reader, setReader] = useState<Obj | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [episode, setEpisode] = useState(0),
    [frame, setFrame] = useState(0),
    [frameData, setDetail] = useState<Obj | null>(null),
    [tableData, setTable] = useState<Obj | null>(null),
    [offset, setOffset] = useState(0),
    [tab, setTab] = useState("labels"),
    [arm, setArm] = useState("all"),
    [image, setImage] = useState<string | null>(null),
    [filePreview, setFilePreview] = useState<Obj | null>(null);
  const fileRequest = useRef(0);
  async function previewFile(path: string, offset = 0) {
    const version = ++fileRequest.current;
    try {
      const value = await request(
        `/${reader!.id}/file?path=${encodeURIComponent(path)}&offset=${offset}&limit=20`,
      );
      if (version === fileRequest.current) setFilePreview(value);
    } catch (e: any) {
      if (version === fileRequest.current) setError(e.message);
    }
  }
  const detailSection = useRef<HTMLElement | null>(null);
  const ep = reader?.episodes.find((e: Obj) => e.episode === episode);
  const detail =
    frameData?._reader === reader?.id &&
    frameData?.summary.episode === episode &&
    frameData?.summary.frame === frame
      ? frameData
      : null;
  const table =
    tableData?._reader === reader?.id &&
    tableData?._episode === episode &&
    tableData?.offset === offset
      ? tableData
      : null;
  useEffect(() => {
    if (initialPath) open(initialPath);
  }, [initialPath]);
  const imageURL = (e: number, f: number, c: number) =>
    `/api/inspect/${reader!.id}/image/${e}/${c}/${f}`;
  async function open(target = path) {
    setBusy(true);
    setError("");
    setDetail(null);
    setTable(null);
    setReader(null);
    setFilePreview(null);
    ++fileRequest.current;
    try {
      const value = await request("/open", { path: target });
      setReader(value);
      setPath(value.root);
      setEpisode(value.episodes[0].episode);
      setFrame(value.episodes[0].first_center ?? 0);
      setOffset(0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function go(e: number, f: number, scroll = false) {
    setEpisode(e);
    setFrame(f);
    setOffset(Math.floor(f / 50) * 50);
    setError("");
    if (scroll)
      detailSection.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
  }
  useEffect(() => {
    if (!reader) return;
    const controller = new AbortController();
    setDetail(null);
    request(
      `/${reader.id}/frame/${episode}/${frame}`,
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted)
          setDetail({ ...value, _reader: reader.id });
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [reader?.id, episode, frame]);
  useEffect(() => {
    if (!reader) return;
    const controller = new AbortController();
    setTable(null);
    request(
      `/${reader.id}/episode/${episode}?offset=${offset}&limit=50`,
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted)
          setTable({ ...value, _reader: reader.id, _episode: episode });
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [reader?.id, episode, offset]);
  const thumb = (e: number, f: number, c: number, alt: string) => (
    <img
      key={`${e}:${f}:${c}`}
      src={imageURL(e, f, c)}
      alt={alt}
      loading="lazy"
      onClick={() => setImage(imageURL(e, f, c))}
    />
  );
  return (
    <section className="export-inspector">
      <div className="inspect-heading">
        <div>
          <span className="eyebrow">EXPORT LENS · 已保存的数据</span>
          <h1>导出数据集检查器</h1>
          <p>
            直接查看磁盘上的视频与
            Parquet。这里显示的是导出快照，不受当前标注草稿影响。
          </p>
        </div>
        <span className="inspect-readonly">只读检查</span>
      </div>
      <form
        className="inspect-open"
        onSubmit={(e) => {
          e.preventDefault();
          open();
        }}
      >
        <label>
          导出目录
          <input
            aria-label="检查的导出目录"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/path/to/export"
            required
          />
        </label>
        <DirectoryPicker
          value={path}
          onChange={setPath}
          purpose="inspect"
          disabled={busy}
        />
        <button className="primary" disabled={busy}>
          {busy ? "正在校验并读取实际文件…" : "打开并检查"}
        </button>
      </form>
      {exports.length > 0 && (
        <div className="inspect-recent">
          <span>已完成的导出：</span>
          {exports.map((j) => (
            <button
              key={j.id}
              disabled={busy}
              onClick={() => open(j.destination)}
            >
              {j.destination.split("/").pop()}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="inspect-error" role="alert">
          {error}
        </p>
      )}
      {!reader && !busy && (
        <div className="inspect-empty">
          <h2>从文件目录，变成可以逐帧核对的地图</h2>
          <div className="inspect-intro">
            <article>
              <b>01 · 哪些帧被保留？</b>
              <p>
                训练中心、历史前缀和复核尾部用不同颜色显示；重叠副本放在同一源时间轴上比较。
              </p>
            </article>
            <article>
              <b>02 · Parquet 填了什么？</b>
              <p>
                逐帧读取观测数值、null 标签、审核状态和 loss
                mask，不用猜测文件含义。
              </p>
            </article>
            <article>
              <b>03 · 模型真正读到什么？</b>
              <p>
                查看一个训练中心的历史抽帧、存在性 mask、分类目标与监督 mask。
              </p>
            </article>
          </div>
          <p>
            请选择已导出的 LeRobot+DSE 目录。检查器不会导入标注、改 schema
            或向数据集写入文件。
          </p>
        </div>
      )}
      {reader && (
        <>
          <div className="inspect-root">
            <code>{reader.root}</code>
            <span>文件校验和已通过</span>
          </div>
          <div className="inspect-metrics">
            {[
              [reader.total_rows, "导出帧记录"],
              [reader.center_rows, "训练中心"],
              [reader.total_rows - reader.center_rows, "仅上下文"],
              [reader.unique_source_frames, "唯一源帧"],
              [reader.duplicate_extra_rows, "重叠产生的额外副本"],
            ].map(([value, label]) => (
              <article key={label}>
                <strong>{value.toLocaleString()}</strong>
                <span>{label}</span>
              </article>
            ))}
          </div>
          <p className="inspect-task">
            任务：{reader.tasks?.map((t: Obj) => t.task).join(" / ")}
          </p>
          <div className="inspect-contract">
            <b>
              统一输入：{reader.history_config.horizon_seconds} 秒 /{" "}
              {reader.history_config.num_frames} 时刻（含当前）
            </b>
            <span>
              保留前史 {reader.context_seconds?.history ?? "未记录"} 秒 ·
              尾部复核 {reader.context_seconds?.review ?? "未记录"} 秒
            </span>
            <p>
              视频与观测数值保留真实内容；标注在 labels.parquet
              单独存放。上下文可以有旧标签，但在该副本中 loss mask 始终为 0。
            </p>
          </div>
          <section className="inspect-map">
            <h2>① 看清片段与重叠</h2>
            <div className="inspect-legend">
              {Object.entries(roles).map(([key, label]) => (
                <span key={key}>
                  <i className={"inspect-role " + key} />
                  {label}
                </span>
              ))}
              <span>
                <i className="inspect-role duplicate" />
                同一源帧的多个副本
              </span>
            </div>
            <p>
              每行是一个导出
              episode，横向位置对应原始源帧。点击色块查看实际记录；不相邻的训练中心保留独立片段，重叠上下文分别保存。
            </p>
            {reader.source_groups.map((group: Obj) => (
              <div className="inspect-source" key={group.uuid}>
                <h3>
                  原始 EP {group.source_episode_index}{" "}
                  <small>
                    源 UUID {group.uuid.slice(0, 12)}… · 数据指纹{" "}
                    {group.fingerprint.slice(0, 10)}…
                  </small>
                </h3>
                <div className="inspect-axis">
                  <span>源帧 #{group.start}</span>
                  <span>#{group.end - 1}</span>
                </div>
                {group.lanes.map((lane: Obj) => (
                  <div className="inspect-lane" key={lane.episode}>
                    <button
                      onClick={() =>
                        go(
                          lane.episode,
                          reader.episodes.find(
                            (e: Obj) => e.episode === lane.episode,
                          ).first_center ?? 0,
                          true,
                        )
                      }
                    >
                      导出 EP {lane.episode}
                    </button>
                    <div>
                      {lane.runs.map((run: Obj, i: number) => (
                        <button
                          key={i}
                          className={"inspect-segment " + run.role}
                          style={{
                            left: `${(100 * (run.source_start - group.start)) / (group.end - group.start)}%`,
                            width: `${(100 * (run.source_end - run.source_start)) / (group.end - group.start)}%`,
                          }}
                          title={`${roles[run.role]} · 源帧 [${run.source_start},${run.source_end}) → 导出EP ${lane.episode} 本地帧 [${run.start},${run.end})`}
                          aria-label={`查看导出EP ${lane.episode} ${roles[run.role]} 源帧 [${run.source_start},${run.source_end})`}
                          onClick={() => go(lane.episode, run.start, true)}
                        >
                          {roles[run.role]}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
            <div className="inspect-overlaps">
              <h3>
                重叠帧对照{" "}
                <small>
                  {reader.repeated_source_frames} 个源帧出现多份记录
                </small>
              </h3>
              {reader.overlaps.length === 0 ? (
                <p>
                  当前导出没有源帧重复。若前史与其他片段重叠，这里会列出实际副本及各自角色。
                </p>
              ) : (
                <>
                  <p>
                    以下区间按真实 source_map
                    匹配。点击副本即可对比它在各片段中的标签与 mask；同源副本的
                    split_group 应相同。
                  </p>
                  {reader.overlaps.map((o: Obj, i: number) => (
                    <div className="inspect-overlap-row" key={i}>
                      <b>
                        源 EP{" "}
                        {
                          reader.source_groups.find(
                            (g: Obj) => g.uuid === o.uuid,
                          )?.source_episode_index
                        }{" "}
                        · 帧 [{o.start},{o.end})
                      </b>
                      <div>
                        {o.copies.map((c: Obj) => (
                          <button
                            key={`${c.episode}:${c.start}`}
                            className={"inspect-copy " + c.role}
                            onClick={() => go(c.episode, c.start, true)}
                          >
                            EP {c.episode} / #{c.start} · {roles[c.role]}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                  {reader.overlap_group_count > reader.overlaps.length && (
                    <p>这里只展示前200组；逐帧详情仍会列出该源帧的全部副本。</p>
                  )}
                </>
              )}
            </div>
          </section>
          <section className="inspect-detail-section" ref={detailSection}>
            <h2>② 点开一帧，核对保存值与训练输入</h2>
            <div className="inspect-controls">
              <label>
                导出 episode
                <select
                  aria-label="检查的导出episode"
                  value={episode}
                  onChange={(e) => {
                    const ep = reader.episodes.find(
                      (v: Obj) => v.episode === +e.target.value,
                    );
                    go(ep.episode, ep.first_center ?? 0);
                  }}
                >
                  {reader.episodes.map((e: Obj) => (
                    <option key={e.episode} value={e.episode}>
                      EP {e.episode} · {e.length}帧 / {e.center_count}中心
                    </option>
                  ))}
                </select>
              </label>
              <label>
                本片段帧号
                <input
                  aria-label="检查的本片段帧号"
                  type="number"
                  min={0}
                  max={ep.length - 1}
                  value={frame}
                  onChange={(e) =>
                    go(
                      episode,
                      Math.max(
                        0,
                        Math.min(ep.length - 1, Math.trunc(+e.target.value)),
                      ),
                    )
                  }
                />
              </label>
              <button
                disabled={frame === 0}
                onClick={() => go(episode, frame - 1)}
              >
                上一帧
              </button>
              <button
                disabled={frame === ep.length - 1}
                onClick={() => go(episode, frame + 1)}
              >
                下一帧
              </button>
              <button
                disabled={ep.first_center === null}
                onClick={() => go(episode, ep.first_center)}
              >
                第一个训练中心
              </button>
            </div>
            <input
              className="inspect-slider"
              aria-label="检查器帧游标"
              type="range"
              min={0}
              max={ep.length - 1}
              value={frame}
              onChange={(e) => go(episode, +e.target.value)}
            />
            <div className="inspect-local-map">
              {ep.runs.map((r: Obj, i: number) => (
                <button
                  key={i}
                  className={"inspect-segment " + r.role}
                  style={{
                    left: `${(100 * r.start) / ep.length}%`,
                    width: `${(100 * (r.end - r.start)) / ep.length}%`,
                  }}
                  title={`${roles[r.role]} 本地[${r.start},${r.end})`}
                  onClick={() => go(episode, r.start)}
                >
                  {roles[r.role]}
                </button>
              ))}
            </div>
            {detail ? (
              <>
                <div className={"inspect-frame-banner " + detail.summary.role}>
                  <b>
                    {roles[detail.summary.role]} · 导出 EP {episode} / 帧 #
                    {frame}
                  </b>
                  <span>
                    ← 原始 EP {detail.summary.source_episode_index} / 源帧 #
                    {detail.summary.source_frame} /{" "}
                    {detail.summary.source_timestamp.toFixed(3)}s
                  </span>
                  <span>
                    {detail.summary.valid_fields} / {detail.summary.label_count}{" "}
                    字段计损失 · {detail.summary.copies} 份源帧副本
                  </span>
                </div>
                <div className="inspect-images">
                  {detail.cameras.map((name: string, c: number) => (
                    <figure key={name}>
                      {thumb(
                        episode,
                        frame,
                        c,
                        `检查 ${cameraName(name)} EP${episode} 帧${frame}`,
                      )}
                      <figcaption>{cameraName(name)} · 实际导出视频</figcaption>
                    </figure>
                  ))}
                </div>
                {detail.equivalents.length > 1 && (
                  <div className="inspect-equivalents">
                    <b>同一源帧的其他位置</b>
                    {detail.equivalents.map((copy: Obj) => (
                      <button
                        key={`${copy.episode}:${copy.frame}`}
                        className={
                          copy.episode === episode && copy.frame === frame
                            ? "active"
                            : ""
                        }
                        onClick={() => go(copy.episode, copy.frame)}
                      >
                        EP{copy.episode} / #{copy.frame} · {roles[copy.role]} ·
                        有效监督{copy.valid_fields}项
                      </button>
                    ))}
                    <small>
                      观测来自同一源帧；视频分别重编码，像素不保证完全相同。训练角色与监督
                      mask 按各副本分别保存。
                    </small>
                  </div>
                )}
                {detail.comparisons?.length > 1 && (
                  <details className="inspect-comparison">
                    <summary>并排对照同一源帧的各份标签</summary>
                    <div className="inspect-table-scroll">
                      <table className="inspect-table">
                        <thead>
                          <tr>
                            <th>字段</th>
                            {detail.comparisons.map((c: Obj) => (
                              <th key={c.episode}>
                                EP{c.episode} / #{c.frame} · {roles[c.role]}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {detail.labels.map((l: Obj) => (
                            <tr key={l.raw.arm + l.raw.field_id}>
                              <th>
                                {l.raw.arm}.{l.raw.field_id}
                              </th>
                              {detail.comparisons.map((c: Obj) => {
                                const value = c.labels.find(
                                  (r: Obj) =>
                                    r.arm === l.raw.arm &&
                                    r.field_id === l.raw.field_id,
                                );
                                return (
                                  <td key={c.episode}>
                                    {value ? (
                                      <>
                                        <code>
                                          value_id={raw(value.value_id)}
                                        </code>
                                        <br />
                                        <strong
                                          className={
                                            value.effective_loss_mask
                                              ? "inspect-mask-on"
                                              : "inspect-mask-off"
                                          }
                                        >
                                          mask={value.effective_loss_mask}
                                        </strong>{" "}
                                        · {value.review_status}
                                      </>
                                    ) : (
                                      "无对应记录"
                                    )}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}
                <div className="inspect-tabs" role="tablist">
                  {[
                    ["labels", "标签与 loss mask"],
                    ["data", "观测数据"],
                    ["sample", "样本角色"],
                    ["source_map", "来源映射"],
                    ["training", "训练输入预览"],
                  ].map(([key, label]) => (
                    <button
                      role="tab"
                      aria-selected={tab === key}
                      className={tab === key ? "active" : ""}
                      key={key}
                      onClick={() => setTab(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {tab === "labels" && (
                  <>
                    <div className="inspect-explanation">
                      <b>实际文件：annotations/labels.parquet</b>
                      <p>
                        value_id 的 null 保持为 null；loader 将它转换为
                        target=-100。unknown
                        是真实类别，与缺失不同。表中的状态与 mask
                        均为文件保存值；“原因”和 target 是阅读辅助。
                      </p>
                      <label>
                        手臂筛选
                        <select
                          aria-label="检查标签手臂"
                          value={arm}
                          onChange={(e) => setArm(e.target.value)}
                        >
                          {[
                            ["all", "全部"],
                            ["left", "左臂"],
                            ["right", "右臂"],
                            ["global", "全局"],
                          ].map(([a, l]) => (
                            <option key={a} value={a}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="inspect-table-scroll">
                      <table className="inspect-table inspect-labels">
                        <thead>
                          <tr>
                            <th>arm / field_id</th>
                            <th>value_id</th>
                            <th>类别</th>
                            <th>annotation_status</th>
                            <th>applicability</th>
                            <th>review_status</th>
                            <th>loss mask</th>
                            <th>loader target</th>
                            <th>原因</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.labels
                            .filter(
                              (l: Obj) => arm === "all" || l.raw.arm === arm,
                            )
                            .map((l: Obj) => (
                              <tr key={l.raw.arm + l.raw.field_id}>
                                <th>
                                  {l.raw.arm}
                                  <br />
                                  <b>{l.raw.field_id}</b>
                                </th>
                                <td
                                  className={
                                    l.raw.value_id === null
                                      ? "inspect-null"
                                      : ""
                                  }
                                >
                                  <code>{raw(l.raw.value_id)}</code>
                                </td>
                                <td>{l.class_name ?? "—"}</td>
                                <td>{l.raw.annotation_status}</td>
                                <td>{l.raw.applicability}</td>
                                <td>{l.raw.review_status}</td>
                                <td>
                                  <strong
                                    className={
                                      l.raw.effective_loss_mask
                                        ? "inspect-mask-on"
                                        : "inspect-mask-off"
                                    }
                                  >
                                    {l.raw.effective_loss_mask}
                                  </strong>
                                </td>
                                <td>
                                  <code>{l.training_target}</code>
                                </td>
                                <td>{l.reason}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                    <details>
                      <summary>
                        查看本帧 labels.parquet 完整原始行（含 provenance）
                      </summary>
                      <pre>
                        {JSON.stringify(
                          detail.labels.map((l: Obj) => l.raw),
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </>
                )}
                {tab === "data" && (
                  <>
                    <div className="inspect-explanation">
                      <b>实际文件：{detail.data_file}</b>
                      <p>
                        这是标准 LeRobot 观测/动作表；没有标注不代表
                        state/action 被清零。视频不塞在这些数值列中，而是通过
                        episode、帧与时间戳对应
                        MP4。action/命令可以保存在导出文件中，但 DSE wrapper
                        不把它们作为前向输入。
                      </p>
                    </div>
                    <RawTable value={detail.data} />
                  </>
                )}
                {tab === "sample" && (
                  <>
                    <p className="inspect-explanation">
                      训练中心资格由 center_is_selected 决定；is_history_context
                      可以与训练中心同时为
                      true（它也可为后面的中心提供历史）。颜色优先显示“训练中心”，原始布尔值全部保留在下表。sampling_weight=0
                      的帧不被加权抽样。
                    </p>
                    <RawTable value={detail.sample} />
                  </>
                )}
                {tab === "source_map" && (
                  <>
                    <p className="inspect-explanation">
                      source_* 指向最初来源；parent_*
                      指向本次导出的直接父集。即使独立片段中帧号重新从0开始，也能找到同一源帧。
                    </p>
                    <RawTable value={detail.source_map} />
                  </>
                )}
                {tab === "training" &&
                  (detail.training.is_sample ? (
                    <>
                      <div className="inspect-explanation">
                        <b>这个训练中心实际采样的历史</b>
                        <p>
                          图像形状 [{detail.training.image_shape.join(", ")}
                          ]，RGB
                          256×256；下方使用导出原视频展示取帧位置。只有当前中心的标签参与损失，历史帧的标签不会混作当前真值。
                        </p>
                      </div>
                      <div className="inspect-history">
                        {detail.history.map((h: Obj, t: number) => (
                          <article key={t}>
                            <h4>
                              {h.offset_seconds === 0
                                ? "当前 t"
                                : `t − ${Number(h.offset_seconds.toFixed(3))}s`}
                            </h4>
                            {h.frame === null ? (
                              <div className="inspect-padding">
                                <b>缺失历史</b>
                                <p>
                                  图像 / state / 末端置零
                                  <br />
                                  image mask = false
                                  <br />
                                  state mask = false
                                  <br />
                                  实际时间 = NaN
                                </p>
                              </div>
                            ) : (
                              <>
                                <p>
                                  本地 #{h.frame} ← 源 #{h.sample.source_frame}
                                  <br />
                                  实际 Δt {h.relative_timestamp.toFixed(3)}s
                                  <br />
                                  image mask = true · state mask = true
                                </p>
                                {detail.cameras.map(
                                  (name: string, c: number) => (
                                    <div key={name}>
                                      {thumb(
                                        episode,
                                        h.frame,
                                        c,
                                        `训练输入 ${cameraName(name)} 历史时刻${t} 帧${h.frame}`,
                                      )}
                                    </div>
                                  ),
                                )}
                                <button onClick={() => go(episode, h.frame)}>
                                  检查该历史帧记录
                                </button>
                              </>
                            )}
                          </article>
                        ))}
                      </div>
                      <details>
                        <summary>
                          loader 输出的本中心分类 target 与 loss_masks
                        </summary>
                        <pre>{JSON.stringify(detail.training, null, 2)}</pre>
                      </details>
                    </>
                  ) : (
                    <div className="inspect-padding">
                      <h3>这一帧只是上下文，不单独产出训练样本</h3>
                      <p>
                        DSEDataset 只从 center_is_selected=true
                        的中心取样。这帧可被某个中心读取为历史图像，但自身标签不计损失。
                      </p>
                      <button onClick={() => go(episode, ep.first_center ?? 0)}>
                        查看本片段第一个训练中心的输入
                      </button>
                    </div>
                  ))}
              </>
            ) : (
              <div className="inspect-loading">正在读取这帧的文件记录…</div>
            )}
            <details className="inspect-row-browser">
              <summary>
                展开本片段逐帧记录（samples + source_map + 标签统计）
              </summary>
              {table && (
                <>
                  <div className="inspect-controls">
                    <button
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 50))}
                    >
                      前50行
                    </button>
                    <span>
                      行 {offset}–{Math.min(offset + 50, table.total) - 1} / 共
                      {table.total}行
                    </span>
                    <button
                      disabled={offset + 50 >= table.total}
                      onClick={() => setOffset(offset + 50)}
                    >
                      后50行
                    </button>
                  </div>
                  <div className="inspect-table-scroll">
                    <table className="inspect-table">
                      <thead>
                        <tr>
                          <th>本地帧</th>
                          <th>源帧</th>
                          <th>角色</th>
                          <th>center</th>
                          <th>history</th>
                          <th>review</th>
                          <th>有效字段数</th>
                          <th>null标签数</th>
                          <th>sampling_weight</th>
                          <th>副本数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {table.rows.map((r: Obj) => (
                          <tr
                            key={r.frame}
                            className={r.frame === frame ? "selected" : ""}
                          >
                            <td>
                              <button onClick={() => go(episode, r.frame)}>
                                #{r.frame}
                              </button>
                            </td>
                            <td>#{r.source_frame}</td>
                            <td>{roles[r.role]}</td>
                            <td>{String(r.center_is_selected)}</td>
                            <td>{String(r.is_history_context)}</td>
                            <td>{String(r.is_review_context)}</td>
                            <td>{r.valid_fields}</td>
                            <td>{r.null_labels}</td>
                            <td>{r.sampling_weight.toFixed(5)}</td>
                            <td>{r.copies}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </details>
          </section>
          <section className="inspect-files">
            <h2>③ 对照真实文件与列类型</h2>
            <p>
              以下为目录内实际文件；展开 Parquet 文件可查看列名、类型及 nullable
              属性。逐帧值通过上面的检查面板读取。
            </p>
            <div className="inspect-file-list">
              {reader.files.map((f: Obj) => (
                <details key={f.path}>
                  <summary>
                    <code>{f.path}</code>
                    <span>
                      {f.rows !== undefined
                        ? `${f.rows.toLocaleString()} 行 · `
                        : ""}
                      {(f.bytes / 1024).toFixed(1)} KB
                    </span>
                  </summary>
                  {!f.path.endsWith(".mp4") && (
                    <div className="inspect-file-preview">
                      <button
                        aria-label={`预览文件 ${f.path}`}
                        onClick={() => previewFile(f.path)}
                      >
                        预览实际文件内容
                      </button>
                      {filePreview && filePreview.path === f.path && (
                        <>
                          <div className="inspect-controls">
                            {filePreview.total !== undefined && (
                              <>
                                <button
                                  disabled={filePreview.offset === 0}
                                  onClick={() =>
                                    previewFile(
                                      f.path,
                                      Math.max(0, filePreview.offset - 20),
                                    )
                                  }
                                >
                                  前20行
                                </button>
                                <span>
                                  行{filePreview.offset}–
                                  {Math.min(
                                    filePreview.offset + 20,
                                    filePreview.total,
                                  ) - 1}{" "}
                                  / 共{filePreview.total}行
                                </span>
                                <button
                                  disabled={
                                    filePreview.offset + 20 >= filePreview.total
                                  }
                                  onClick={() =>
                                    previewFile(f.path, filePreview.offset + 20)
                                  }
                                >
                                  后20行
                                </button>
                              </>
                            )}
                          </div>
                          <pre>
                            {JSON.stringify(
                              filePreview.kind === "json"
                                ? filePreview.value
                                : filePreview.rows,
                              null,
                              2,
                            )}
                          </pre>
                        </>
                      )}
                    </div>
                  )}
                  {f.columns ? (
                    <div className="inspect-table-scroll">
                      <table className="inspect-table">
                        <thead>
                          <tr>
                            <th>列名</th>
                            <th>Arrow 类型</th>
                            <th>nullable</th>
                          </tr>
                        </thead>
                        <tbody>
                          {f.columns.map((c: Obj) => (
                            <tr key={c.name}>
                              <th>{c.name}</th>
                              <td>{c.type}</td>
                              <td>{String(c.nullable)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p>
                      此文件为
                      {f.path.endsWith(".mp4")
                        ? "视频，可在上面的逐帧画面中查看"
                        : "元数据或审核配置"}
                      。
                    </p>
                  )}
                </details>
              ))}
            </div>
            <details>
              <summary>查看实际 manifest.json</summary>
              <pre>{JSON.stringify(reader.manifest, null, 2)}</pre>
            </details>
          </section>
        </>
      )}
      {image && (
        <div
          className="inspect-image-modal"
          role="dialog"
          aria-label="导出视频放大"
        >
          <button onClick={() => setImage(null)}>关闭大图</button>
          <img src={image} alt="导出视频原帧放大" />
        </div>
      )}
    </section>
  );
}
