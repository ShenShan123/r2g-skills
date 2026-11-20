import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { BadgeCheck, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, Image as ImageIcon, Loader2, X } from "lucide-react";
import { api, Design } from "../lib/api";
import { StepTrack } from "./ui";
import { t, tb } from "../lib/i18n";

type View = { stage: string; label: string; available: boolean; caption: string;
  state: string; source?: string; source_sha256?: string; geometry_sha256?: string; captured_at?: number;
  repair_attempted?: boolean; signoff_status?: string; run?: string; reused_from?: string;
  metrics?: { sampled?: boolean; cell_types?: number; logic_cells?: number; placed_cells?: number; clock_cells?: number;
    antenna_cells?: number; route_segments?: number; power_straps?: number; clock_segments?: number;
    layers?: Record<string, { segments: number; color: string }> } };
const STAGES = ["synth", "floorplan", "placement", "cts", "route", "final"];
const LABELS = ["综合", "布局规划", "单元放置", "时钟树", "详细布线", "最终版图"];

const fmt = (n?: number) => new Intl.NumberFormat("en-US", (n ?? 0) >= 10000 ? { notation: "compact", maximumFractionDigits: 1 } : {}).format(n ?? 0);

/** One line under each thumbnail: the number that stage adds to the picture. */
function summary(v: View): string {
  const m = v.metrics ?? {};
  if (!v.available) return "";
  switch (v.stage) {
    case "synth": return m.cell_types ? t("{n} 种单元", { n: fmt(m.cell_types) }) : "";
    case "floorplan": return m.power_straps ? t("{n} 条电源条", { n: fmt(m.power_straps) }) : "";
    case "placement": return (m.logic_cells ?? m.placed_cells) ? t("{n} 个单元", { n: fmt(m.logic_cells ?? m.placed_cells) }) : "";
    case "cts": return m.clock_cells ? t("{n} 个时钟缓冲", { n: fmt(m.clock_cells) }) : "";
    case "route": return m.route_segments ? t("{n} 段布线", { n: fmt(m.route_segments) }) : "";
    default: return m.antenna_cells ? t("{n} 个天线二极管", { n: fmt(m.antenna_cells) })
      : m.route_segments ? t("{n} 段布线", { n: fmt(m.route_segments) }) : "";
  }
}

function Swatch({ color, round, children }: { color: string; round?: boolean; children: ReactNode }) {
  return <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
    <span className={`inline-block h-2.5 w-2.5 ${round ? "rounded-full" : "rounded-[3px]"}`} style={{ background: color }} />{children}</span>;
}

/** What the colours in a layout view mean (the images carry no text of their own). */
function Legend({ layers }: { layers?: Record<string, { segments: number; color: string }> }) {
  return <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted">
    <Swatch color="linear-gradient(90deg,#0e465c,#22d3ee,#fde047)">{t("单元密度")}</Swatch>
    <Swatch color="#3f5f8c">{t("电源网络")}</Swatch>
    <Swatch color="#fb923c" round>{t("时钟树")}</Swatch>
    <Swatch color="#f0abfc" round>{t("天线二极管")}</Swatch>
    <Swatch color="#fbbf24">{t("IO 引脚")}</Swatch>
    {layers && Object.keys(layers).length > 0 && <span className="inline-flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <span className="text-faint">{t("布线层")}</span>
      {Object.entries(layers).map(([name, l]) => <Swatch key={name} color={l.color}><span className="font-mono">{name}</span></Swatch>)}
    </span>}
  </div>;
}

