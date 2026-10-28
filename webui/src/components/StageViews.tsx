import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, Image as ImageIcon, Loader2, X } from "lucide-react";
import { api, Design } from "../lib/api";
import { StepTrack } from "./ui";

type View = { stage: string; label: string; available: boolean; caption: string;
  state: string; source?: string; source_sha256?: string; geometry_sha256?: string; captured_at?: number;
  repair_attempted?: boolean; signoff_status?: string; run?: string; reused_from?: string;
  metrics?: { sampled?: boolean } };
const STAGES = ["synth", "floorplan", "placement", "cts", "route", "final"];
const LABELS = ["综合", "Floorplan", "Placement", "CTS", "Route", "最终版图"];

function EnlargedView({ view, url, name, index, total, onPrevious, onNext, onClose }: {
  view: View; url: string; name: string; index: number; total: number;
  onPrevious?: () => void; onNext?: () => void; onClose: () => void;
}) {
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
        <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><h2 id={`${id}-title`} className="font-semibold">{view.label}</h2><span className="shrink-0 text-xs text-faint" aria-live="polite">{index + 1} / {total}</span></div><p className="truncate text-xs text-muted">{name}</p></div>
        <a className="btn-ghost px-2 py-1.5 text-xs" href={url} target="_blank" rel="noreferrer" aria-label="打开原图"><ExternalLink size={15} /><span className="hidden sm:inline">原图</span></a>
        <button ref={closeButton} type="button" aria-label="关闭版图大图" className="rounded-lg p-2 text-muted hover:bg-panel2" onClick={onClose}><X size={18} /></button>
      </div>
      <div className="relative flex min-h-48 items-center justify-center bg-bg">
        {imageError ? <p role="alert" className="p-6 text-sm text-muted">版图暂时无法加载，请关闭后重试。</p>
          : <img key={url} src={url} alt={`${name} ${view.label}放大图`} onError={() => setImageError(true)} className="max-h-[70dvh] w-full object-contain" />}
        <button type="button" aria-label="上一张版图" title="上一张（←）" disabled={!onPrevious} onClick={onPrevious}
          className="absolute left-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-panel/90 text-fg shadow-sm transition hover:bg-panel focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-default disabled:opacity-30 sm:left-4"><ChevronLeft size={22} /></button>
        <button type="button" aria-label="下一张版图" title="下一张（→）" disabled={!onNext} onClick={onNext}
          className="absolute right-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-line bg-panel/90 text-fg shadow-sm transition hover:bg-panel focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-default disabled:opacity-30 sm:right-4"><ChevronRight size={22} /></button>
      </div>
      <div className="border-t border-line px-4 py-3 text-xs text-muted">
        <p id={`${id}-caption`}>{view.caption}</p>
        {view.metrics?.sampled && <p className="mt-1 text-faint">大设计预览已抽样，完整数据保留在设计产物中。</p>}
        {view.source && <details className="mt-2 text-[10px] text-faint"><summary className="cursor-pointer">产物来源</summary><p className="mt-1 break-all">{view.source}</p></details>}
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
      .catch(() => { if (alive) setError("阶段版图暂时无法加载，请收起后重试"); });
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
  return <div aria-label={`${d.name} 阶段版图`}>
    <StepTrack stage={d.stage} status={d.status} repaired={repaired} />
    <button type="button" className="mt-3 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted transition hover:bg-panel2 hover:text-fg"
      aria-expanded={expanded} aria-controls={timelineId} onClick={() => { setExpanded(!expanded); setSelected(null); }}>
      {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      {expanded ? "收起各阶段版图演化" : "显示各阶段版图演化"}
    </button>
    {expanded && <div id={timelineId} className="mt-2 rounded-xl border border-line p-3" aria-label="最终版本各阶段版图演化">
      <p className="mb-2 text-[11px] text-faint">最终版本 · 点击缩略图放大<span className="ml-2 sm:hidden">· 左右滑动查看</span></p>
      {error ? <p role="alert" className="py-3 text-xs text-bad">{error}</p>
        : !views.length ? <div className="flex items-center gap-2 py-4 text-xs text-muted"><Loader2 size={15} className="animate-spin" /> 正在读取阶段版图…</div>
        : <div className="overflow-x-auto pb-1" role="region" aria-label="可横向滚动的版图演化" tabIndex={0}>
          <ol className="flex min-w-[660px] gap-5 py-1">
            {items.map((view, i) => <li key={view.stage} className="relative min-w-0 flex-1">
              <button type="button" disabled={!view.available} title={view.caption} aria-label={view.stage === "final" ? "放大最终版图" : `放大${LABELS[i]}版图`}
                className="w-full overflow-hidden rounded-lg border border-line text-left transition hover:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-default disabled:hover:border-line"
                onClick={() => setSelected(view.stage)}>
                <div className="flex aspect-[8/5] items-center justify-center bg-bg">
                  {view.available ? <img src={imageUrl(view)} alt={`${LABELS[i]}缩略图`} loading="lazy" className="h-full w-full object-contain" />
                    : <div className="flex flex-col items-center gap-1 text-faint"><ImageIcon size={18} /><span className="text-[10px]">{view.state === "pending" ? "待生成" : view.state === "unverified" ? "未确认" : "未保留"}</span></div>}
                </div>
                <div className="truncate px-1.5 py-2 text-center text-[11px] text-muted">{LABELS[i]}</div>
              </button>
              {i < items.length - 1 && <ArrowRight aria-hidden size={13} className="absolute -right-[17px] top-1/2 -translate-y-1/2 text-faint" />}
            </li>)}
          </ol>
        </div>}
    </div>}
    {active && <EnlargedView view={active} url={imageUrl(active)} name={d.name} index={activeIndex} total={available.length}
      onPrevious={activeIndex > 0 ? () => setSelected(available[activeIndex - 1].stage) : undefined}
      onNext={activeIndex < available.length - 1 ? () => setSelected(available[activeIndex + 1].stage) : undefined}
      onClose={closeImage} />}
  </div>;
}
