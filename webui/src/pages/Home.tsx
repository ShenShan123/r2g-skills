import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { FolderUp, Play, Loader2, X, FileCode2, MessagesSquare, ChevronRight, Check, FolderTree, Search, AlertTriangle } from "lucide-react";
import { api, fmtTime, Job, Output, Scan } from "../lib/api";
import OutputPicker from "../components/OutputPicker";
import ParamsPicker, { cleanParams, Params } from "../components/ParamsPicker";
import { StatusBadge } from "../components/ui";

const HDL = /\.(s?v|s?vh)$/i;
// HDL plus the data files $readmem reads; nothing else in the folder is sent
const SEND = /\.(s?v|s?vh|hex|mem|bin|txt|dat|coe|mif|init|rom|data)$/i;

export default function Home() {
  const nav = useNavigate();
  const [examples, setExamples] = useState<{ name: string; top: string }[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<File[]>([]);
  const [top, setTop] = useState("");
  const [output, setOutput] = useState<Output>("graph");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [mode, setMode] = useState<"single" | "batch">("single");
  const [params, setParams] = useState<Params | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const dirInput = useRef<HTMLInputElement>(null);

  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => {
    api.examples().then((r) => setExamples(r.examples)).catch(() => {});
    api.jobs().then((j) => setJobs(j.slice(0, 4))).catch(() => {});
  }, []);

  const hdl = files.filter((f) => HDL.test(f.name));
  const folder = files[0] ? (((files[0] as any).webkitRelativePath || "") as string).split("/")[0] : "";
  const toggle = (n: string) => { const s = new Set(picked); s.has(n) ? s.delete(n) : s.add(n); setPicked(s); };
  const count = mode === "batch" ? (scan?.ok ?? 0) : picked.size + (hdl.length ? 1 : 0);

  async function start() {
    setBusy(true); setErr("");
    try {
      if (mode === "batch" && scan) {
        const designs = scan.designs.filter((d) => !d.problem).map((d) => ({ path: d.path, top: d.top, name: d.name }));
        const name = scan.root.split("/").pop();
        const r = await api.createJob(designs, `批量 · ${name} · ${designs.length} 个设计`, output, cleanParams(params));
        nav(`/jobs/${r.job_id}`); return;
      }
      const designs: any[] = [...picked].map((example) => ({ example }));
      if (hdl.length) {
        const up = await api.upload(files.filter((f) => SEND.test(f.name)));
        designs.push({ path: up.path, top: top || undefined, name: top || folder || undefined });
      }
      const r = await api.createJob(designs, "", output, cleanParams(params));
      nav(`/jobs/${r.job_id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <h1 className="text-2xl font-bold tracking-tight md:text-3xl">把 RTL 变成签核过的数据集</h1>
      <p className="mt-2 text-[15px] text-muted">
        综合、布局布线、DRC/LVS 签核、失败自动修复、生成数据集并逐项校验，全部自动完成。
      </p>

      <div className="card mt-6 divide-y divide-line">
        {/* 1 */}
        <Step n={1} title="选择设计" done={count > 0}>
          <div className="mb-3 inline-flex rounded-xl bg-panel2 p-1 text-sm">
            {([["single", "一个设计"], ["batch", "批量（很多个设计）"]] as const).map(([k, l]) => (
              <button key={k} type="button" onClick={() => setMode(k)}
                className={`rounded-lg px-3 py-1.5 ${mode === k ? "bg-panel font-medium text-fg shadow-card" : "text-muted"}`}>{l}</button>))}
          </div>
          {mode === "batch" ? <BatchPick scan={scan} setScan={setScan} /> : (<>
          <label className={`flex cursor-pointer items-center gap-3 rounded-xl border border-dashed px-4 py-4 transition
              ${hdl.length ? "border-accent bg-accent/5" : "border-line hover:border-accent hover:bg-panel2/60"}`}>
            <FolderUp size={22} className={hdl.length ? "text-accent" : "text-muted"} />
            <div className="min-w-0 flex-1">
              {hdl.length ? (
                <>
                  <div className="font-medium">{folder || "已选择文件夹"}</div>
                  <div className="truncate text-xs text-muted">{hdl.length} 个 Verilog 文件 · 点击可重新选择</div>
                </>) : (
                <>
                  <div className="font-medium">上传 RTL 文件夹</div>
                  <div className="text-xs text-muted">包含 .v / .sv 的文件夹；只会上传源码和 $readmem 数据文件</div>
                </>)}
            </div>
            {hdl.length > 0 && (
              <button type="button" className="rounded-lg p-1.5 text-muted hover:bg-panel2" aria-label="清除"
                onClick={(e) => { e.preventDefault(); setFiles([]); setTop(""); }}><X size={16} /></button>)}
            <input ref={dirInput} type="file" multiple className="hidden"
              onChange={(e) => { setFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
          </label>
          {hdl.length > 0 && (
            <div className="mt-3 space-y-2">
              <div className="flex flex-wrap gap-1.5">
                {hdl.slice(0, 6).map((f) => <span key={f.name} className="chip bg-panel2 text-muted"><FileCode2 size={12} />{f.name}</span>)}
                {hdl.length > 6 && <span className="chip bg-panel2 text-muted">+{hdl.length - 6}</span>}
              </div>
              <input className="input" placeholder="顶层模块名（可不填，自动识别）" value={top} onChange={(e) => setTop(e.target.value)} />
            </div>)}
          <div className="mt-4 text-sm text-muted">没有现成的 RTL？先用示例试试：</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {examples.map((e) => (
              <button key={e.name} type="button" onClick={() => toggle(e.name)}
                className={`chip border px-3 py-1.5 text-sm ${picked.has(e.name) ? "border-accent bg-accent/10 text-accent" : "border-line text-fg hover:bg-panel2"}`}>
                {picked.has(e.name) && <Check size={13} />}<span className="font-mono">{e.name}</span></button>))}
          </div>
          </>)}
        </Step>

        {/* 2 */}
        <Step n={2} title="选择数据集类型" done>
          <OutputPicker value={output} onChange={setOutput} />
        </Step>

        {/* 3 */}
        <Step n={3} title="流程参数" done>
          <ParamsPicker value={params} onChange={setParams} />
        </Step>

        {/* 4 */}
        <Step n={4} title="开始" done={false}>
          <button className="btn-primary w-full justify-center py-3 text-base" onClick={start} disabled={busy || count === 0}>
            {busy ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
            {busy ? "提交中…" : count ? `开始处理 ${count} 个设计${cleanParams(params) ? "（自定义参数）" : ""}` : "先在第 1 步选择设计"}
          </button>
          <p className="mt-2 text-center text-xs text-muted">
            {mode === "batch" ? "设计会按「设置」里的并发上限排队运行；服务重启后会自动接着跑。" : "小设计约 3–5 分钟；提交后会打开进度页，可以随时离开再回来看。"}</p>
          {err && <p className="mt-2 text-center text-sm text-bad">{err}</p>}
        </Step>
      </div>

      <Link to="/chat" className="card mt-4 flex items-center gap-3 px-5 py-4 hover:border-accent/60">
        <MessagesSquare size={20} className="text-accent" />
        <div className="flex-1">
          <div className="font-medium">不确定怎么做？问 agent</div>
          <div className="text-xs text-muted">让它帮你在 GitHub 上找设计、提交任务、解释失败原因</div>
        </div>
        <ChevronRight size={18} className="text-faint" />
      </Link>

      {jobs.length > 0 && (
        <section className="mt-8">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-sm font-medium text-muted">最近的任务</div>
            <Link to="/jobs" className="text-sm text-accent hover:underline">全部</Link>
          </div>
          <div className="card divide-y divide-line">
            {jobs.map((j) => (
              <Link key={j.id} to={`/jobs/${j.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-panel2/60">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{j.title}</div>
                  <div className="text-xs text-muted">{fmtTime(j.created)} · {j.counts?.passed ?? 0}/{Object.values(j.counts ?? {}).reduce((a, b) => a + b, 0)} 通过</div>
                </div>
                <StatusBadge status={j.status} />
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

function BatchPick({ scan, setScan }: { scan: Scan | null; setScan: (s: Scan | null) => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<"" | "upload" | "scan">("");
  const [err, setErr] = useState("");
  const [showSkip, setShowSkip] = useState(false);
  const dir = useRef<HTMLInputElement>(null);
  useEffect(() => { dir.current?.setAttribute("webkitdirectory", ""); }, []);

  async function doScan(p: string) {
    setBusy("scan"); setErr(""); setScan(null);
    try { setScan(await api.scan(p)); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  }
  async function upload(list: File[]) {
    const keep = list.filter((f) => SEND.test(f.name));
    if (!keep.length) { setErr("这个文件夹里没有 Verilog 文件"); return; }
    setBusy("upload"); setErr(""); setScan(null);
    try {
      const up = await api.upload(keep);
      const folder = (((keep[0] as any).webkitRelativePath || "") as string).split("/")[0];
      await doScan(folder ? `${up.path}/${folder}` : up.path);
    } catch (e: any) { setErr(e.message); setBusy(""); }
  }
  const ok = scan?.designs.filter((d) => !d.problem) ?? [];
  const bad = scan?.designs.filter((d) => d.problem) ?? [];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">准备一个文件夹，里面<b className="text-fg">每个子文件夹是一个设计</b>。可以上传，也可以直接填服务器上的路径（文件已经在服务器上时更快）。</p>
      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-line px-4 py-3 hover:border-accent hover:bg-panel2/60">
        {busy === "upload" ? <Loader2 size={20} className="animate-spin text-accent" /> : <FolderTree size={20} className="text-muted" />}
        <div className="flex-1"><div className="font-medium">{busy === "upload" ? "上传中…" : "上传装着多个设计的文件夹"}</div>
          <div className="text-xs text-muted">只会上传源码和 $readmem 数据文件</div></div>
        <input ref={dir} type="file" multiple className="hidden" onChange={(e) => { upload(Array.from(e.target.files || [])); e.target.value = ""; }} />
      </label>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (path.trim()) doScan(path.trim()); }}>
        <input className="input font-mono" placeholder="或填服务器上的目录，例如 /data/rtl_corpus" value={path} onChange={(e) => setPath(e.target.value)} />
        <button className="btn-ghost shrink-0" disabled={!path.trim() || !!busy}>
          {busy === "scan" ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />} 扫描</button>
      </form>
      {err && <p className="text-sm text-bad">{err}</p>}
      {scan && (
        <div className="rounded-xl bg-panel2 p-4 text-sm">
          <div className="font-medium">识别到 {scan.total} 个设计：<span className="text-ok">{scan.ok} 个将运行</span>
            {scan.skipped > 0 && <span className="text-warn">，{scan.skipped} 个将跳过</span>}</div>
          <div className="mt-1 truncate font-mono text-xs text-faint">{scan.root}</div>
          {ok.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {ok.slice(0, 10).map((d) => <span key={d.name} className="chip bg-panel text-muted"><span className="font-mono">{d.name}</span>{d.top !== d.name && <span className="text-faint">· 顶层 {d.top}</span>}</span>)}
              {ok.length > 10 && <span className="chip bg-panel text-muted">+{ok.length - 10}</span>}
            </div>)}
          {bad.length > 0 && (
            <div className="mt-3">
              <button type="button" className="flex items-center gap-1.5 text-xs text-warn" onClick={() => setShowSkip(!showSkip)}>
                <AlertTriangle size={13} /> {showSkip ? "收起" : "查看"}跳过的设计和原因</button>
              {showSkip && (
                <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs">
                  {bad.map((d) => <li key={d.name}><span className="font-mono">{d.name}</span>：<span className="text-muted">{d.problem}</span></li>)}
                </ul>)}
            </div>)}
        </div>)}
    </div>
  );
}
