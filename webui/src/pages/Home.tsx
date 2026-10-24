import { useEffect, useRef, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { FolderUp, Play, Loader2, X, FileCode2, MessagesSquare, ChevronRight, Check } from "lucide-react";
import { api, fmtTime, Job, Output } from "../lib/api";
import OutputPicker from "../components/OutputPicker";
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
  const dirInput = useRef<HTMLInputElement>(null);

  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => {
    api.examples().then((r) => setExamples(r.examples)).catch(() => {});
    api.jobs().then((j) => setJobs(j.slice(0, 4))).catch(() => {});
  }, []);

  const hdl = files.filter((f) => HDL.test(f.name));
  const folder = files[0] ? (((files[0] as any).webkitRelativePath || "") as string).split("/")[0] : "";
  const toggle = (n: string) => { const s = new Set(picked); s.has(n) ? s.delete(n) : s.add(n); setPicked(s); };
  const count = picked.size + (hdl.length ? 1 : 0);

  async function start() {
    setBusy(true); setErr("");
    try {
      const designs: any[] = [...picked].map((example) => ({ example }));
      if (hdl.length) {
        const up = await api.upload(files.filter((f) => SEND.test(f.name)));
        designs.push({ path: up.path, top: top || undefined, name: top || folder || undefined });
      }
      const r = await api.createJob(designs, "", output);
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
        </Step>

        {/* 2 */}
        <Step n={2} title="选择数据集类型" done>
          <OutputPicker value={output} onChange={setOutput} />
        </Step>

        {/* 3 */}
        <Step n={3} title="开始" done={false}>
          <button className="btn-primary w-full justify-center py-3 text-base" onClick={start} disabled={busy || count === 0}>
            {busy ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} />}
            {busy ? "提交中…" : count ? `开始处理 ${count} 个设计` : "先在第 1 步选择设计"}
          </button>
          <p className="mt-2 text-center text-xs text-muted">小设计约 3–5 分钟；提交后会打开进度页，可以随时离开再回来看。</p>
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