function EnlargedView({ view, url, name, index, total, onPrevious, onNext, onClose }: {
  view: View; url: string; name: string; index: number; total: number;
  onPrevious?: () => void; onNext?: () => void; onClose: () => void;
}) {
  const viewLabel = t(LABELS[STAGES.indexOf(view.stage)] ?? view.label);
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const id = useId();
  const navigation = useRef({ previous: onPrevious, next: onNext });
  navigation.current = { previous: onPrevious, next: onNext };
  const [imageError, setImageError] = useState(false);
  useEffect(() => { setImageError(false); }, [url]);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById("root");
    const wasInert = root?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
      if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && !event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault(); event.stopPropagation();
        (event.key === "ArrowLeft" ? navigation.current.previous : navigation.current.next)?.();
      }
      if (event.key === "Tab") {
        const controls = dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], summary");
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (!Array.from(controls).includes(document.activeElement as HTMLElement)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      if (root) root.inert = wasInert;
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [onClose]);
  return createPortal(<div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm sm:p-6"
    onClick={event => { event.stopPropagation(); if (event.target === event.currentTarget) onClose(); }}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-caption`}
      className="max-h-[calc(100dvh-2rem)] w-full max-w-6xl overflow-y-auto rounded-2xl border border-line bg-panel shadow-xl">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h2 id={`${id}-title`} className="font-semibold">{viewLabel}</h2><span className="shrink-0 text-xs text-faint" aria-live="polite">{index + 1} / {total}</span></div><p className="truncate text-xs text-muted">{name}</p></div>
        <a className="btn-ghost px-2 py-1.5 text-xs" href={url} target="_blank" rel="noreferrer" aria-label={t("打开原图")}><ExternalLink size={15} /><span className="hidden sm:inline">{t("原图")}</span></a>
        <button ref={closeButton} type="button" aria-label={t("关闭版图大图")} className="rounded-lg p-2 text-muted hover:bg-panel2" onClick={onClose}><X size={18} /></button>
      </div>
      <div className="relative flex min-h-48 items-center justify-center bg-[#0b1220]">
        {imageError ? <p role="alert" className="p-6 text-sm text-muted">{t("版图暂时无法加载，请关闭后重试。")}</p>
          : <img key={url} src={url} alt={t("{name} {label}放大图", { name, label: viewLabel })} onError={() => setImageError(true)} className="max-h-[70dvh] w-full object-contain" />}
        <button type="button" aria-label={t("上一张版图")} title={t("上一张（←）")} disabled={!onPrevious} onClick={onPrevious}
          className="absolute left-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-panel/90 text-fg shadow-sm transition hover:bg-panel focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-default disabled:opacity-30 sm:left-4"><ChevronLeft size={22} /></button>
        <button type="button" aria-label={t("下一张版图")} title={t("下一张（→）")} disabled={!onNext} onClick={onNext}
          className="absolute right-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-panel/90 text-fg shadow-sm transition hover:bg-panel focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-default disabled:opacity-30 sm:right-4"><ChevronRight size={22} /></button>
      </div>
      <div className="border-t border-line px-4 py-3 text-xs text-muted">
        <p id={`${id}-caption`}>{tb(view.caption)}</p>
        {view.stage !== "synth" && <div className="mt-2.5"><Legend layers={view.metrics?.layers} /></div>}
        {view.metrics?.sampled && <p className="mt-1 text-faint">{t("大设计预览已抽样，完整数据保留在设计产物中。")}</p>}
        {view.source && <details className="mt-2 text-[10px] text-faint"><summary className="cursor-pointer">{t("产物来源")}</summary><p className="mt-1 break-all">{view.source}</p></details>}
      </div>
    </div>
  </div>, document.body);
}

export default function StageViews({ jobId, d }: { jobId: string; d: Design }) {
  const [expanded, setExpanded] = useState(false);
  const [views, setViews] = useState<View[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const timelineId = useId();
  const closeImage = useCallback(() => setSelected(null), []);
  useEffect(() => { setExpanded(false); setSelected(null); setViews([]); setError(""); }, [jobId, d.name]);
  useEffect(() => {
    if (!expanded) return;
    let alive = true;
    const refresh = () => api.stageViews(jobId, d.name, "final").then(r => { if (alive) { setViews(r.stages); setError(""); } })
      .catch(() => { if (alive) setError(t("阶段版图暂时无法加载，请收起后重试")); });
    refresh();
    const timer = d.status === "running" ? setInterval(refresh, 5000) : undefined;
    return () => { alive = false; clearInterval(timer); };
  }, [expanded, jobId, d.name, d.stage, d.status, d.updated]);
  const repaired = Object.prototype.hasOwnProperty.call(d.detail ?? {}, "repair") || d.stage === "repair"
    || views.some(v => v.stage === "final" && v.repair_attempted);
  const items: View[] = STAGES.map((stage, i) => views.find(v => v.stage === stage) ?? {
    stage, label: LABELS[i], available: false, caption: "等待该阶段产物", state: "pending",
  });
  const available = items.filter(v => v.available);
  const activeIndex = available.findIndex(v => v.stage === selected);
  const active = available[activeIndex];
  const imageUrl = (v: View) => `/api/jobs/${encodeURIComponent(jobId)}/designs/${encodeURIComponent(d.name)}/stage-views/${v.stage}?version=final&v=${v.geometry_sha256 ?? v.source_sha256 ?? v.captured_at ?? d.updated}`;
  return <div aria-label={t("{name} 阶段版图", { name: d.name })}>
    <StepTrack stage={d.stage} status={d.status} repaired={repaired} />
    <button type="button" className="mt-3 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted transition hover:bg-panel2 hover:text-fg"
      aria-expanded={expanded} aria-controls={timelineId} onClick={() => { setExpanded(!expanded); setSelected(null); }}>
      {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      {expanded ? t("收起各阶段版图演化") : t("显示各阶段版图演化")}
    </button>
    {expanded && <div id={timelineId} className="mt-3 rounded-2xl border border-line bg-panel2/40 p-4" aria-label={t("最终版本各阶段版图演化")}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <div className="flex items-baseline gap-2"><h4 className="text-sm font-semibold text-fg">{t("版图演化")}</h4>
          <span className="text-[11px] text-faint">{t("最终版本 · 点击缩略图放大")}<span className="ml-1 sm:hidden">{t("· 左右滑动查看")}</span></span></div>
        {views.length > 0 && !error && <Legend />}
      </div>
      {error ? <p role="alert" className="py-3 text-xs text-bad">{error}</p>
        : !views.length ? <div className="flex items-center gap-2 py-4 text-xs text-muted"><Loader2 size={15} className="animate-spin" /> {t("正在读取阶段版图…")}</div>
        : <div className="-mx-1 overflow-x-auto px-1 pb-1" role="region" aria-label={t("可横向滚动的版图演化")} tabIndex={0}>
          <ol className="grid min-w-[720px] grid-cols-6 gap-3 py-1">
            {items.map((view, i) => {
              const last = view.stage === "final";
              const clean = last && view.available && view.signoff_status === "clean";
              const note = summary(view);
              return <li key={view.stage} className="min-w-0">
                <button type="button" disabled={!view.available} title={tb(view.caption)} aria-label={last ? t("放大最终版图") : t("放大{label}版图", { label: t(LABELS[i]) })}
                  className={`group block w-full text-left focus-visible:outline-none disabled:cursor-default`}
                  onClick={() => setSelected(view.stage)}>
                  <div className={`relative aspect-square overflow-hidden rounded-xl bg-[#0b1220] shadow-card ring-1 transition duration-200
                    ${last && view.available ? "ring-2 ring-ok/70" : "ring-line"}
                    ${view.available ? "group-hover:-translate-y-0.5 group-hover:shadow-lg group-hover:ring-accent group-focus-visible:ring-2 group-focus-visible:ring-accent" : ""}`}>
                    {view.available ? <img src={imageUrl(view)} alt={t("{label}缩略图", { label: t(LABELS[i]) })} loading="lazy"
                        className="h-full w-full object-contain transition duration-300 group-hover:scale-[1.03]" />
                      : <div className="flex h-full flex-col items-center justify-center gap-1.5 text-slate-500"><ImageIcon size={20} /><span className="text-[10px]">{view.state === "pending" ? t("待生成") : view.state === "unverified" ? t("未确认") : t("未保留")}</span></div>}
                    {last && view.available && view.repair_attempted && <span className="absolute right-2 top-2 rounded-full bg-fuchsia-500/85 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur-sm">{t("已修复")}</span>}
                  </div>
                  <div className="mt-2 px-0.5">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-fg"><span className={`flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold ${view.available ? "bg-accent/15 text-accent" : "bg-panel2 text-faint"}`}>{i + 1}</span>{t(LABELS[i])}{clean && <BadgeCheck size={13} className="text-ok" aria-label={t("签核干净")} />}</div>
                    <div className="truncate text-[11px] text-faint">{note || "\u00a0"}</div>
                  </div>
                </button>
              </li>;
            })}
          </ol>
        </div>}
    </div>}
    {active && <EnlargedView view={active} url={imageUrl(active)} name={d.name} index={activeIndex} total={available.length}
      onPrevious={activeIndex > 0 ? () => setSelected(available[activeIndex - 1].stage) : undefined}
      onNext={activeIndex < available.length - 1 ? () => setSelected(available[activeIndex + 1].stage) : undefined}
      onClose={closeImage} />}
  </div>;
}
