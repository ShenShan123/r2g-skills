import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderUp, X, Sparkles, FileCode2 } from "lucide-react";
import { api, Output } from "../lib/api";
import OutputPicker from "./OutputPicker";

export default function NewJobDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [examples, setExamples] = useState<{ name: string; top: string; description: string }[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<File[]>([]);
  const [top, setTop] = useState("");
  const [output, setOutput] = useState<Output>("graph");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const dirInput = useRef<HTMLInputElement>(null);

  useEffect(() => { api.examples().then((r) => setExamples(r.examples)); }, []);
  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);

  const hdl = files.filter((f) => /\.(s?v|s?vh)$/i.test(f.name));
  // HDL plus the data files $readmem reads; everything else in the folder
  // (.git, build output, docs) is never sent
  const data = files.filter((f) => /\.(hex|mem|bin|txt|dat|coe|mif|init|rom|data)$/i.test(f.name));
  const toggle = (n: string) => { const s = new Set(picked); s.has(n) ? s.delete(n) : s.add(n); setPicked(s); };

  async function submit() {
    setBusy(true); setErr("");
    try {
      const designs: any[] = [...picked].map((example) => ({ example }));
      if (hdl.length) {
        const up = await api.upload([...hdl, ...data]);
        designs.push({ path: up.path, top: top || undefined, name: top || undefined });
      }
      if (!designs.length) throw new Error("请至少选择一个示例或上传 RTL");
      const r = await api.createJob(designs, "", output);
      onClose(); nav(`/jobs/${r.job_id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="card w-full max-w-xl p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">新建任务</h2>
          <button className="rounded-lg p-1 text-muted hover:bg-panel2" onClick={onClose}><X size={18} /></button>
        </div>
        <p className="mt-1 text-sm text-muted">平台 nangate45（45 nm）。设计会依次经过综合、布局布线、DRC/LVS 签核、自动修复，再生成你选择的数据集并逐项校验。</p>

        <div className="label mt-5">输出</div>
        <div className="mt-2"><OutputPicker value={output} onChange={setOutput} /></div>

        <div className="label mt-5">内置示例</div>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {examples.map((e) => (
            <button key={e.name} onClick={() => toggle(e.name)}
              className={`rounded-xl border px-3 py-2.5 text-left text-sm transition ${picked.has(e.name)
                ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
              <div className="font-medium font-mono">{e.name}</div>
              <div className="text-xs text-muted">顶层 {e.top}</div>
            </button>
          ))}
        </div>

        <div className="label mt-5">上传你的 RTL</div>
        <label className="mt-2 flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-line px-4 py-6 text-sm text-muted hover:border-accent hover:text-fg">
          <FolderUp size={22} />
          <span className="mt-2">选择一个包含 .v / .sv 文件的文件夹</span>
          <input ref={dirInput} type="file" multiple className="hidden"
            onChange={(e) => setFiles(Array.from(e.target.files || []))} />
        </label>
        {hdl.length > 0 && (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {hdl.slice(0, 8).map((f) => (
                <span key={f.name} className="chip bg-panel2 text-muted"><FileCode2 size={12} />{f.name}</span>
              ))}
              {hdl.length > 8 && <span className="chip bg-panel2 text-muted">+{hdl.length - 8}</span>}
              {data.length > 0 && <span className="chip bg-panel2 text-muted">数据文件 {data.length} 个</span>}
            </div>
            <input className="input" placeholder="顶层模块名（可留空，自动识别）" value={top} onChange={(e) => setTop(e.target.value)} />
          </div>
        )}

        {err && <div className="mt-4 rounded-xl bg-bad/10 px-3 py-2 text-sm text-bad">{err}</div>}
        <div className="mt-6 flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={busy || (!picked.size && !hdl.length)} onClick={submit}>
            <Sparkles size={16} /> {busy ? "提交中…" : "开始运行"}
          </button>
        </div>
      </div>
    </div>
  );
}
