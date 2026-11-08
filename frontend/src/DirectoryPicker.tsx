import { t as tr, useI18n } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./directory-picker.css";

type Listing = {
  path: string;
  parent: string | null;
  home: string;
  directories: { name: string; path: string }[];
  skipped: number;
};

async function request(url: string, body?: object, signal?: AbortSignal) {
  const response = await fetch("/api/filesystem/" + url, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  const value = await response.json();
  if (!response.ok)
    throw Error(
      typeof value.detail === "string" ? value.detail : "无法读取目录",
    );
  return value;
}

export default function DirectoryPicker({
  value,
  onChange,
  purpose,
  disabled = false,
  onBrowse,
}: {
  value: string;
  onChange: (path: string) => void;
  purpose: "import" | "inspect";
  disabled?: boolean;
  onBrowse?: () => void;
}) {
  const { language } = useI18n();
  const [open, setOpen] = useState(false);
  const [nativeBusy, setNativeBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [listing, setListing] = useState<Listing | null>(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [hidden, setHidden] = useState(false);
  const [filter, setFilter] = useState("");
  const dialog = useRef<HTMLDialogElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const browsing = useRef<AbortController | null>(null);
  const native = useRef<AbortController | null>(null);
  const title =
    purpose === "import"
      ? "选择导入的数据集文件夹"
      : "选择要检查的导出数据集文件夹";

  useEffect(
    () => () => {
      browsing.current?.abort();
      native.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else {
      dialog.current?.close();
      browsing.current?.abort();
    }
  }, [open]);

  async function browse(target: string, initial = false, showHidden = hidden) {
    browsing.current?.abort();
    const controller = new AbortController();
    browsing.current = controller;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        path: target,
        initial: String(initial),
        show_hidden: String(showHidden),
      });
      const result: Listing = await request(
        "directories?" + query,
        undefined,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setListing(result);
        setPath(result.path);
        setFilter("");
      }
    } catch (e: any) {
      if (!controller.signal.aborted) setError(e.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  function openBrowser(message = "") {
    onBrowse?.();
    setListing(null);
    setNotice(message);
    setFilter("");
    setOpen(true);
    browse(value, true);
  }
  async function pickNative() {
    onBrowse?.();
    setError("");
    setNativeBusy(true);
    const controller = new AbortController();
    native.current = controller;
    try {
      const result = await request(
        "pick-directory",
        { initial_path: value, purpose, language },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (result.status === "selected") onChange(result.path);
      else if (result.status === "unavailable") openBrowser(result.message);
      else if (result.status === "busy") setError(result.message);
    } catch (e: any) {
      if (!controller.signal.aborted)
        openBrowser("系统选择器暂不可用，可在这里浏览文件夹。" + e.message);
    } finally {
      if (!controller.signal.aborted) setNativeBusy(false);
    }
  }
  const entries =
    listing?.directories.filter((entry) =>
      entry.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase()),
    ) || [];

  return (
    <>
      <span className="directory-picker">
        <button
          ref={button}
          type="button"
          disabled={disabled || nativeBusy}
          onClick={pickNative}
          aria-label={tr(title)}
        >
          <span aria-hidden="true">▣</span>{" "}
          {nativeBusy ? tr("等待文件夹选择…") : tr("选择文件夹")}
        </button>
        <button
          type="button"
          className="directory-browser-link"
          disabled={disabled || nativeBusy}
          onClick={() => openBrowser()}
          aria-label={
            tr("在页面内浏览") +
            (purpose === "import" ? tr("导入目录") : tr("导出检查目录"))
          }
        >
          {tr("页面内浏览")}
        </button>
        {error && !open && (
          <span className="directory-picker-error" role="alert">
            {tr(error)}
          </span>
        )}
      </span>
      {createPortal(
        <dialog
          ref={dialog}
          className="directory-dialog"
          aria-label={tr(title)}
          onCancel={(event) => {
            event.preventDefault();
            setOpen(false);
          }}
          onClose={() => {
            setOpen(false);
            setError("");
            button.current?.focus();
          }}
        >
          <div className="directory-heading">
            <div>
              <strong>{tr(title)}</strong>
              <p>{tr("进入数据集根目录后，点击“使用此文件夹”。")}</p>
            </div>
            <button
              type="button"
              aria-label={tr("关闭文件夹浏览器")}
              onClick={() => setOpen(false)}
            >
              ×
            </button>
          </div>
          {notice && (
            <p className="directory-notice" role="status">
              {tr(notice)}
            </p>
          )}
          <form
            className="directory-path"
            onSubmit={(event) => {
              event.preventDefault();
              event.stopPropagation();
              browse(path);
            }}
          >
            <input
              aria-label={tr("文件夹浏览路径")}
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder={tr("输入目录路径")}
            />
            <button disabled={loading}>{tr("前往")}</button>
          </form>
          <div className="directory-tools">
            <button
              type="button"
              disabled={loading || !listing?.parent}
              onClick={() => browse(listing!.parent!)}
            >
              {tr("↑ 上一级")}
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={() => browse(listing?.home || "")}
            >
              {tr("主目录")}
            </button>
            <label>
              <input
                type="checkbox"
                checked={hidden}
                onChange={(event) => {
                  setHidden(event.target.checked);
                  browse(
                    listing?.path || value,
                    !listing,
                    event.target.checked,
                  );
                }}
              />{" "}
              {tr("显示隐藏文件夹")}
            </label>
          </div>
          <input
            className="directory-filter"
            aria-label={tr("筛选当前目录的子文件夹")}
            placeholder={tr("筛选子文件夹…")}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          {error && (
            <p className="directory-picker-error" role="alert">
              {tr(error)}
            </p>
          )}
          <div
            className="directory-list"
            aria-label={tr("子文件夹")}
            aria-busy={loading}
          >
            {loading ? (
              <p role="status">{tr("正在读取文件夹…")}</p>
            ) : entries.length ? (
              entries.map((entry) => (
                <button
                  type="button"
                  key={entry.path}
                  title={entry.path}
                  onClick={() => browse(entry.path)}
                >
                  <span aria-hidden="true">▸</span>
                  <span>{entry.name}</span>
                  <span aria-hidden="true">→</span>
                </button>
              ))
            ) : (
              <p>
                {filter
                  ? tr("没有匹配的子文件夹")
                  : tr("此目录没有可见的子文件夹，可直接使用此文件夹。")}
              </p>
            )}
          </div>
          <div className="directory-selection">
            <small>{tr("当前选择")}</small>
            <code>{listing?.path || tr("尚未选择")}</code>
            {!!listing?.skipped && <small>{tr("部分子目录无法读取")}</small>}
          </div>
          <div className="directory-actions">
            <button type="button" onClick={() => setOpen(false)}>
              {tr("取消")}
            </button>
            <button
              type="button"
              className="primary"
              disabled={!listing || loading || !!error}
              onClick={() => {
                onChange(listing!.path);
                setOpen(false);
              }}
            >
              {tr("使用此文件夹")}
            </button>
          </div>
        </dialog>,
        document.body,
      )}
    </>
  );
}
