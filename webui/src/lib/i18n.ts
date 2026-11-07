// UI language: Chinese (the source text) by default, English on request.
// Strings are keyed by their Chinese source, so zh mode renders the source unchanged.
// `?lang=en|zh` in the URL wins over the saved choice (a headless screenshot can
// force English); the toggle saves the choice in localStorage.
import { useSyncExternalStore } from "react";
import { EN, PATTERNS } from "./i18n-en";

export type Lang = "zh" | "en";
const KEY = "r2g-lang";
const isLang = (v: unknown): v is Lang => v === "zh" || v === "en";

function initial(): Lang {
  try {
    const q = new URLSearchParams(window.location.search).get("lang");
    if (isLang(q)) return q;
  } catch { /* no window.location */ }
  try {
    const s = localStorage.getItem(KEY);
    if (isLang(s)) return s;
  } catch { /* private mode */ }
  return "zh";
}

let lang: Lang = initial();
const listeners = new Set<() => void>();

function apply() {
  document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  document.title = t("r2g · RTL 到图数据集");
}

export const getLang = () => lang;

export function setLang(next: Lang) {
  if (next === lang) return;
  lang = next;
  try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  try {   // keep an explicit ?lang= in step, or a reload would switch back
    const u = new URL(window.location.href);
    if (u.searchParams.has("lang")) { u.searchParams.set("lang", next); history.replaceState(history.state, "", u); }
  } catch { /* ignore */ }
  apply();
  listeners.forEach((f) => f());
}

export function useLang(): Lang {
  return useSyncExternalStore((f) => { listeners.add(f); return () => { listeners.delete(f); }; }, getLang);
}

type Vars = Record<string, string | number | null | undefined>;

/** `{k}` -> value; `{k:noun}` (English only) -> "1 noun" / "2 nouns". */
function fill(s: string, vars?: Vars): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)(?::(\w+))?\}/g, (m, k: string, noun?: string) => {
    if (!(k in vars)) return m;
    const v = vars[k];
    return noun ? `${v} ${noun}${Number(v) === 1 ? "" : "s"}` : String(v ?? "");
  });
}

const has = (k: string) => Object.prototype.hasOwnProperty.call(EN, k);

/** A UI literal: the Chinese source in zh mode, its English entry in en mode.
 *  A "|context" suffix tells apart one Chinese word with two English senses
 *  ("任务" the nav item vs "任务|event" a log row); zh mode drops the suffix. */
export function t(zh: string, vars?: Vars): string {
  if (lang === "en" && has(zh)) return fill(EN[zh], vars);
  const bar = zh.indexOf("|");
  return fill(bar < 0 ? zh : zh.slice(0, bar), vars);
}

/** English for any Chinese string, by exact entry, then pattern rules, then
 *  piece by piece around " · " / "；". Unknown text falls through unchanged. */
export function en(s: string): string {
  if (has(s)) return EN[s];
  for (const [re, f] of PATTERNS) {
    const m = s.match(re);
    if (m) return f(m, en);
  }
  if (s.includes(" · ")) return s.split(" · ").map(en).join(" · ");
  if (s.includes("；")) return s.split("；").map(en).join("; ");
  if (s.includes("、")) return s.split("、").map(en).join(", ");
  return s;
}

/** A string that came from the backend (stage names, events, verdicts, captions):
 *  translated in en mode, passed through unchanged otherwise. */
export function tb<T>(s: T): T {
  return (lang === "en" && typeof s === "string" && s ? en(s) : s) as T;
}

apply();   // last: t() needs everything above initialised
