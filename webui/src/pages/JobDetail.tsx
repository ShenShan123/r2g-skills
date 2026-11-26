import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Download, Square, X, Image as ImageIcon, FileText, LayoutGrid, Grid3x3, ChevronDown, ChevronRight,
         CheckCircle2, XCircle, AlertTriangle, Circle, MessagesSquare, Wand2, Loader2, Search, PackageOpen, RotateCcw, RefreshCw } from "lucide-react";
import { api, ApiError, askAgent, Design, fmtClock, Job, jobStatus, JobEvent, OUTPUT_LABEL, PLATFORM_LABEL } from "../lib/api";
import { StatusBadge, Stat, isActive } from "../components/ui";
import DeleteResource from "../components/DeleteResource";
import ConfirmDialog from "../components/ConfirmDialog";
import StageViews from "../components/StageViews";
import { getLang, t, tb } from "../lib/i18n";

const KIND_ICON: Record<string, any> = { ok: CheckCircle2, fail: XCircle, warn: AlertTriangle };
const KIND_CLS: Record<string, string> = { ok: "text-ok", fail: "text-bad", warn: "text-warn", start: "text-run" };
const PARAM_ZH: Record<string, string> = {
  period_ns: "时钟", period_tiers: "时钟分档", utilization: "利用率", area_allowance: "面积余量", pdn_floor_um: "core 下限",
  die_side_um: "die", io_pct: "IO 延迟%", setup_uncertainty_pct: "建立不确定度%", hold_uncertainty_ns: "保持不确定度",
  place_density_lb_addon: "密度加量",
};
const STAGE_ZH: Record<string, string> = {
  prepare: "准备", synth: "综合", size: "定尺寸", flow: "布局布线", signoff: "签核", repair: "自动修复",
  graph: "生成数据集", verify: "数据集校验", preview: "版图预览", ingest: "写入知识库", done: "完成",
};

