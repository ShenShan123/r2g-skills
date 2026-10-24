import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Download, Square, X, Image as ImageIcon, FileText, LayoutGrid, Grid3x3, ChevronDown, ChevronRight,
         CheckCircle2, XCircle, AlertTriangle, Circle, MessagesSquare, Wand2, Loader2, Search, PackageOpen } from "lucide-react";
import { api, askAgent, Design, fmtClock, Job, JobEvent, OUTPUT_LABEL } from "../lib/api";
import { StatusBadge, Stat, StepTrack, isActive } from "../components/ui";

const KIND_ICON: Record<string, any> = { ok: CheckCircle2, fail: XCircle, warn: AlertTriangle };
const KIND_CLS: Record<string, string> = { ok: "text-ok", fail: "text-bad", warn: "text-warn", start: "text-run" };
const STAGE_ZH: Record<string, string> = {
  prepare: "准备", synth: "综合", size: "定尺寸", flow: "布局布线", signoff: "签核", repair: "自动修复",
  graph: "生成数据集", verify: "数据集校验", preview: "版图预览", ingest: "写入知识库", done: "完成",
};

export default function JobDetail() {
  const { id = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const lastEv = useRef(0);

  useEffect(() => {
    let alive = true; lastEv.current = 0; setEvents([]);
    let t: any;
    // the page keeps at most the last 500 events; a big batch polls more slowly
    const tick = async () => {
      try {
        const [j, ev] = await Promise.all([api.job(id), api.events(id, lastEv.current, lastEv.current ? 0 : 300)]);
        if (!alive) return;
        setJob(j);
        if (ev.length) { lastEv.current = ev[ev.length - 1].id; setEvents((p) => [...p, ...ev].slice(-500)); }
        t = setTimeout(tick, (j.designs?.length ?? 0) > BIG ? 5000 : 2000);
      } catch { t = setTimeout(tick, 5000); }
    };
    tick();
    return () => { alive = false; clearTimeout(t); };
  }, [id]);

  if (!job) return <div className="p-10 text-muted">加载中…</div>;
  const ds = job.designs ?? [];
  const passed = ds.filter((d) => d.status === "passed").length;
  const failed = ds.filter((d) => d.status === "failed").length;
  const active = isActive(job.status);
  const started = events[0]?.ts ?? job.created;
  const minutes = Math.max(0, Math.round(((active ? Date.now() / 1000 : job.updated) - started) / 60));
  const selected = ds.find((d) => d.name === sel) ?? null;
  const now = [...events].reverse().find((e) => e.kind === "start");

  return (
    <div className="mx-auto max-w-4xl px-4 py-6 md:px-8 md:py-8">
      <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft size={15} /> 全部任务</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold md:text-2xl">{job.title}</h1>
          <div className="mt-1 text-xs text-faint">{OUTPUT_LABEL[job.spec?.output ?? "graph"]} · {job.spec?.platform} · {job.id}</div>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* what is going on, in one sentence, with the one action that matters now */}
      <div className={`card mt-5 flex flex-wrap items-center gap-4 p-4 md:p-5 ${!active && passed ? "border-ok/40" : ""}`}>
        <div className="min-w-0 flex-1">
          {active ? (
            <>
              <div className="flex items-center gap-2 font-semibold"><Loader2 size={17} className="animate-spin text-run" />
                正在处理 · 已完成 {passed + failed}/{ds.length}</div>
              <div className="mt-1 truncate text-sm text-muted">{now ? `${now.design ?? ""} ${now.message}` : "准备中"} · 已用 {minutes} 分钟</div>
            </>) : (
            <>
              <div className="font-semibold">{passed ? `完成：${passed}/${ds.length} 个设计通过` : `没有设计通过（${failed}/${ds.length} 失败）`}</div>
              <div className="mt-1 text-sm text-muted">
                {passed ? "通过的设计都已签核（DRC、LVS 干净）并通过数据集校验，已打包。" : "点开失败的设计查看原因，或让 agent 帮你分析。"}
                {passed > 0 && failed > 0 && ` ${failed} 个失败的设计不在数据集里。`} 用时 {minutes} 分钟。
              </div>
            </>)}
        </div>
        {active && passed > 0 && <PartialDownload id={id} />}
        {active && <button className="btn-ghost" onClick={() => api.cancel(id)}><Square size={14} /> 取消</button>}
        {job.result?.package && <a className="btn-primary" href={`/api/jobs/${id}/download`}><Download size={16} /> 下载数据集</a>}
      </div>

      {ds.length > BIG ? <BatchList jobId={id} ds={ds} onOpen={setSel} /> : (
        <div className="mt-4 space-y-3">
          {ds.map((d) => <DesignCard key={d.name} jobId={id} d={d} onOpen={() => setSel(d.name)} />)}
        </div>)}

      <div className="mt-6">
        <button className="flex items-center gap-1.5 text-sm text-muted hover:text-fg" onClick={() => setShowLog(!showLog)}>
          {showLog ? <ChevronDown size={16} /> : <ChevronRight size={16} />} 详细过程（最近 {events.length} 条）
        </button>
        {showLog && (
          <div className="card mt-2 max-h-[420px] space-y-0.5 overflow-y-auto p-2">
            {events.map((e) => {
              const I = KIND_ICON[e.kind] ?? Circle;
              return (
                <div key={e.id} className="flex gap-2.5 rounded-lg px-2 py-1.5 text-[13px]">
                  <span className="w-14 shrink-0 font-mono text-[11px] leading-5 text-faint">{fmtClock(e.ts)}</span>
                  <I size={14} className={`mt-0.5 shrink-0 ${KIND_CLS[e.kind] ?? "text-faint"}`} />
                  <div className="min-w-0 leading-5"><span className="font-mono text-xs text-accent">{e.design ?? "任务"}</span>{" "}
                    <span className={e.kind === "fail" ? "text-bad" : ""}>{e.message}</span></div>
                </div>);
            })}
          </div>)}
      </div>
      {selected && <DesignPanel jobId={id} d={selected} output={job.spec?.output ?? "graph"} onClose={() => setSel(null)} />}
    </div>
  );
}

