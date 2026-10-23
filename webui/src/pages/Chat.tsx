import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Send, Wrench, ChevronDown, ChevronRight, Plus, MessageSquare, ExternalLink, AlertCircle, Loader2 } from "lucide-react";
import { api, chatStream } from "../lib/api";
import { Md, Logo } from "../components/ui";

type Part = { kind: "text"; text: string } | { kind: "tool"; id: string; name: string; args: any; result?: any };
type Msg = { role: "user" | "assistant"; parts: Part[]; error?: string };

const TOOL_LABEL: Record<string, string> = {
  list_examples: "查看内置示例", start_job: "提交任务", job_status: "查询任务进度", list_jobs: "列出任务",
  design_detail: "查看设计详情", read_log: "读取日志", diagnose: "请修复引擎诊断", knowledge_lookup: "查询故障知识库",
  cancel_job: "取消任务", dataset_info: "查询数据集", check_environment: "检查工具链", read_rtl: "读取源码",
};
const SUGGEST = [
  "跑一下三个内置示例并转成图数据集",
  "最近的任务进展怎么样？",
  "PDN-0185 是什么问题，怎么解决？",
  "检查一下 nangate45 的工具链环境",
];

/** Rebuild the display from a stored session (internal message format). */
function fromStored(ms: any[]): Msg[] {
  const out: Msg[] = [];
  for (const m of ms) {
    if (m.role === "user") out.push({ role: "user", parts: [{ kind: "text", text: m.content }] });
    else if (m.role === "assistant") {
      const last = out[out.length - 1];
      const target = last && last.role === "assistant" ? last : (out.push({ role: "assistant", parts: [] }), out[out.length - 1]);
      if (m.content) target.parts.push({ kind: "text", text: m.content });
      for (const t of m.tool_calls ?? []) target.parts.push({ kind: "tool", id: t.id, name: t.name, args: t.arguments });
    } else if (m.role === "tool") {
      const last = out[out.length - 1];
      const p = last?.parts.find((x) => x.kind === "tool" && x.id === m.tool_call_id) as any;
      if (p) { try { p.result = JSON.parse(m.content); } catch { p.result = m.content; } }
    }
  }
  return out;
}

