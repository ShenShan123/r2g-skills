import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Download, Square, X, Image as ImageIcon, FileText, LayoutGrid, Grid3x3,
         CheckCircle2, XCircle, AlertTriangle, Circle } from "lucide-react";
import { api, Design, fmtClock, Job, JobEvent, Stage, OUTPUT_LABEL } from "../lib/api";
import { StagePipe, StatusBadge, Stat } from "../components/ui";

const KIND_ICON: Record<string, any> = { ok: CheckCircle2, fail: XCircle, warn: AlertTriangle };
const KIND_CLS: Record<string, string> = { ok: "text-ok", fail: "text-bad", warn: "text-warn", start: "text-run" };

export default function JobDetail() {
  const { id = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const lastEv = useRef(0);
  const evBox = useRef<HTMLDivElement>(null);

  useEffect(() => { api.health().then((h) => setStages(h.stages)); }, []);
  useEffect(() => {
    let alive = true; lastEv.current = 0; setEvents([]);
    const tick = async () => {
      try {
        const [j, ev] = await Promise.all([api.job(id), api.events(id, lastEv.current)]);
        if (!alive) return;
        setJob(j);
        if (ev.length) { lastEv.current = ev[ev.length - 1].id; setEvents((p) => [...p, ...ev]); }
      } catch { /* transient */ }
    };
    tick(); const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);
  useEffect(() => { evBox.current?.scrollTo({ top: evBox.current.scrollHeight, behavior: "smooth" }); }, [events.length]);

  if (!job) return <div className="p-10 text-muted">加载中…</div>;
  const ds = job.designs ?? [];
  const passed = ds.filter((d) => d.status === "passed").length;
  const failed = ds.filter((d) => d.status === "failed").length;
  const running = job.status === "running" || job.status === "pending";
  const started = events[0]?.ts ?? job.created;
  const elapsed = Math.round(((running ? Date.now() / 1000 : job.updated) - started) / 60);
  const selected = ds.find((d) => d.name === sel) ?? null;

  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1 overflow-y-auto px-10 py-8">
        <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft size={15} /> 任务</Link>
        <div className="mt-3 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">{job.title}</h1>
            <div className="mt-1 font-mono text-xs text-faint">{job.id} · {job.spec?.platform} · {OUTPUT_LABEL[job.spec?.output ?? "graph"]}</div>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={job.status} />
            {running && <button className="btn-ghost" onClick={() => api.cancel(id)}><Square size={14} /> 取消</button>}
            {job.result?.package && (
              <a className="btn-primary" href={`/api/jobs/${id}/download`}><Download size={16} /> 下载数据集</a>)}
          </div>
        </div>

        <div className="mt-6 grid grid-cols-4 gap-3">
          <Stat label="设计" value={ds.length} />
          <Stat label="通过" value={<span className="text-ok">{passed}</span>} sub="签核 + 数据集校验" />
          <Stat label="失败" value={<span className={failed ? "text-bad" : ""}>{failed}</span>} />
          <Stat label="用时" value={`${Math.max(elapsed, 0)} 分钟`} sub={running ? "进行中" : "已结束"} />
        </div>

        <div className="mt-6 grid grid-cols-1 gap-3 xl:grid-cols-2">
          {ds.map((d) => <DesignCard key={d.name} d={d} stages={stages} onOpen={() => setSel(d.name)} active={sel === d.name} />)}
        </div>
      </div>

      <aside className="flex w-[380px] shrink-0 flex-col border-l border-line bg-panel/50">
        <div className="border-b border-line px-5 py-4">
          <div className="font-semibold">实时进度</div>
          <div className="text-xs text-muted">每一步都来自流程与签核工具的真实输出</div>
        </div>
        <div ref={evBox} className="flex-1 space-y-0.5 overflow-y-auto px-3 py-3">
          {events.map((e) => {
            const I = KIND_ICON[e.kind] ?? Circle;
            return (
              <div key={e.id} className="flex gap-2.5 rounded-lg px-2 py-1.5 text-[13px] hover:bg-panel2/60">
                <I size={14} className={`mt-0.5 shrink-0 ${KIND_CLS[e.kind] ?? "text-faint"}`} />
                <div className="min-w-0">
                  <div className="leading-5"><span className="font-mono text-xs text-accent">{e.design ?? "任务"}</span>{" "}
                    <span className={e.kind === "fail" ? "text-bad" : ""}>{e.message}</span></div>
                  <div className="text-[11px] text-faint">{fmtClock(e.ts)}</div>
                </div>
              </div>);
          })}
        </div>
      </aside>
      {selected && <DesignPanel jobId={id} d={selected} output={job.spec?.output ?? "graph"} onClose={() => setSel(null)} />}
    </div>
  );
}

function DesignCard({ d, stages, onOpen, active }: { d: Design; stages: Stage[]; onOpen: () => void; active: boolean }) {
  const s = d.detail?.sizing; const so = d.detail?.signoff; const v = d.detail?.verify;
  const stageLabel = stages.find((x) => x.id === d.stage)?.label ?? (d.stage === "done" ? "完成" : d.stage);
  return (
    <button onClick={onOpen}
      className={`card p-5 text-left transition hover:border-accent/60 ${active ? "border-accent" : ""}`}>
      <div className="flex items-center justify-between">
        <div className="font-mono font-semibold">{d.name}</div>
        <StatusBadge status={d.status} />
      </div>
      <div className="mt-3"><StagePipe stages={stages} current={d.stage} status={d.status} /></div>
      <div className="mt-2 text-xs text-muted">{d.status === "failed" ? <span className="text-bad">{d.detail?.reason}</span> : stageLabel}</div>
      <div className="mt-4 grid grid-cols-4 gap-2 text-xs">
        <Mini k="单元" v={s?.cells} /><Mini k="die" v={s ? `${s.die_side_um} µm` : null} />
        <Mini k="DRC" v={so?.drc} good={so?.drc === "clean"} />
        <Mini k="LVS" v={so?.lvs} good={so?.lvs === "clean"} />
      </div>
      {v && <div className="mt-3 text-xs text-ok">数据集校验 {v.passed}/{v.total} 项通过</div>}
    </button>
  );
}

function Mini({ k, v, good }: { k: string; v: any; good?: boolean }) {
  return (
    <div className="rounded-lg bg-panel2 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-faint">{k}</div>
      <div className={`truncate font-medium ${good === true ? "text-ok" : good === false && v ? "text-bad" : ""}`}>{v ?? "—"}</div>
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
      <div className="flex h-full w-[640px] flex-col border-l border-line bg-panel" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div><div className="font-mono text-lg font-semibold">{d.name}</div><div className="text-xs text-muted">{d.project}</div></div>
          <div className="flex items-center gap-2"><StatusBadge status={d.status} />
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
                <div className="grid grid-cols-3 gap-2">
                  <Stat label="综合后单元" value={s?.cells} /><Stat label="面积" value={s ? `${s.area_um2} µm²` : null} />
                  <Stat label="IO 引脚" value={s?.pins} /><Stat label="die 边长" value={s ? `${s.die_side_um} µm` : null}
                    sub={s ? { area: "由面积决定", pins: "由引脚数决定", pdn_floor: "由电源网格下限决定" }[s.binding as string] : ""} />
                  <Stat label="时钟周期" value={s ? `${s.period_ns} ns` : null} sub="按单元数分档" />
                  <Stat label="建立时间裕量" value={fmt(sum?.timing?.setup_wns)} sub="setup WNS (ns)" />
                </div>
              </section>
              <section>
                <div className="label mb-2">PPA（来自版图）</div>
                <div className="grid grid-cols-3 gap-2">
                  <Stat label="利用率" value={typeof geo.utilization === "number" ? `${(geo.utilization * 100).toFixed(1)} %` : null} />
                  <Stat label="单元面积" value={fmt(geo.stdcell_area_um2, 1) && `${fmt(geo.stdcell_area_um2, 1)} µm²`} />
                  <Stat label="功耗" value={typeof sum?.power?.total_power_w === "number" ? `${(sum.power.total_power_w * 1000).toFixed(3)} mW` : null} />
                </div>
              </section>
              <section>
                <div className="label mb-2">签核（由工具判定）</div>
                <div className="grid grid-cols-3 gap-2">
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
                  <div className="font-medium text-bad">失败于：{d.detail?.failed_stage}</div>
                  <div className="mt-1 text-muted">{d.detail?.reason}</div>
                  <div className="mt-2 text-xs text-muted">可以在「对话」里让 agent 分析原因并给出修复建议。</div>
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
                    className={`chip ${logStage === st ? "bg-accent/15 text-accent" : "bg-panel2 text-muted hover:text-fg"}`}>{st}</button>))}
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
      <div className="mb-4 grid grid-cols-3 gap-2 text-sm">
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">网格</div><div className="font-semibold">{info.shape?.join(" × ")}</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">GCell</div><div className="font-semibold">{info.gcell_step_um?.[0]?.toFixed(2)} µm</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">特征来源</div><div className="font-semibold">{({ place: "放置后", cts: "CTS 后", floorplan: "floorplan" } as any)[info.feature_stage] ?? info.feature_stage}</div></div>
      </div>
      {block("特征（输入）", "只取自放置后版图，无布线信息", "feature", info.features)}
      {block("标签（预测目标）", "取自布线后结果", "label", info.labels)}
      <p className="text-xs text-faint">颜色仅用于预览（深紫→黄，按 99 分位缩放）；数值在下载包的 features.npz / labels.npz 中，数组为 [x][y]。</p>
    </div>);
}