const DOT: Record<string, string> = { passed: "bg-ok", failed: "bg-bad", running: "bg-run", pending: "bg-line", cancelled: "bg-warn" };
const BIG = 12;      // more designs than this: a filterable list instead of cards
const PAGE = 50;

function PartialDownload({ id }: { id: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className="btn-ghost" disabled={busy} title="把目前已通过的设计先打包下载"
      onClick={async () => { setBusy(true); try { const r = await api.packageNow(id); window.location.href = r.url; } finally { setBusy(false); } }}>
      {busy ? <Loader2 size={15} className="animate-spin" /> : <PackageOpen size={15} />} 下载已完成的部分
    </button>);
}

/** Group key of a failure: the stage and the message without file names, line
 *  numbers or counts -- 2000 designs with the same yosys error are one group. */
function reasonKey(d: Design): string {
  const msg = (d.detail?.reason ?? "").replace(/^[^：]*失败：/, "").replace(/^[^\s：]+\.(s?v|s?vh|v)[^：]*：/i, "")
    .replace(/\d+/g, "N").slice(0, 60);
  return `${STAGE_ZH[d.detail?.failed_stage] ?? d.detail?.failed_stage ?? "?"}：${msg}`;
}

function BatchList({ jobId, ds, onOpen }: { jobId: string; ds: Design[]; onOpen: (n: string) => void }) {
  const [filter, setFilter] = useState<string>("all");
  const [reason, setReason] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    ds.forEach((d) => { c[d.status] = (c[d.status] ?? 0) + 1; });
    return c;
  }, [ds]);
  const reasons = useMemo(() => {
    const m = new Map<string, number>();
    ds.filter((d) => d.status === "failed").forEach((d) => m.set(reasonKey(d), (m.get(reasonKey(d)) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [ds]);
  const rows = ds.filter((d) => (filter === "all" || d.status === filter) && (!reason || reasonKey(d) === reason)
    && (!q || d.name.toLowerCase().includes(q.toLowerCase())));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  const tabs: [string, string][] = [["all", "全部"], ["running", "运行中"], ["pending", "排队中"], ["passed", "通过"], ["failed", "失败"]];
  return (
    <div className="mt-4 space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => { setFilter(k); setReason(null); setPage(0); }}
            className={`rounded-xl border px-3 py-2 text-left ${filter === k ? "border-accent bg-accent/10" : "border-line bg-panel hover:bg-panel2"}`}>
            <div className="text-xs text-muted">{l}</div>
            <div className={`text-lg font-semibold ${k === "passed" ? "text-ok" : k === "failed" && counts.failed ? "text-bad" : ""}`}>
              {k === "all" ? ds.length : counts[k] ?? 0}</div>
          </button>))}
      </div>
      {reasons.length > 0 && (
        <div className="card p-4">
          <div className="text-sm font-medium">失败原因（点一下只看这一类）</div>
          <div className="mt-2 space-y-1">
            {reasons.map(([r, n]) => (
              <button key={r} onClick={() => { setFilter("failed"); setReason(reason === r ? null : r); setPage(0); }}
                className={`flex w-full items-center gap-3 rounded-lg px-2 py-1 text-left text-sm ${reason === r ? "bg-bad/10" : "hover:bg-panel2"}`}>
                <span className="w-10 shrink-0 text-right font-semibold text-bad">{n}</span><span className="truncate text-muted">{r}</span>
              </button>))}
          </div>
        </div>)}
      <div className="card">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <Search size={15} className="text-faint" />
          <input className="w-full bg-transparent py-1 text-sm outline-none" placeholder="按设计名搜索" value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          <span className="shrink-0 text-xs text-faint">{rows.length} 个</span>
        </div>
        <div className="divide-y divide-line">
          {shown.map((d) => (
            <button key={d.name} onClick={() => onOpen(d.name)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-panel2/60">
              <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[d.status] ?? "bg-line"}`} />
              <span className="w-40 shrink-0 truncate font-mono text-sm sm:w-56">{d.name}</span>
              <span className={`min-w-0 flex-1 truncate text-xs ${d.status === "failed" ? "text-bad" : "text-muted"}`}>{plain(d)}</span>
              <StatusBadge status={d.status} />
            </button>))}
          {shown.length === 0 && <div className="px-3 py-6 text-center text-sm text-muted">没有符合条件的设计</div>}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-center gap-3 border-t border-line px-3 py-2 text-sm">
            <button className="btn-ghost px-3 py-1" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
            <span className="text-muted">{page + 1} / {pages}</span>
            <button className="btn-ghost px-3 py-1" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>下一页</button>
          </div>)}
      </div>
      {counts.failed > 0 && (
        <Link className="btn-ghost" to={askAgent(`任务 ${jobId} 有 ${counts.failed} 个设计失败，帮我按原因归类，告诉我哪些是 RTL 问题、哪些可以修`)}>
          <MessagesSquare size={15} /> 让 agent 归类分析失败的设计</Link>)}
    </div>
  );
}

function plain(d: Design): string {
  const v = d.detail?.verify;
  if (d.status === "pending") return "排队中，等待空闲的算力";
  if (d.status === "passed") return v ? `完成：签核通过，数据集校验 ${v.passed}/${v.total} 项全部通过` : "完成";
  if (d.status === "failed") return d.detail?.reason ?? "失败";
  if (d.status === "cancelled") return "已取消";
  return `正在${STAGE_ZH[d.stage] ?? d.stage}…`;
}

function DesignCard({ jobId, d, onOpen }: { jobId: string; d: Design; onOpen: () => void }) {
  const s = d.detail?.sizing; const so = d.detail?.signoff;
  const fixes: any[] = d.detail?.frontend_fixes ?? [];
  const repaired = Array.isArray(d.detail?.repair) && d.detail.repair.length > 0;
  return (
    <div className="card p-4 md:p-5">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1 truncate font-mono font-semibold">{d.name}</div>
        <StatusBadge status={d.status} />
        <button className="btn-ghost px-3 py-1.5" onClick={onOpen}>详情</button>
      </div>
      <div className="mt-4"><StepTrack stage={d.stage} status={d.status} repaired={repaired} /></div>
      <p className={`mt-3 text-sm ${d.status === "failed" ? "text-bad" : "text-muted"}`}>{plain(d)}</p>
      {(s || so) && (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
          {s && <span>{s.cells} 个单元 · die {s.die_side_um} µm · 时钟 {s.period_ns} ns</span>}
          {so && <span>DRC <b className={so.drc === "clean" ? "text-ok" : "text-bad"}>{so.drc === "clean" ? "干净" : `${so.drc_violations ?? "?"} 处违例`}</b>
            {" · "}LVS <b className={so.lvs === "clean" ? "text-ok" : "text-bad"}>{so.lvs === "clean" ? "一致" : so.lvs}</b></span>}
        </div>)}
      {(fixes.length > 0 || repaired) && (
        <div className="mt-3 rounded-xl bg-accent2/10 px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-accent2"><Wand2 size={13} /> 自动修复</div>
          <ul className="mt-1 space-y-0.5 text-muted">
            {fixes.map((f, i) => <li key={i}>{f.detail}</li>)}
            {repaired && <li>签核未通过后自动诊断并修复（{d.detail.repair.length} 步，见详情）</li>}
          </ul>
        </div>)}
      {d.status === "failed" && (
        <Link className="btn-ghost mt-3" to={askAgent(`任务 ${jobId} 里的设计 ${d.name} 失败了，帮我分析原因并告诉我怎么改`)}>
          <MessagesSquare size={15} /> 让 agent 分析原因</Link>)}
    </div>
  );
}

const LOG_STAGES = ["synth", "flow", "signoff", "repair", "graph", "verify"];

function DesignPanel({ jobId, d, output, onClose }: { jobId: string; d: Design; output: string; onClose: () => void }) {
  const [tab, setTab] = useState<"overview" | "images" | "layout" | "log">(output === "image" ? "images" : "overview");
  const [rep, setRep] = useState<any>(null);
  const [logStage, setLogStage] = useState("flow");
  const [log, setLog] = useState("");
  const [imgOk, setImgOk] = useState(true);
  useEffect(() => { setRep(null); api.reports(jobId, d.name).then(setRep).catch(() => {}); }, [jobId, d.name, d.status]);
  useEffect(() => {
    if (tab === "log") api.log(jobId, d.name, logStage).then((r) => setLog(r.log_tail ?? r.error ?? "")).catch(() => setLog(""));
  }, [tab, logStage, jobId, d.name, d.stage]);
  const s = d.detail?.sizing;
  const sum = useMemo(() => rep?.ppa?.summary ?? {}, [rep]);
  const geo = rep?.ppa?.geometry ?? {};
  const fmt = (x: any, n = 3) => (typeof x === "number" ? x.toFixed(n) : null);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40 backdrop-blur-[2px]" onClick={onClose}>
      <div className="flex h-full w-full flex-col border-l border-line bg-panel md:w-[640px]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div><div className="font-mono text-lg font-semibold">{d.name}</div><div className="text-xs text-muted">{d.project}</div></div>
          <div className="flex items-center gap-2"><StatusBadge status={d.status} />
            {d.status === "passed" && <a className="btn-ghost px-3 py-1.5" href={`/api/jobs/${jobId}/designs/${d.name}/download`}><Download size={14} /> 下载</a>}
            <button className="rounded-lg p-1.5 text-muted hover:bg-panel2" onClick={onClose}><X size={18} /></button></div>
        </div>
        <div className="flex gap-1 border-b border-line px-4">
          {([["overview", "概览", LayoutGrid], ...(output === "image" ? [["images", "图像", Grid3x3]] : []),
             ["layout", "版图", ImageIcon], ["log", "日志", FileText]] as [string, string, any][]).map(([k, l, I]) => (
            <button key={k} onClick={() => setTab(k as any)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm ${tab === k ? "border-accent text-accent" : "border-transparent text-muted hover:text-fg"}`}>
              <I size={15} /> {l}</button>))}
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {tab === "overview" && (
            <div className="space-y-5">
              <section>
                <div className="label mb-2">尺寸与约束</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label="综合后单元" value={s?.cells} /><Stat label="面积" value={s ? `${s.area_um2} µm²` : null} />
                  <Stat label="IO 引脚" value={s?.pins} /><Stat label="die 边长" value={s ? `${s.die_side_um} µm` : null}
                    sub={s ? { area: "由面积决定", pins: "由引脚数决定", pdn_floor: "由电源网格下限决定" }[s.binding as string] : ""} />
                  <Stat label="时钟周期" value={s ? `${s.period_ns} ns` : null} sub="按单元数分档" />
                  <Stat label="建立时间裕量" value={fmt(sum?.timing?.setup_wns)} sub="setup WNS (ns)" />
                </div>
              </section>
              <section>
                <div className="label mb-2">PPA（来自版图）</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label="利用率" value={typeof geo.utilization === "number" ? `${(geo.utilization * 100).toFixed(1)} %` : null} />
                  <Stat label="单元面积" value={fmt(geo.stdcell_area_um2, 1) && `${fmt(geo.stdcell_area_um2, 1)} µm²`} />
                  <Stat label="功耗" value={typeof sum?.power?.total_power_w === "number" ? `${(sum.power.total_power_w * 1000).toFixed(3)} mW` : null} />
                </div>
              </section>
              <section>
                <div className="label mb-2">签核（由工具判定）</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label="DRC" value={<Verdict v={rep?.drc?.status} />} sub={rep?.drc ? `${rep.drc.total_violations ?? 0} 处违例` : ""} />
                  <Stat label="LVS" value={<Verdict v={rep?.lvs?.status} />} sub={rep?.lvs?.mismatch_count != null ? `${rep.lvs.mismatch_count} 处不匹配` : ""} />
                  <Stat label="数据集校验" value={rep?.verify ? `${rep.verify.passed}/${rep.verify.total}` : "—"}
                    sub={rep?.graph_dataset?.status ? `manifest ${rep.graph_dataset.status}` : ""} />
                </div>
              </section>
              {Array.isArray(d.detail?.repair) && d.detail.repair.length > 0 && (
                <section>
                  <div className="label mb-2">自动修复记录</div>
                  <div className="space-y-2">
                    {d.detail.repair.map((r: any, i: number) => (
                      <div key={i} className="rounded-xl bg-panel2 px-3 py-2 text-sm">
                        <span className="font-mono text-accent2">{r.strategy ?? r.strategy_id ?? r.id ?? "修复"}</span>{" "}
                        <span className="text-muted">{r.verdict ?? r.result ?? ""} {r.before != null ? `${r.before} → ${r.after}` : ""}</span>
                      </div>))}
                  </div>
                </section>)}
              {d.status === "failed" && (
                <div className="rounded-xl border border-bad/40 bg-bad/10 p-4 text-sm">
                  <div className="font-medium text-bad">失败于：{STAGE_ZH[d.detail?.failed_stage] ?? d.detail?.failed_stage}</div>
                  <div className="mt-1 text-muted">{d.detail?.reason}</div>
                  <Link className="btn-ghost mt-3" to={askAgent(`任务 ${jobId} 里的设计 ${d.name} 失败了，帮我分析原因并告诉我怎么改`)}>
                    <MessagesSquare size={15} /> 让 agent 分析原因</Link>
                </div>)}
            </div>)}
          {tab === "images" && <ImagesTab jobId={jobId} d={d} />}
          {tab === "layout" && (
            imgOk ? <img src={`/api/jobs/${jobId}/designs/${d.name}/layout.png?ts=${d.updated}`} onError={() => setImgOk(false)}
                      className="w-full rounded-xl border border-line bg-black" alt="layout" />
                  : <div className="rounded-xl bg-panel2 p-10 text-center text-sm text-muted">版图预览在设计完成后生成</div>)}
          {tab === "log" && (
            <div>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {LOG_STAGES.map((st) => (
                  <button key={st} onClick={() => setLogStage(st)}
                    className={`chip ${logStage === st ? "bg-accent/15 text-accent" : "bg-panel2 text-muted hover:text-fg"}`}>{STAGE_ZH[st] ?? st}</button>))}
              </div>
              <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-xl bg-bg p-4 font-mono text-[11.5px] leading-5 text-muted">{log || "（暂无日志）"}</pre>
            </div>)}
        </div>
      </div>
    </div>
  );
}

