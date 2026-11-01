type PendingImage = {
  signal: AbortSignal;
  promise: Promise<string>;
};

// Keep decoded images warm between windows. Stable HTTP URLs also let the
// browser reuse its media cache; cursor versions belong to window requests.
export class FrameImageCache {
  private ready = new Map<string, HTMLImageElement>();
  private pending = new Map<string, PendingImage>();
  private bytes = 0;

  constructor(
    private maxBytes = 64 * 1024 * 1024,
    private maxImages = 128,
  ) {}

  async load(url: string, signal: AbortSignal): Promise<string> {
    signal.throwIfAborted();
    const cached = this.ready.get(url);
    if (cached) {
      this.ready.delete(url);
      this.ready.set(url, cached);
      return url;
    }
    const pending = this.pending.get(url);
    if (pending && pending.signal === signal) return pending.promise;

    const image = new Image();
    image.decoding = "async";
    const abort = () => image.removeAttribute("src");
    signal.addEventListener("abort", abort, { once: true });
    image.src = url;
    const entry: PendingImage = {
      signal,
      promise: image
        .decode()
        .then(() => {
          signal.throwIfAborted();
          const previous = this.ready.get(url);
          if (previous) this.bytes -= this.size(previous);
          this.ready.delete(url);
          this.ready.set(url, image);
          this.bytes += this.size(image);
          this.trim();
          return url;
        })
        .catch((error) => {
          signal.throwIfAborted();
          throw new Error("相机帧加载失败，请重试", { cause: error });
        })
        .finally(() => {
          signal.removeEventListener("abort", abort);
          if (this.pending.get(url) === entry) this.pending.delete(url);
        }),
    };
    this.pending.set(url, entry);
    return entry.promise;
  }

  clear() {
    this.ready.clear();
    this.bytes = 0;
  }

  private size(image: HTMLImageElement) {
    return image.naturalWidth * image.naturalHeight * 4;
  }

  private trim() {
    for (const [url, image] of this.ready) {
      if (this.bytes <= this.maxBytes && this.ready.size <= this.maxImages)
        break;
      this.ready.delete(url);
      this.bytes -= this.size(image);
      // Dropping our reference never invalidates a displayed image or modal.
    }
  }
}
