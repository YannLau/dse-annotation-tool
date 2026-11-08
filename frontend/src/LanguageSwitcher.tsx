import { useI18n } from "./i18n";
import type { Language } from "./i18nCore";

export default function LanguageSwitcher() {
  const { language, setLanguage, t } = useI18n();
  return (
    <label className="language-switcher">
      <span aria-hidden="true">◎</span>
      <select
        id="ui-language"
        aria-label={t("界面语言")}
        title={t("界面语言")}
        value={language}
        onChange={(event) => setLanguage(event.target.value as Language)}
      >
        <option value="zh-CN">简体中文</option>
        <option value="en">English</option>
      </select>
    </label>
  );
}