export default function Chat() {
  const [sessions, setSessions] = useState<any[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  const loadSessions = () => api.sessions().then(setSessions).catch(() => {});
  useEffect(() => { loadSessions(); }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  async function open(id: string) {
    setSid(id); const s = await api.session(id); setMsgs(fromStored(s.messages));
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setInput(""); setBusy(true);
    setMsgs((m) => [...m, { role: "user", parts: [{ kind: "text", text }] }, { role: "assistant", parts: [] }]);
    const upd = (f: (a: Msg) => void) => setMsgs((m) => {
      const c = m.slice(); const a = { ...c[c.length - 1], parts: c[c.length - 1].parts.slice() }; f(a); c[c.length - 1] = a; return c;
    });
    try {
      await chatStream(text, sid, (e) => {
        if (e.type === "session") setSid(e.session_id);
        else if (e.type === "text") upd((a) => {
          const p = a.parts[a.parts.length - 1];
          if (p && p.kind === "text") a.parts[a.parts.length - 1] = { kind: "text", text: p.text + e.delta };
          else a.parts.push({ kind: "text", text: e.delta });
        });
        else if (e.type === "tool_call") upd((a) => { a.parts.push({ kind: "tool", id: e.id, name: e.name, args: e.arguments }); });
        else if (e.type === "tool_result") upd((a) => {
          a.parts = a.parts.map((p) => (p.kind === "tool" && p.id === e.id ? { ...p, result: e.result } : p));
        });
        else if (e.type === "error") upd((a) => { a.error = e.message; });
      });
    } catch (err: any) { upd((a) => { a.error = err.message; }); }
    setBusy(false); loadSessions();
  }

  return (
    <div className="flex h-full">
      <div className="flex w-60 shrink-0 flex-col border-r border-line bg-panel/40">
        <div className="p-3"><button className="btn-ghost w-full justify-center" onClick={() => { setSid(null); setMsgs([]); }}><Plus size={15} /> 新对话</button></div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {sessions.filter((s) => s.title).map((s) => (
            <button key={s.id} onClick={() => open(s.id)}
              className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm ${sid === s.id ? "bg-panel2 text-fg" : "text-muted hover:bg-panel2/60"}`}>
              <MessageSquare size={14} className="shrink-0" /><span className="truncate">{s.title}</span></button>))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-6 py-8">
            {msgs.length === 0 && (
              <div className="mt-16 text-center">
                <div className="flex justify-center"><Logo size={48} /></div>
                <h1 className="mt-5 text-2xl font-bold">想把哪些 RTL 变成数据集？</h1>
                <p className="mt-2 text-sm text-muted">我会提交任务、跟踪进度、解释失败原因。所有通过/失败的结论都来自签核工具，不是我说了算。</p>
                <div className="mx-auto mt-8 grid max-w-xl grid-cols-2 gap-2">
                  {SUGGEST.map((s) => (
                    <button key={s} onClick={() => send(s)} className="card px-4 py-3 text-left text-sm hover:border-accent/60">{s}</button>))}
                </div>
              </div>)}
            <div className="space-y-6">
              {msgs.map((m, i) => m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent/15 px-4 py-2.5 text-[15px]">{(m.parts[0] as any).text}</div>
                </div>
              ) : (
                <div key={i} className="flex gap-3">
                  <div className="mt-1 shrink-0"><Logo size={26} /></div>
                  <div className="min-w-0 flex-1 space-y-2">
                    {m.parts.map((p, k) => p.kind === "text" ? <Md key={k}>{p.text}</Md> : <ToolCard key={k} p={p} />)}
                    {busy && i === msgs.length - 1 && !m.error && (
                      <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> 思考中…</div>)}
                    {m.error && (
                      <div className="flex items-start gap-2 rounded-xl bg-bad/10 px-3 py-2 text-sm text-bad">
                        <AlertCircle size={16} className="mt-0.5 shrink-0" />
                        <div>{m.error}<div className="mt-1 text-xs text-muted">请到 <Link to="/settings" className="underline">设置</Link> 检查模型与密钥。</div></div>
                      </div>)}
                  </div>
                </div>))}
            </div>
            <div ref={end} />
          </div>
        </div>
        <div className="border-t border-line bg-panel/40 px-6 py-4">
          <form className="mx-auto flex max-w-3xl gap-2" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder="例如：把 /data/rtl/my_cpu 跑一遍 nangate45 并转图" className="input min-h-[44px] resize-none py-2.5 text-[15px]" />
            <button className="btn-primary h-[44px] px-4" disabled={busy || !input.trim()}><Send size={17} /></button>
          </form>
        </div>
      </div>
    </div>
  );
}

function ToolCard({ p }: { p: Extract<Part, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  const done = p.result !== undefined;
  const err = done && p.result && typeof p.result === "object" && "error" in p.result;
  const jobId = p.result?.job_id;
  const argText = Object.entries(p.args || {}).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join("  ");
  return (
    <div className={`rounded-xl border ${err ? "border-bad/40" : "border-line"} bg-panel2/50`}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm">
        {done ? <Wrench size={14} className={err ? "text-bad" : "text-accent2"} /> : <Loader2 size={14} className="animate-spin text-run" />}
        <span className="font-medium">{TOOL_LABEL[p.name] ?? p.name}</span>
        <span className="truncate font-mono text-xs text-faint">{argText}</span>
        <span className="ml-auto flex items-center gap-2">
          {jobId && <Link to={`/jobs/${jobId}`} onClick={(e) => e.stopPropagation()} className="chip bg-accent/15 text-accent">打开任务 <ExternalLink size={11} /></Link>}
          {open ? <ChevronDown size={15} className="text-faint" /> : <ChevronRight size={15} className="text-faint" />}
        </span>
      </button>
      {open && done && (
        <pre className="max-h-72 overflow-auto border-t border-line px-3 py-2 font-mono text-[11.5px] leading-5 text-muted">
          {JSON.stringify(p.result, null, 2)}</pre>)}
    </div>
  );
}
