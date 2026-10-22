import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Play, Upload, Wrench, ShieldCheck, Bot, Network, ArrowRight, ChevronRight } from "lucide-react";
import { api, fmtTime, Job } from "../lib/api";
import { StatusBadge } from "../components/ui";
import NewJobDialog from "../components/NewJobDialog";

const PIPE = [
  ["RTL", "Verilog / SV"], ["综合", "Yosys"], ["布局布线", "OpenROAD"], ["签核", "DRC · LVS"],
  ["自动修复", "诊断 → 修改 → 复核"], ["转图", "PyG 图数据集"], ["校验", "独立复算"],
];
const FEATURES = [
  { Icon: Wrench, title: "失败自己修", text: "天线违例、引脚放不下、违规单元……agent 读日志、诊断、选修法、重跑、复核。" },
  { Icon: ShieldCheck, title: "判定交给代码", text: "通过与否只由 DRC / LVS 和数据集校验器决定；工具没有判决就不算通过。" },
  { Icon: Bot, title: "模型随你选", text: "DeepSeek、通义、Kimi、GLM、OpenAI、Claude、Gemini 或本地模型，配置即用。" },
  { Icon: Network, title: "训练就绪的数据", text: "五种图视图 + 网表图，拥塞、线长、时序、IR drop、RC 寄生标签对齐到同一份 DEF。" },
];

export default function Home() {
  const nav = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dlg, setDlg] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.jobs().then((j) => setJobs(j.slice(0, 5))).catch(() => {}); }, []);

  async function demo() {
    setBusy(true);
    try {
      const r = await api.createJob([{ example: "counter8" }, { example: "alu8" }, { example: "uart_tx" }], "一键体验 · 3 个示例");
      nav(`/jobs/${r.job_id}`);
    } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <section className="relative overflow-hidden rounded-3xl border border-line bg-panel p-10">
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-accent/20 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 left-1/3 h-72 w-72 rounded-full bg-accent2/15 blur-3xl" />
        <div className="relative">
          <span className="chip bg-accent/15 text-accent">开源 EDA · 自主 agent</span>
          <h1 className="mt-4 text-4xl font-bold leading-tight tracking-tight">
            从 RTL 到签核版图，<br />
            <span className="bg-gradient-to-r from-accent to-accent2 bg-clip-text text-transparent">再到可训练的图数据集</span>
          </h1>
          <p className="mt-4 max-w-2xl text-[15px] leading-7 text-muted">
            给它一批 Verilog，r2g 在后台跑完综合、布局布线、DRC/LVS 签核；遇到失败自己诊断和修复；
            最后把通过签核的设计转成 GNN 图数据集，逐项校验后交给你。
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <button className="btn-primary px-5 py-2.5 text-[15px]" onClick={demo} disabled={busy}>
              <Play size={17} /> {busy ? "启动中…" : "一键体验（3 个示例，约 3 分钟）"}
            </button>
            <button className="btn-ghost px-5 py-2.5 text-[15px]" onClick={() => setDlg(true)}><Upload size={17} /> 上传我的 RTL</button>
            <Link to="/chat" className="btn-ghost px-5 py-2.5 text-[15px]">和 agent 对话 <ArrowRight size={16} /></Link>
          </div>
        </div>
      </section>

      <section className="mt-8">
        <div className="label mb-3">流程</div>
        <div className="flex flex-wrap items-center gap-2">
          {PIPE.map(([t, s], i) => (
            <div key={t} className="flex items-center gap-2">
              <div className={`rounded-xl border px-4 py-2.5 ${i === 4 ? "border-accent2/50 bg-accent2/10" : "border-line bg-panel"}`}>
                <div className="text-sm font-semibold">{t}</div>
                <div className="text-[11px] text-muted">{s}</div>
              </div>
              {i < PIPE.length - 1 && <ChevronRight size={16} className="text-faint" />}
            </div>
          ))}
        </div>
      </section>

      <section className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
        {FEATURES.map(({ Icon, title, text }) => (
          <div key={title} className="card p-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent"><Icon size={20} /></div>
            <div className="mt-4 font-semibold">{title}</div>
            <p className="mt-1.5 text-sm leading-6 text-muted">{text}</p>
          </div>
        ))}
      </section>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <div className="label">最近任务</div>
          <Link to="/jobs" className="text-sm text-accent hover:underline">全部任务</Link>
        </div>
        <div className="card divide-y divide-line">
          {jobs.length === 0 && <div className="p-6 text-sm text-muted">还没有任务。点上面的「一键体验」试试。</div>}
          {jobs.map((j) => (
            <Link key={j.id} to={`/jobs/${j.id}`} className="flex items-center justify-between px-5 py-3.5 hover:bg-panel2/60">
              <div>
                <div className="font-medium">{j.title}</div>
                <div className="text-xs text-muted">{fmtTime(j.created)} · {j.id}</div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted">{j.counts?.passed ?? 0} 通过 / {Object.values(j.counts ?? {}).reduce((a, b) => a + b, 0)}</span>
                <StatusBadge status={j.status} />
              </div>
            </Link>
          ))}
        </div>
      </section>
      {dlg && <NewJobDialog onClose={() => setDlg(false)} />}
    </div>
  );
}
