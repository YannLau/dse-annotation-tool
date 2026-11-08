import { useSyncExternalStore } from "react";
import { categoryLabel as label, translate } from "./i18nCore";
import type { Language, MessageValues } from "./i18nCore";

const storageKey = "dse-ui-language";
const valid = (value: unknown): value is Language =>
  value === "zh-CN" || value === "en";
function initialLanguage(): Language {
  try {
    const saved = localStorage.getItem(storageKey);
    if (valid(saved)) return saved;
  } catch {
    /* The switch also works when browser storage is unavailable. */
  }
  return "zh-CN";
}
let language = initialLanguage();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
export const getLanguage = () => language;
export function setLanguage(next: Language) {
  if (!valid(next) || next === language) return;
  language = next;
  try {
    localStorage.setItem(storageKey, next);
  } catch {
    /* Keep the in-memory choice. */
  }
  listeners.forEach((listener) => listener());
}
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === storageKey)
      setLanguage(valid(event.newValue) ? event.newValue : "zh-CN");
  });
}

// Canonical message strings stay in state so saved notifications, errors and
// server-provided reasons can be rendered again after either language switch.
export const t = (message: string | null | undefined, values?: MessageValues) =>
  translate(message, language, values);
export const categoryLabel = (category: Record<string, any>) =>
  label(category, language);
export function useI18n() {
  const current = useSyncExternalStore(
    subscribe,
    getLanguage,
    () => "zh-CN" as Language,
  );
  return { language: current, setLanguage, t };
}
