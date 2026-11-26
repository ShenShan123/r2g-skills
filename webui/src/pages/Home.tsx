import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { FolderUp, Play, Loader2, X, FileCode2, MessagesSquare, ChevronRight, Check, FolderTree, Search, AlertTriangle } from "lucide-react";
import { api, fmtBytes, fmtTime, Inspect, Job, jobStatus, Output, Scan, Platform, PLATFORM_LABEL, Upload } from "../lib/api";
import OutputPicker from "../components/OutputPicker";
import ParamsPicker, { cleanParams, Params } from "../components/ParamsPicker";
import { StatusBadge } from "../components/ui";
import { t, tb } from "../lib/i18n";

const HDL = /\.(s?v|s?vh)$/i;
// HDL plus the data files $readmem reads; nothing else in the folder is sent
const SEND = /\.(s?v|s?vh|hex|mem|bin|txt|dat|coe|mif|init|rom|data)$/i;

type Picked = { upload: Upload; folder: string; info: Inspect | null; error: string };

export default function Home() {
  const nav = useNavigate();
  const [examples, setExamples] = useState<{ name: string; top: string }[]>([]);
  const [platforms, setPlatforms] = useState<{ id: string; label: string }[]>(
    Object.entries(PLATFORM_LABEL).map(([id, label]) => ({ id, label })));
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [rtl, setRtl] = useState<Picked | null>(null);
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [pickErr, setPickErr] = useState("");
  const [top, setTop] = useState("");
  const [platform, setPlatform] = useState<Platform>("nangate45");
  const [output, setOutput] = useState<Output>("graph");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [params, setParams] = useState<Params | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const dirInput = useRef<HTMLInputElement>(null);
  // uploads made on this page and not (yet) submitted: removed again when replaced or left behind
  const unsubmitted = useRef(new Set<string>());
  const track = (id: string | null, keep = true) => { if (id) keep ? unsubmitted.current.add(id) : unsubmitted.current.delete(id); };
  const discard = (id?: string) => { if (id && unsubmitted.current.delete(id)) api.deleteUpload(id).catch(() => {}); };

  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => {
    api.examples().then((r) => setExamples(r.examples)).catch(() => {});
    api.platforms().then((r) => r.platforms.length && setPlatforms(r.platforms)).catch(() => {});
    api.jobs(4).then(setJobs).catch(() => {});
    const pending = unsubmitted.current;
    // leaving the page (in-app, or closing/reloading the tab) discards what was uploaded but not submitted
    const drop = () => { pending.forEach((id) => api.deleteUpload(id, true).catch(() => {})); pending.clear(); };
    window.addEventListener("pagehide", drop);
    return () => { window.removeEventListener("pagehide", drop); drop(); };
  }, []);

  const toggle = (n: string) => { const s = new Set(picked); s.has(n) ? s.delete(n) : s.add(n); setPicked(s); };
  const rtlReady = !!rtl?.info && !rtl.error && (!rtl.info.problem || !!top);
  const count = mode === "batch" ? (scan?.ok ?? 0) : picked.size + (rtlReady ? 1 : 0);
  const blocked = mode === "single" && (!!progress || (!!rtl && !rtlReady));

  async function pickFolder(list: File[]) {
    setPickErr(""); setTop("");
    const keep = list.filter((f) => SEND.test(f.name));
    if (!keep.some((f) => HDL.test(f.name))) { setPickErr(t("这个文件夹里没有 .v / .sv 文件")); return; }
    discard(rtl?.upload.id); setRtl(null);
    const folder = (((keep[0] as any).webkitRelativePath || "") as string).split("/")[0];
    setProgress({ sent: 0, total: keep.reduce((n, f) => n + f.size, 0) });
    try {
      const up = await api.upload(keep, (sent, total) => setProgress({ sent, total }));
      track(up.id);
      let info: Inspect | null = null, error = "";
      try { info = await api.inspect(folder ? `${up.path}/${folder}` : up.path); }
      catch (e: any) { error = tb(e.message); }
      setRtl({ upload: up, folder, info, error });
      if (info && !info.problem && info.top) setTop("");
    } catch (e: any) { setPickErr(tb(e.message)); } finally { setProgress(null); }
  }
  function clearFolder() { discard(rtl?.upload.id); setRtl(null); setTop(""); setPickErr(""); }

  async function start() {
    setBusy(true); setErr("");
    try {
      if (mode === "batch" && scan) {
        const designs = scan.designs.filter((d) => !d.problem).map((d) => ({ path: d.path, top: d.top, name: d.name }));
        const name = scan.root.split("/").pop();
        const r = await api.createJob(designs, `批量 · ${name} · ${designs.length} 个设计`, output, cleanParams(params), platform);
        unsubmitted.current.clear(); nav(`/jobs/${r.job_id}`); return;
      }
      const designs: any[] = [...picked].map((example) => ({ example }));
      if (rtlReady && rtl?.info) designs.push({ path: rtl.info.path, top: top || rtl.info.top || undefined, name: rtl.info.name });
      const r = await api.createJob(designs, "", output, cleanParams(params), platform);
      unsubmitted.current.clear(); nav(`/jobs/${r.job_id}`);
    } catch (e: any) { setErr(tb(e.message)); } finally { setBusy(false); }
  }

  const info = rtl?.info;
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <h1 className="text-2xl font-bold tracking-tight md:text-3xl">{t("把 RTL 变成签核过的数据集")}</h1>
      <p className="mt-2 text-[15px] text-muted">
        {t("综合、布局布线、DRC/LVS 签核、失败自动修复、生成数据集并逐项校验，全部自动完成。")}
      </p>

      <div className="card mt-6 divide-y divide-line">
        {/* 1 */}
        <Step n={1} title={t("选择设计")} done>
          <div className="mb-3 inline-flex rounded-xl bg-panel2 p-1 text-sm">
            {([["single", "一个设计"], ["batch", "批量（很多个设计）"]] as const).map(([k, l]) => (
              <button key={k} type="button" aria-pressed={mode === k} onClick={() => setMode(k)}
                className={`rounded-lg px-3 py-1.5 ${mode === k ? "bg-panel font-medium text-fg shadow-card" : "text-muted"}`}>{t(l)}</button>))}
          </div>
          {mode === "batch" ? <BatchPick scan={scan} setScan={setScan} track={track} discard={discard} /> : (<>
          <label className={`flex items-center gap-3 rounded-xl border border-dashed px-4 py-4 transition
              ${progress ? "cursor-wait border-accent" : rtl ? "cursor-pointer border-accent bg-accent/5" : "cursor-pointer border-line hover:border-accent hover:bg-panel2/60"}`}>
            {progress ? <Loader2 size={22} className="animate-spin text-accent" /> : <FolderUp size={22} className={rtl ? "text-accent" : "text-muted"} />}
            <div className="min-w-0 flex-1">
              {progress ? (
                <>
                  <div className="font-medium">{t("上传中… {sent} / {total}", { sent: fmtBytes(progress.sent), total: fmtBytes(progress.total) })}</div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-panel2"><div className="h-full rounded-full bg-accent transition-all"
                    style={{ width: `${progress.total ? Math.round(100 * progress.sent / progress.total) : 0}%` }} /></div>
                </>) : rtl ? (
                <>
                  <div className="font-medium">{rtl.folder || t("已选择文件夹")}</div>
                  <div className="truncate text-xs text-muted">{t("已上传 {n} 个文件（{size}）· 点击可重新选择", { n: rtl.upload.files.length, size: fmtBytes(rtl.upload.bytes) })}</div>
                </>) : (
                <>
                  <div className="font-medium">{t("上传 RTL 文件夹")}</div>
                  <div className="text-xs text-muted">{t("包含 .v / .sv 的文件夹；只会上传源码和 $readmem 数据文件")}</div>
                </>)}
            </div>
            {rtl && !progress && (
              <button type="button" className="rounded-lg p-1.5 text-muted hover:bg-panel2" aria-label={t("清除")}
                onClick={(e) => { e.preventDefault(); clearFolder(); }}><X size={16} /></button>)}
            <input ref={dirInput} type="file" multiple className="hidden" disabled={!!progress} aria-label={t("上传 RTL 文件夹")}
              onChange={(e) => { pickFolder(Array.from(e.target.files || [])); e.target.value = ""; }} />
          </label>
          {pickErr && <p role="alert" className="mt-2 text-sm text-bad">{pickErr}</p>}
          {rtl && (
            <div className="mt-3 space-y-2 text-sm">
              {rtl.error && <p role="alert" className="text-bad">{rtl.error}</p>}
              {info && (<>
                <div className="flex flex-wrap gap-1.5">
                  {info.file_names.slice(0, 6).map((f) => <span key={f} className="chip bg-panel2 text-muted"><FileCode2 size={12} />{f.split("/").pop()}</span>)}
                  {info.files > 6 && <span className="chip bg-panel2 text-muted">+{info.files - 6}</span>}
                </div>
                {!info.problem ? (
                  <div className="flex flex-wrap items-center gap-2 text-muted">
                    <Check size={15} className="text-ok" />{t("顶层模块：")}
                    {info.candidates.length > 1 ? (
                      <select className="input w-auto py-1 font-mono" value={top || info.top || ""} aria-label={t("顶层模块")}
                        onChange={(e) => setTop(e.target.value === info.top ? "" : e.target.value)}>
                        {[...new Set([info.top as string, ...info.candidates])].map((m) => <option key={m} value={m}>{m}</option>)}
                      </select>) : <b className="font-mono text-fg">{info.top}</b>}
                    <span className="text-xs text-faint">{tb(info.top_source)}</span>
                  </div>
                ) : info.candidates.length > 0 ? (
                  <div className="rounded-xl border border-warn/40 bg-warn/10 px-3 py-2">
                    <div className="flex items-center gap-1.5 font-medium text-warn"><AlertTriangle size={14} />{t("有多个候选顶层模块，请选一个")}</div>
                    <select className="input mt-2 w-auto py-1 font-mono" value={top} aria-label={t("顶层模块")} onChange={(e) => setTop(e.target.value)}>
                      <option value="">{t("选择顶层模块…")}</option>
                      {info.candidates.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                ) : <p role="alert" className="text-bad">{tb(info.problem)}</p>}
              </>)}
              {rtl.upload.rejected.length > 0 && (
                <details className="text-xs text-warn">
                  <summary className="cursor-pointer">{t("{n} 个文件未上传（点开看原因）", { n: rtl.upload.rejected.length })}</summary>
                  <ul className="mt-1 space-y-0.5 text-muted">{rtl.upload.rejected.slice(0, 50).map((r) => <li key={r.file}><span className="font-mono">{r.file}</span>{t("：")}{tb(r.reason)}</li>)}</ul>
                </details>)}
            </div>)}
          <div className="mt-4 text-sm text-muted">{t("没有现成的 RTL？先用示例试试：")}</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {examples.map((e) => (
              <button key={e.name} type="button" aria-pressed={picked.has(e.name)} onClick={() => toggle(e.name)}
                className={`chip border px-3 py-1.5 text-sm ${picked.has(e.name) ? "border-accent bg-accent/10 text-accent" : "border-line text-fg hover:bg-panel2"}`}>
                {picked.has(e.name) && <Check size={13} />}<span className="font-mono">{e.name}</span></button>))}
          </div>
          </>)}
        </Step>

        {/* 2 */}
        <Step n={2} title={t("选择工艺平台")} done>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3" role="group" aria-label={t("工艺平台")}>
            {platforms.map(({ id, label }) => (
              <button key={id} type="button" disabled={busy} aria-pressed={platform === id}
                onClick={() => { if (platform !== id) { setPlatform(id as Platform); setParams(null); } }}
                className={`rounded-xl border px-4 py-3 text-left transition disabled:opacity-50 ${platform === id ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
                <div className="flex items-center gap-2 font-medium">
                  {label.split(" · ")[0]}
                  <span aria-hidden="true" className={`ml-auto h-4 w-4 shrink-0 rounded-full border-2 ${platform === id ? "border-accent bg-accent shadow-[inset_0_0_0_2px_rgb(var(--panel))]" : "border-line"}`} />
                </div>
                <div className="mt-1 text-xs leading-5 text-muted">{label.split(" · ")[1]}</div>
              </button>
            ))}
          </div>
        </Step>

        {/* 3 */}
        <Step n={3} title={t("选择数据集类型")} done>
          <OutputPicker value={output} onChange={setOutput} />
        </Step>

        {/* 4 */}
        <Step n={4} title={t("流程参数")} done>
          <ParamsPicker key={platform} platform={platform} value={params} onChange={setParams} />
        </Step>

        {/* 5 */}
        <Step n={5} title={t("开始")} done={false}>
          <button className="btn-primary w-full justify-center py-3 text-base" onClick={start} disabled={busy || count === 0 || blocked}>
            {busy ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
            {busy ? t("提交中…") : progress ? t("等待上传完成…") : blocked ? t("先在第 1 步选好顶层模块")
              : count ? t("开始处理 {n} 个设计", { n: count }) + (cleanParams(params) ? t("（自定义参数）") : "") : t("先在第 1 步选择设计")}
          </button>
          <p className="mt-2 text-center text-xs text-muted">
            {mode === "batch" ? t("设计会按「设置」里的并发上限排队运行；服务重启后会自动接着跑。") : t("小设计约 3–5 分钟；提交后会打开进度页，可以随时离开再回来看。")}</p>
          {err && <p role="alert" className="mt-2 text-center text-sm text-bad">{err}</p>}
        </Step>
      </div>

      <Link to="/chat" className="card mt-4 flex items-center gap-3 px-5 py-4 hover:border-accent/60">
        <MessagesSquare size={20} className="text-accent" />
        <div className="flex-1">
          <div className="font-medium">{t("不确定怎么做？问 agent")}</div>
          <div className="text-xs text-muted">{t("让它帮你在 GitHub 上找设计、提交任务、解释失败原因")}</div>
        </div>
        <ChevronRight size={18} className="text-faint" />
      </Link>

      {jobs.length > 0 && (
        <section className="mt-8">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-sm font-medium text-muted">{t("最近的任务")}</div>
            <Link to="/jobs" className="text-sm text-accent hover:underline">{t("全部")}</Link>
          </div>
          <div className="card divide-y divide-line">
            {jobs.map((j) => (
              <Link key={j.id} to={`/jobs/${j.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-panel2/60">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{tb(j.title)}</div>
                  <div className="text-xs text-muted">{fmtTime(j.created)} · {PLATFORM_LABEL[j.spec?.platform] ?? j.spec?.platform} · {t("{n}/{total} 通过", { n: j.counts?.passed ?? 0, total: Object.values(j.counts ?? {}).reduce((a, b) => a + b, 0) })}</div>
                </div>
                <StatusBadge status={jobStatus(j)} />
              </Link>))}
          </div>
        </section>)}
    </div>
  );
}

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: any }) {
  return (
    <section className="p-5 md:p-6">
      <div className="mb-3 flex items-center gap-2.5">
        <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold
          ${done ? "bg-accent text-onaccent" : "bg-panel2 text-muted border border-line"}`}>{n}</span>
        <h2 className="font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function BatchPick({ scan, setScan, track, discard }: { scan: Scan | null; setScan: (s: Scan | null) => void;
  track: (id: string | null, keep?: boolean) => void; discard: (id?: string) => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "scan">("");
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [err, setErr] = useState("");
  const [showSkip, setShowSkip] = useState(false);
  const [uploadId, setUploadId] = useState<string | undefined>();
  const [rejected, setRejected] = useState<{ file: string; reason: string }[]>([]);
  const dir = useRef<HTMLInputElement>(null);
  useEffect(() => { dir.current?.setAttribute("webkitdirectory", ""); }, []);

  async function doScan(p: string) {
    setBusy("scan"); setErr(""); setScan(null);
    try {
      const s = await api.scan(p);
      if (!s.total) setErr(t("这个目录下没有找到设计：需要一个文件夹，里面每个子文件夹装着一个设计的 .v / .sv 文件"));
      setScan(s);
    } catch (e: any) { setErr(tb(e.message)); } finally { setBusy(""); }
  }
  async function upload(list: File[]) {
    const keep = list.filter((f) => SEND.test(f.name));
    if (!keep.some((f) => HDL.test(f.name))) { setErr(t("这个文件夹里没有 Verilog 文件")); return; }
    discard(uploadId); setUploadId(undefined); setRejected([]);
    setBusy("upload"); setErr(""); setScan(null);
    setProgress({ sent: 0, total: keep.reduce((n, f) => n + f.size, 0) });
    try {
      const up = await api.upload(keep, (sent, total) => setProgress({ sent, total }));
      track(up.id); setUploadId(up.id); setRejected(up.rejected);
      const folder = (((keep[0] as any).webkitRelativePath || "") as string).split("/")[0];
      setProgress(null);
      await doScan(folder ? `${up.path}/${folder}` : up.path);
    } catch (e: any) { setErr(tb(e.message)); setBusy(""); setProgress(null); }
  }
  const ok = scan?.designs.filter((d) => !d.problem) ?? [];
  const bad = scan?.designs.filter((d) => d.problem) ?? [];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">{t("准备一个文件夹，里面")}<b className="text-fg">{t("每个子文件夹是一个设计")}</b>{t("。可以上传，也可以直接填服务器上的路径（文件已经在服务器上时更快）。")}</p>
      <label className={`flex items-center gap-3 rounded-xl border border-dashed border-line px-4 py-3 ${busy ? "cursor-wait" : "cursor-pointer hover:border-accent hover:bg-panel2/60"}`}>
        {busy === "upload" ? <Loader2 size={20} className="animate-spin text-accent" /> : <FolderTree size={20} className="text-muted" />}
        <div className="min-w-0 flex-1"><div className="font-medium">{progress ? t("上传中… {sent} / {total}", { sent: fmtBytes(progress.sent), total: fmtBytes(progress.total) }) : t("上传装着多个设计的文件夹")}</div>
          {progress ? <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-panel2"><div className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${progress.total ? Math.round(100 * progress.sent / progress.total) : 0}%` }} /></div>
            : <div className="text-xs text-muted">{t("只会上传源码和 $readmem 数据文件")}</div>}</div>
        <input ref={dir} type="file" multiple className="hidden" disabled={!!busy} aria-label={t("上传装着多个设计的文件夹")}
          onChange={(e) => { upload(Array.from(e.target.files || [])); e.target.value = ""; }} />
      </label>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (path.trim()) { discard(uploadId); setUploadId(undefined); doScan(path.trim()); } }}>
        <input className="input font-mono" placeholder={t("或填服务器上的目录，例如 /data/rtl_corpus")} value={path} onChange={(e) => setPath(e.target.value)}
          aria-label={t("服务器上的目录")} />
        <button className="btn-ghost shrink-0" disabled={!path.trim() || !!busy}>
          {busy === "scan" ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />} {t("扫描")}</button>
      </form>
      {err && <p role="alert" className="text-sm text-bad">{err}</p>}
      {rejected.length > 0 && (
        <details className="text-xs text-warn">
          <summary className="cursor-pointer">{t("{n} 个文件未上传（点开看原因）", { n: rejected.length })}</summary>
          <ul className="mt-1 space-y-0.5 text-muted">{rejected.slice(0, 50).map((r) => <li key={r.file}><span className="font-mono">{r.file}</span>{t("：")}{tb(r.reason)}</li>)}</ul>
        </details>)}
      {scan && scan.total > 0 && (
        <div className="rounded-xl bg-panel2 p-4 text-sm">
          <div className="font-medium">{t("识别到 {n} 个设计：", { n: scan.total })}<span className="text-ok">{t("{n} 个将运行", { n: scan.ok })}</span>
            {scan.skipped > 0 && <span className="text-warn">{t("，{n} 个将跳过", { n: scan.skipped })}</span>}</div>
          <div className="mt-1 truncate font-mono text-xs text-faint">{scan.root}</div>
          {ok.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {ok.slice(0, 10).map((d) => <span key={d.name} className="chip bg-panel text-muted"><span className="font-mono">{d.name}</span>{d.top !== d.name && <span className="text-faint">{t("· 顶层 {top}", { top: d.top })}</span>}</span>)}
              {ok.length > 10 && <span className="chip bg-panel text-muted">+{ok.length - 10}</span>}
            </div>)}
          {bad.length > 0 && (
            <div className="mt-3">
              <button type="button" className="flex items-center gap-1.5 text-xs text-warn" onClick={() => setShowSkip(!showSkip)}>
                <AlertTriangle size={13} /> {showSkip ? t("收起跳过的设计和原因") : t("查看跳过的设计和原因")}</button>
              {showSkip && (
                <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs">
                  {bad.map((d) => <li key={d.name}><span className="font-mono">{d.name}</span>{t("：")}<span className="text-muted">{tb(d.problem)}</span></li>)}
                </ul>)}
            </div>)}
        </div>)}
    </div>
  );
}