export default function JobDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const [confirmStop, setConfirmStop] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState<{ busy: boolean; error: string }>({ busy: false, error: "" });
  const lastEv = useRef(0);
  const deleteRequested = useRef(false);

  useEffect(() => {
    let alive = true; lastEv.current = 0; setEvents([]); setCancelBusy(false); setCancelError("");
    setJob(null); setMissing(false); setLoadError(""); setSel(null); deleteRequested.current = false;
    let t: any;
    // the page keeps at most the last 500 events; a big batch polls more slowly
    const tick = async () => {
      try {
        const [j, ev] = await Promise.all([api.job(id), api.events(id, lastEv.current, lastEv.current ? 0 : 300)]);
        if (!alive) return;
        setJob(j); setLoadError("");
        if (ev.length) { lastEv.current = ev[ev.length - 1].id; setEvents((p) => [...p, ...ev].slice(-500)); }
        // a finished job changes only if someone deletes or re-packages it: poll slowly
        t = setTimeout(tick, !isActive(j.status) && !j.deletion ? 15000 : (j.designs?.length ?? 0) > BIG ? 5000 : 2000);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          // deleted: by this page's own trash button -> back to the list; otherwise say so
          if (deleteRequested.current) navigate("/jobs", { replace: true }); else setMissing(true);
          return;
        }
        setLoadError(e instanceof Error ? e.message : String(e));
        t = setTimeout(tick, 5000);
      }
    };
    tick();
    return () => { alive = false; clearTimeout(t); };
  }, [id]);

  async function stopJob() {
    setConfirmStop(false); setCancelBusy(true); setCancelError("");
    try {
      const response = await api.cancel(id);
      if (response.error) throw new Error(tb(response.error));
      setJob(await api.job(id));
    } catch (e) { setCancelError(e instanceof Error ? tb(e.message) : t("停止请求失败，请重试")); }
    finally { setCancelBusy(false); }
  }
  async function rerun(designs?: string[]) {
    setRetry({ busy: true, error: "" });
    try { const r = await api.retry(id, designs); navigate(`/jobs/${r.job_id}`); setRetry({ busy: false, error: "" }); }
    catch (e) { setRetry({ busy: false, error: e instanceof Error ? tb(e.message) : String(e) }); }
  }

  if (missing) return (
    <div className="mx-auto max-w-7xl px-4 py-10 md:px-8">
      <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft size={15} /> {t("全部任务")}</Link>
      <div className="card mt-4 p-8 text-center">
        <div className="text-lg font-semibold">{t("任务不存在或已被删除")}</div>
        <div className="mt-1 font-mono text-xs text-faint">{id}</div>
        <Link to="/jobs" className="btn-primary mt-5">{t("返回任务列表")}</Link>
      </div>
    </div>);
  if (!job) return <div className="p-10 text-muted">{loadError ? <span className="text-bad">{t("加载失败，正在重试：")}{tb(loadError)}</span> : t("加载中…")}</div>;
  const ds = job.designs ?? [];
  const passed = ds.filter((d) => d.status === "passed").length;
  const failed = ds.filter((d) => d.status === "failed").length;
  const cancelled = ds.filter((d) => d.status === "cancelled").length;
  const running = ds.filter((d) => d.status === "running").length;
  const pending = ds.filter((d) => d.status === "pending").length;
  const dual = ds.filter((d) => d.detail?.signoff?.dual_clean === true).length;
  const stopping = job.status === "cancelling";
  const active = isActive(job.status);
  const started = events[0]?.ts ?? job.created;
  const minutes = Math.max(0, Math.round(((active ? Date.now() / 1000 : job.updated) - started) / 60));
  const selected = ds.find((d) => d.name === sel) ?? null;
  const now = [...events].reverse().find((e) => e.kind === "start");
  const signoffOnly = job.spec?.output === "signoff";
  const rerunnable = failed + cancelled;
  const deleting = job.deletion === "pending";

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 md:px-8 md:py-8">
      <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft size={15} /> {t("全部任务")}</Link>
      {loadError && <div role="status" className="mt-2 rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">
        {t("暂时连不上 r2g 服务，显示的是最后一次读到的状态；正在重试（服务重启后任务会自动接着跑）。")}</div>}
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold md:text-2xl">{tb(job.title)}</h1>
          <div className="mt-1 text-xs text-faint">{t(OUTPUT_LABEL[job.spec?.output ?? "graph"] ?? job.spec?.output)} · {PLATFORM_LABEL[job.spec?.platform] ?? job.spec?.platform} ·{" "}
            {Object.keys(job.spec?.params ?? {}).length
              ? <span className="text-accent2">{t("自定义参数：")}{Object.entries(job.spec.params).map(([k, v]) => `${PARAM_ZH[k] ? t(PARAM_ZH[k]) : k} ${typeof v === "object" ? t("已改") : v}`).join(t("、"))}</span>
              : t("r2g 经验参数")} · {job.id}
            {job.spec?.retry_of && <> · <Link className="text-accent hover:underline" to={`/jobs/${job.spec.retry_of}`}>{t("重新运行自 {id}", { id: job.spec.retry_of })}</Link></>}</div>
        </div>
        <div className="flex items-center gap-2">
          {deleting ? <span className="chip bg-bad/10 text-bad">{t("删除中")}</span> : <StatusBadge status={jobStatus(job)} />}
          <DeleteResource kind="job" id={id} title={job.title} state={job.deletion}
            onRequested={() => { deleteRequested.current = true; }} onDeleted={() => navigate("/jobs", { replace: true })} />
        </div>
      </div>

      {/* what is going on, in one sentence, with the one action that matters now */}
      <div className={`card mt-5 flex flex-wrap items-center gap-4 p-4 md:p-5 ${!active && passed ? "border-ok/40" : ""}`}>
        <div className="min-w-0 flex-1">
          {active ? (
            <>
              <div className="flex items-center gap-2 font-semibold"><Loader2 size={17} className="animate-spin text-run" />
                {stopping ? t("正在停止所有未完成设计") : running ? t("正在处理") : t("排队等待计算资源")}{t(" · 已结束 {n}/{total}", { n: passed + failed + cancelled, total: ds.length })}</div>
              <div className="mt-1 text-sm text-muted">{t("{r} 运行 · {p} 排队 · {c} 已取消 · 已用 {m} 分钟", { r: running, p: pending, c: cancelled, m: minutes })}</div>
              {now && running > 0 && !stopping && <div className="mt-1 truncate text-sm text-muted">{now.design ?? ""} {tb(now.message)}</div>}
            </>) : (
            <>
              <div className="font-semibold">{job.status === "cancelled" ? t("任务已停止：{n} 个设计已取消", { n: cancelled }) : passed ? t("完成：{n}/{total} 个设计通过", { n: passed, total: ds.length }) : t("没有设计通过（{n}/{total} 失败）", { n: failed, total: ds.length })}</div>
              <div className="mt-1 text-sm text-muted">
                {job.status === "cancelled" ? t("已完成的结果和报告保留。") : passed ? (signoffOnly ? t("通过的设计都已签核（DRC、LVS 干净）。") : t("通过的设计都已签核（DRC、LVS 干净）并通过数据集校验，已打包。")) : t("点开失败的设计查看原因，或让 agent 帮你分析。")}
                {passed > 0 && failed > 0 && !signoffOnly && t(" {n} 个失败的设计不在数据集里。", { n: failed })}{t(" 用时 {m} 分钟。", { m: minutes })}
              </div>
            </>)}
        </div>
        {active && passed > 0 && !signoffOnly && <PartialDownload id={id} />}
        {active && !deleting && <button className="btn-ghost" disabled={cancelBusy || stopping}
          title={t("取消所有排队设计，并停止运行中的设计；已完成的结果保留")}
          onClick={() => setConfirmStop(true)}>
          {cancelBusy || stopping ? <Loader2 size={14} className="animate-spin" /> : <Square size={14} />}
          {cancelBusy ? t("提交停止请求…") : stopping ? t("正在停止…") : t("停止整个任务")}
        </button>}
        {!active && !deleting && rerunnable > 0 && (
          <button className="btn-ghost" disabled={retry.busy} onClick={() => rerun()}
            title={t("用相同的平台、数据集类型和参数，把失败和已取消的设计作为一个新任务重新运行；本任务保持不变")}>
            {retry.busy ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
            {t("重新运行 {n} 个未通过的设计", { n: rerunnable })}</button>)}
        {job.result?.package && <a className="btn-primary" href={`/api/jobs/${id}/download`} download><Download size={16} /> {t("下载数据集")}</a>}
      </div>
      {cancelError && <div role="alert" className="mt-2 text-sm text-bad">{t("停止失败：")}{cancelError}</div>}
      {retry.error && <div role="alert" className="mt-2 text-sm text-bad">{t("重新运行失败：")}{retry.error}</div>}
      {job.result?.package_error && <div role="alert" className="mt-2 text-sm text-bad">{t("打包数据集失败：")}{tb(job.result.package_error)}</div>}
      {confirmStop && (
        <ConfirmDialog title={t("停止整个任务？")} confirmLabel={t("停止任务")} onConfirm={stopJob} onCancel={() => setConfirmStop(false)}>
          <p className="break-words font-medium text-fg">{tb(job.title)}</p>
          <p>{t("排队中的 {p} 个设计会取消，运行中的 {r} 个设计会立即停止（它们的 EDA 进程会被结束）。已经完成的设计、报告和数据集保留。", { p: pending, r: running })}</p>
          <p>{t("停止后不能继续；需要时可以把未通过的设计重新运行。")}</p>
        </ConfirmDialog>)}
      <div className="mt-3 text-sm text-muted">{t("DRC + LVS 双清：")}<strong className="text-ok">{dual}</strong>{t(" · 数据集通过：{p} · 失败：{f}", { p: passed, f: failed })}
        <span className="ml-2 text-xs">{t("双清与数据集校验分别统计。")}</span></div>

      {ds.length > BIG ? <BatchList jobId={id} ds={ds} onOpen={setSel} /> : (
        <div className="mt-4 space-y-3">
          {ds.map((d) => <DesignCard key={d.name} jobId={id} d={d} onOpen={() => setSel(d.name)}
            onRetry={!active && !deleting && ["failed", "cancelled"].includes(d.status) ? () => rerun([d.name]) : undefined} retryBusy={retry.busy} />)}
        </div>)}

      <div className="mt-6">
        <button className="flex items-center gap-1.5 text-sm text-muted hover:text-fg" onClick={() => setShowLog(!showLog)}>
          {showLog ? <ChevronDown size={16} /> : <ChevronRight size={16} />} {t("详细过程（最近 {n} 条）", { n: events.length })}
        </button>
        {showLog && (
          <div className="card mt-2 max-h-[420px] space-y-0.5 overflow-y-auto p-2">
            {events.map((e) => {
              const I = KIND_ICON[e.kind] ?? Circle;
              return (
                <div key={e.id} className="flex gap-2.5 rounded-lg px-2 py-1.5 text-[13px]">
                  <span className="w-14 shrink-0 font-mono text-[11px] leading-5 text-faint">{fmtClock(e.ts)}</span>
                  <I size={14} className={`mt-0.5 shrink-0 ${KIND_CLS[e.kind] ?? "text-faint"}`} />
                  <div className="min-w-0 leading-5"><span className="font-mono text-xs text-accent">{e.design ?? t("任务|event")}</span>{" "}
                    <span className={e.kind === "fail" ? "text-bad" : ""}>{tb(e.message)}</span></div>
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
  const [error, setError] = useState("");
  return (
    <div>
      <button className="btn-ghost" disabled={busy} title={t("把目前已通过的设计先打包下载")}
        onClick={async () => {
          setBusy(true); setError("");
          try { const r = await api.packageNow(id); window.location.href = r.url; }
          catch (e) { setError(e instanceof Error ? tb(e.message) : String(e)); }
          finally { setBusy(false); }
        }}>
        {busy ? <Loader2 size={15} className="animate-spin" /> : <PackageOpen size={15} />} {busy ? t("正在打包…") : t("下载已完成的部分")}
      </button>
      {error && <p role="alert" className="mt-1 text-xs text-bad">{t("打包失败：")}{error}</p>}
    </div>);
}

/** Group key of a failure: the stage and the message without file names, line
 *  numbers or counts -- 2000 designs with the same yosys error are one group. */
function reasonKey(d: Design): string {
  const reason: string = d.detail?.reason ?? "";
  const msg = (getLang() === "en"
    ? tb(reason).replace(/^[^:]* failed: /i, "").replace(/^[^\s:]+\.(s?v|s?vh|v):[^:]*: /i, "")
    : reason.replace(/^[^：]*失败：/, "").replace(/^[^\s：]+\.(s?v|s?vh|v)[^：]*：/i, ""))
    .replace(/\d+/g, "N").slice(0, 60);
  const stage = d.detail?.failed_stage;
  return `${STAGE_ZH[stage] ? t(STAGE_ZH[stage]) : stage ?? "?"}${t("：")}${msg}`;
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
  const tabs: [string, string][] = [["all", t("全部")], ["running", t("运行中")], ["pending", t("排队中")], ["passed", t("数据集通过")], ["failed", t("失败")], ["cancelled", t("已取消")]];
  return (
    <div className="mt-4 space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
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
          <div className="text-sm font-medium">{t("失败原因（点一下只看这一类）")}</div>
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
          <input className="w-full bg-transparent py-1 text-sm outline-none" placeholder={t("按设计名搜索")} value={q}
            onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          <span className="shrink-0 text-xs text-faint">{t("{n} 个", { n: rows.length })}</span>
        </div>
        <div className="divide-y divide-line">
          {shown.map((d) => (
            <button key={d.name} onClick={() => onOpen(d.name)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-panel2/60">
              <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[d.status] ?? "bg-line"}`} />
              <span className="w-40 shrink-0 truncate font-mono text-sm sm:w-56">{d.name}</span>
              <span className={`min-w-0 flex-1 truncate text-xs ${d.status === "failed" ? "text-bad" : "text-muted"}`}>{plain(d)}</span>
              <StatusBadge status={d.status} />
            </button>))}
          {shown.length === 0 && <div className="px-3 py-6 text-center text-sm text-muted">{t("没有符合条件的设计")}</div>}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-center gap-3 border-t border-line px-3 py-2 text-sm">
            <button className="btn-ghost px-3 py-1" disabled={page === 0} onClick={() => setPage(page - 1)}>{t("上一页")}</button>
            <span className="text-muted">{page + 1} / {pages}</span>
            <button className="btn-ghost px-3 py-1" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>{t("下一页")}</button>
          </div>)}
      </div>
      {counts.failed > 0 && (
        <Link className="btn-ghost" to={askAgent(t("任务 {job} 有 {n} 个设计失败，帮我按原因归类，告诉我哪些是 RTL 问题、哪些可以修", { job: jobId, n: counts.failed }))}>
          <MessagesSquare size={15} /> {t("让 agent 归类分析失败的设计")}</Link>)}
    </div>
  );
}

function plain(d: Design): string {
  const v = d.detail?.verify;
  if (d.status === "pending") return t("排队中，等待空闲的算力");
  if (d.status === "passed") return v ? t("完成：签核通过，数据集校验 {n}/{total} 项全部通过", { n: v.passed, total: v.total }) : t("完成");
  if (d.status === "failed") return tb(d.detail?.reason) ?? t("失败");
  if (d.status === "cancelled") return t("已取消");
  return t("正在{stage}…", { stage: STAGE_ZH[d.stage] ? t(STAGE_ZH[d.stage]) : d.stage });
}

function DesignCard({ jobId, d, onOpen, onRetry, retryBusy }: { jobId: string; d: Design; onOpen: () => void;
  onRetry?: () => void; retryBusy?: boolean }) {
  const s = d.detail?.sizing; const so = d.detail?.signoff;
  const fixes: any[] = [...(d.detail?.frontend_fixes ?? []), ...(d.detail?.flow_fixes ?? [])];
  const repaired = Array.isArray(d.detail?.repair) && d.detail.repair.length > 0;
  return (
    <div className="card p-4 md:p-5">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1 truncate font-mono font-semibold">{d.name}</div>
        <StatusBadge status={d.status} />
        <button className="btn-ghost px-3 py-1.5" onClick={onOpen}>{t("详情")}</button>
      </div>
      <div className="mt-4"><StageViews jobId={jobId} d={d} /></div>
      <p className={`mt-3 text-sm ${d.status === "failed" ? "text-bad" : "text-muted"}`}>{plain(d)}</p>
      {(s || so) && (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
          {s && <span>{t("{cells} 个单元 · die {die} µm{b} · 时钟 {p} ns{ps}", { cells: s.cells, die: s.die_side_um, p: s.period_ns,
            b: s.binding === "fixed" ? t("（自定义）") : s.binding === "pins_ppl" ? t("（按引脚数修正）") : "",
            ps: s.period_source === "fixed" ? t("（自定义）") : "" })}</span>}
          {so && <span>DRC <b className={so.drc === "clean" ? "text-ok" : "text-bad"}>{so.drc === "clean" ? t("干净") : t("{n} 处违例", { n: so.drc_violations ?? "?" })}</b>
            {" · "}LVS <b className={so.lvs === "clean" ? "text-ok" : "text-bad"}>{so.lvs === "clean" ? t("一致") : so.lvs}</b></span>}
        </div>)}
      {so?.drc_scope && <p className="mt-2 text-xs text-muted">{tb(so.drc_scope)}</p>}
      {(fixes.length > 0 || repaired) && (
        <div className="mt-3 rounded-xl bg-accent2/10 px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-accent2"><Wand2 size={13} /> {t("自动修复")}</div>
          <ul className="mt-1 space-y-0.5 text-muted">
            {fixes.map((f, i) => <li key={i}>{tb(f.detail)}</li>)}
            {repaired && <li>{t("签核未通过后自动诊断并修复（{n} 步，见详情）", { n: d.detail.repair.length })}</li>}
          </ul>
        </div>)}
      {(d.status === "failed" || onRetry) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {d.status === "failed" && (
            <Link className="btn-ghost" to={askAgent(t("任务 {job} 里的设计 {name} 失败了，帮我分析原因并告诉我怎么改", { job: jobId, name: d.name }))}>
              <MessagesSquare size={15} /> {t("让 agent 分析原因")}</Link>)}
          {onRetry && (
            <button className="btn-ghost" disabled={retryBusy} onClick={onRetry} title={t("作为一个新任务重新运行这个设计")}>
              <RotateCcw size={15} /> {t("重新运行这个设计")}</button>)}
        </div>)}
    </div>
  );
}

const LOG_STAGES = ["synth", "flow", "signoff", "repair", "graph", "verify", "ingest"];

function DesignPanel({ jobId, d, output, onClose }: { jobId: string; d: Design; output: string; onClose: () => void }) {
  const [tab, setTab] = useState<"overview" | "images" | "layout" | "log">(output === "image" ? "images" : "overview");
  const [rep, setRep] = useState<any>(null);
  const [logStage, setLogStage] = useState("flow");
  const [log, setLog] = useState("");
  const [logBusy, setLogBusy] = useState(false);
  const [logTick, setLogTick] = useState(0);
  const running = d.status === "running";
  useEffect(() => { setRep(null); api.reports(jobId, d.name).then(setRep).catch(() => {}); }, [jobId, d.name, d.status]);
  useEffect(() => {
    if (tab !== "log") return;
    let alive = true;
    setLogBusy(true);
    api.log(jobId, d.name, logStage)
      .then((r) => { if (alive) setLog(r.log_tail ?? (r.error ? tb(r.error) : "")); })
      .catch((e) => { if (alive) setLog(t("日志读取失败：") + (e instanceof Error ? e.message : String(e))); })
      .finally(() => { if (alive) setLogBusy(false); });
    // a running stage keeps writing: follow it
    const timer = running ? setTimeout(() => setLogTick((n) => n + 1), 4000) : undefined;
    return () => { alive = false; clearTimeout(timer); };
  }, [tab, logStage, jobId, d.name, d.stage, running, logTick]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector("[role=alertdialog],[role=dialog][aria-modal=true]:not([data-panel])")) onClose(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  const s = d.detail?.sizing;
  const sum = useMemo(() => rep?.ppa?.summary ?? {}, [rep]);
  const geo = rep?.ppa?.geometry ?? {};
  const fmt = (x: any, n = 3) => (typeof x === "number" ? x.toFixed(n) : null);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40 backdrop-blur-[2px]" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={t("设计 {name} 的详情", { name: d.name })} data-panel
        className="flex h-full w-full flex-col border-l border-line bg-panel md:w-[640px]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-6 py-4">
          <div className="min-w-0"><div className="truncate font-mono text-lg font-semibold">{d.name}</div><div className="break-all text-xs text-muted">{d.project}</div></div>
          <div className="flex shrink-0 items-center gap-2 whitespace-nowrap"><StatusBadge status={d.status} />
            {d.status === "passed" && <a className="btn-ghost px-3 py-1.5" href={`/api/jobs/${encodeURIComponent(jobId)}/designs/${encodeURIComponent(d.name)}/download`} download><Download size={14} /> {t("下载")}</a>}
            <button className="rounded-lg p-1.5 text-muted hover:bg-panel2" aria-label={t("关闭")} title={t("关闭（Esc）")} onClick={onClose}><X size={18} /></button></div>
        </div>
        <div className="flex gap-1 border-b border-line px-4">
          {([["overview", t("概览"), LayoutGrid], ...(output === "image" ? [["images", t("图像"), Grid3x3]] : []),
             ["layout", t("版图"), ImageIcon], ["log", t("日志"), FileText]] as [string, string, any][]).map(([k, l, I]) => (
            <button key={k} onClick={() => setTab(k as any)}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm ${tab === k ? "border-accent text-accent" : "border-transparent text-muted hover:text-fg"}`}>
              <I size={15} /> {l}</button>))}
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {tab === "overview" && (
            <div className="space-y-5">
              <section>
                <div className="label mb-2">{t("尺寸与约束")}</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label={t("综合后单元")} value={s?.cells} /><Stat label={t("面积")} value={s ? `${s.area_um2} µm²` : null} />
                  <Stat label={t("IO 引脚")} value={s?.pins} /><Stat label={t("die 边长")} value={s ? `${s.die_side_um} µm` : null}
                    sub={s ? tb(({ area: "由面积决定", pins: "由引脚数决定", pdn_floor: "由电源网格下限决定" } as Record<string, string>)[s.binding as string]) : ""} />
                  <Stat label={t("时钟周期")} value={s ? `${s.period_ns} ns` : null} sub={t("按单元数分档")} />
                  <Stat label={t("建立时间裕量")} value={fmt(sum?.timing?.setup_wns)} sub={t("建立时间裕量（纳秒）")} />
                </div>
              </section>
              <section>
                <div className="label mb-2">{t("PPA（来自版图）")}</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label={t("利用率")} value={typeof geo.utilization === "number" ? `${(geo.utilization * 100).toFixed(1)} %` : null} />
                  <Stat label={t("单元面积")} value={fmt(geo.stdcell_area_um2, 1) && `${fmt(geo.stdcell_area_um2, 1)} µm²`} />
                  <Stat label={t("功耗")} value={typeof sum?.power?.total_power_w === "number" ? `${(sum.power.total_power_w * 1000).toFixed(3)} mW` : null} />
                </div>
              </section>
              <section>
                <div className="label mb-2">{t("签核（由工具判定）")}</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Stat label="DRC" value={<Verdict v={rep?.drc?.status} />} sub={rep?.drc ? t("{n} 处违例", { n: rep.drc.total_violations ?? 0 }) : ""} />
                  <Stat label="LVS" value={<Verdict v={rep?.lvs?.status} />} sub={rep?.lvs?.mismatch_count != null ? t("{n} 处不匹配", { n: rep.lvs.mismatch_count }) : ""} />
                  <Stat label={t("数据集校验")} value={rep?.verify ? `${rep.verify.passed}/${rep.verify.total}` : "—"}
                    sub={rep?.graph_dataset?.status === "ok" ? t("数据清单校验通过") : rep?.graph_dataset?.status ?? ""} />
                </div>
              </section>
              {Array.isArray(d.detail?.repair) && d.detail.repair.length > 0 && (
                <section>
                  <div className="label mb-2">{t("自动修复记录")}</div>
                  <div className="space-y-2">
                    {d.detail.repair.map((r: any, i: number) => {
                      const verdict = String(r.verdict ?? r.result ?? "");
                      const known: Record<string, [string, string]> = {
                        applied: ["已减少", "bg-warn/15 text-warn"], cleared: ["已清零", "bg-ok/15 text-ok"],
                        stop_clean: ["无需修复", "bg-ok/15 text-ok"], failed: ["未成功", "bg-bad/15 text-bad"],
                      };
                      const [vText, vClass] = known[verdict] ?? [verdict, "bg-panel text-muted"];
                      const strategy = r.strategy ?? r.strategy_id ?? r.id;
                      const clean = String(r.after) === "0";
                      return <div key={i} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-xl bg-panel2 px-3 py-2 text-sm">
                        {r.check && <span className="rounded-md bg-panel px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted ring-1 ring-line">{r.check}</span>}
                        {r.iter != null && <span className="text-xs text-faint">{t("第 {n} 轮", { n: r.iter })}</span>}
                        {strategy && strategy !== "none" && <span title={strategy} className="min-w-0 truncate font-mono text-[13px] text-accent2">{strategy === "antenna_diode_repair" ? t("天线二极管修复") : strategy}</span>}
                        {r.before != null && <span className="ml-auto font-mono text-[13px] tabular-nums text-muted">{r.before}<span className="mx-1 text-faint">→</span><span className={clean ? "font-semibold text-ok" : "text-fg"}>{r.after}</span></span>}
                        {verdict && <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${vClass} ${r.before == null ? "ml-auto" : ""}`}>{t(vText)}</span>}
                      </div>;
                    })}
                  </div>
                </section>)}
              {d.status === "failed" && (
                <div className="rounded-xl border border-bad/40 bg-bad/10 p-4 text-sm">
                  <div className="font-medium text-bad">{t("失败于：")}{STAGE_ZH[d.detail?.failed_stage] ? t(STAGE_ZH[d.detail?.failed_stage]) : d.detail?.failed_stage}</div>
                  <div className="mt-1 text-muted">{tb(d.detail?.reason)}</div>
                  <Link className="btn-ghost mt-3" to={askAgent(t("任务 {job} 里的设计 {name} 失败了，帮我分析原因并告诉我怎么改", { job: jobId, name: d.name }))}>
                    <MessagesSquare size={15} /> {t("让 agent 分析原因")}</Link>
                </div>)}
            </div>)}
          {tab === "images" && <ImagesTab jobId={jobId} d={d} />}
          {tab === "layout" && <StageViews jobId={jobId} d={d} defaultExpanded />}
          {tab === "log" && (
            <div>
              <div className="mb-3 flex flex-wrap items-center gap-1.5">
                {LOG_STAGES.map((st) => (
                  <button key={st} aria-pressed={logStage === st} onClick={() => setLogStage(st)}
                    className={`chip ${logStage === st ? "bg-accent/15 text-accent" : "bg-panel2 text-muted hover:text-fg"}`}>{STAGE_ZH[st] ? t(STAGE_ZH[st]) : st}</button>))}
                <button type="button" className="ml-auto flex items-center gap-1 text-xs text-muted hover:text-fg" onClick={() => setLogTick((n) => n + 1)}
                  title={running ? t("运行中每 4 秒自动刷新") : t("刷新")}>
                  <RefreshCw size={13} className={logBusy ? "animate-spin" : ""} />{running ? t("自动刷新中") : t("刷新")}</button>
              </div>
              <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-xl bg-bg p-4 font-mono text-[11.5px] leading-5 text-muted">{log || t("（暂无日志）")}</pre>
            </div>)}
        </div>
      </div>
    </div>
  );
}

function Verdict({ v }: { v?: string }) {
  if (!v) return <span className="text-faint">—</span>;
  return <span className={v === "clean" ? "text-ok" : "text-bad"}>{v === "clean" ? t("干净") : v}</span>;
}

const CH_LABEL: Record<string, string> = {
  cell_density: "单元密度", macro_region: "宏单元区域", rudy: "RUDY（布线需求估计）", pin_rudy: "引脚 RUDY",
  pin_density: "引脚密度", congestion: "拥塞（平滑）", congestion_raw: "拥塞（原始）", ir_drop: "IR drop",
};

function ImagesTab({ jobId, d }: { jobId: string; d: Design }) {
  const [info, setInfo] = useState<any>(null);
  useEffect(() => { api.images(jobId, d.name).then(setInfo).catch(() => setInfo({ available: false })); }, [jobId, d.name, d.status]);
  if (!info) return <div className="text-sm text-muted">{t("加载中…")}</div>;
  if (!info.available) return <div className="rounded-xl bg-panel2 p-10 text-center text-sm text-muted">{t("图像数据集在设计完成后生成")}</div>;
  const block = (title: string, sub: string, kind: "feature" | "label", chans: Record<string, any>) => (
    <section className="mb-6">
      <div className="mb-2 flex items-baseline justify-between"><div className="label">{title}</div><div className="text-xs text-faint">{sub}</div></div>
      <div className="grid grid-cols-3 gap-3">
        {Object.entries(chans).map(([k, v]: any) => (
          <figure key={k} className="overflow-hidden rounded-xl border border-line bg-panel2">
            <img src={`/api/jobs/${jobId}/designs/${d.name}/images/${kind}_${k}.png`} alt={k}
              className="aspect-square w-full" style={{ imageRendering: "pixelated" }} />
            <figcaption className="px-2.5 py-2 text-xs">
              <div className="font-medium">{CH_LABEL[k] ? t(CH_LABEL[k]) : k}</div>
              <div className="text-faint">{t("最大 {v} {unit}", { v: Number(v.max).toPrecision(3), unit: v.unit })}</div>
            </figcaption>
          </figure>))}
      </div>
    </section>);
  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 text-sm">
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">{t("网格")}</div><div className="font-semibold">{info.shape?.join(" × ")}</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">GCell</div><div className="font-semibold">{info.gcell_step_um?.[0]?.toFixed(2)} µm</div></div>
        <div className="rounded-xl bg-panel2 px-3 py-2"><div className="label">{t("特征来源")}</div><div className="font-semibold">{tb(({ place: "放置后", cts: "CTS 后", floorplan: "floorplan" } as any)[info.feature_stage] ?? info.feature_stage)}</div></div>
      </div>
      {block(t("特征（输入）"), t("只取自放置后版图，无布线信息"), "feature", info.features)}
      {block(t("标签（预测目标）"), t("取自布线后结果"), "label", info.labels)}
      <p className="text-xs text-faint">{t("颜色仅用于预览（深紫→黄，按 99 分位缩放）；数值在下载包的 features.npz / labels.npz 中，数组为 [x][y]。")}</p>
    </div>);
}