function Verdict({ v }: { v?: string }) {
  if (!v) return <span className="text-faint">—</span>;
  return <span className={v === "clean" ? "text-ok" : "text-bad"}>{v === "clean" ? "干净" : v}</span>;
}

const CH_LABEL: Record<string, string> = {
  cell_density: "单元密度", macro_region: "宏单元区域", rudy: "RUDY（布线需求估计）", pin_rudy: "引脚 RUDY",
  pin_density: "引脚密度", congestion: "拥塞（平滑）", congestion_raw: "拥塞（原始）", ir_drop: "IR drop",
};

function ImagesTab({ jobId, d }: { jobId: string; d: Design }) {
  const [info, setInfo] = useState<any>(null);
  useEffect(() => { api.images(jobId, d.name).then(setInfo).catch(() => setInfo({ available: false })); }, [jobId, d.name, d.status]);
  if (!info) return <div className="text-sm text-muted">加载中…</div>;
  if (!info.available) return <div className="rounded-xl bg-panel2 p-10 text-center text-sm text-muted">图像数据集在设计完成后生成</div>;
  const block = (title: string, sub: string, kind: "feature" | "label", chans: Record<string, any>) => (
    <section className="mb-6">
      <div className="mb-2 flex items-baseline justify-between"><div className="label">{title}</div><div className="text-xs text-faint">{sub}</div></div>
      <div className="grid grid-cols-3 gap-3">
        {Object.entries(chans).map(([k, v]: any) => (
          <figure key={k} className="overflow-hidden rounded-xl border border-line bg-panel2">
            <img src={`/api/jobs/${jobId}/designs/${d.name}/images/${kind}_${k}.png`} alt={k}
              className="aspect-square w-full" style={{ imageRendering: "pixelated" }} />
            <figcaption className="px-2.5 py-2 text-xs">
              <div className="font-medium">{CH_LABEL[k] ?? k}</div>
              <div className="text-faint">最大 {Number(v.max).toPrecision(3)} {v.unit}</div>
            </figcaption>
          </figure>))}
      </div>
    </section>);
  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 text-sm">
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">网格</div><div className="font-semibold">{info.shape?.join(" × ")}</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">GCell</div><div className="font-semibold">{info.gcell_step_um?.[0]?.toFixed(2)} µm</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">特征来源</div><div className="font-semibold">{({ place: "放置后", cts: "CTS 后", floorplan: "floorplan" } as any)[info.feature_stage] ?? info.feature_stage}</div></div>
      </div>
      {block("特征（输入）", "只取自放置后版图，无布线信息", "feature", info.features)}
      {block("标签（预测目标）", "取自布线后结果", "label", info.labels)}
      <p className="text-xs text-faint">颜色仅用于预览（深紫→黄，按 99 分位缩放）；数值在下载包的 features.npz / labels.npz 中，数组为 [x][y]。</p>
    </div>);
}
