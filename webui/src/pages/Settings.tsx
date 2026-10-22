import { useEffect, useState } from "react";
import { Save, PlugZap, CheckCircle2, XCircle, KeyRound, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function Settings() {
  const [cfg, setCfg] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "test">("");

  const load = () => api.config().then((c) => {
    setCfg(c);
    setForm({ provider: c.model.provider, model: c.model.model, base_url: c.model.base_url,
              api_key_env: c.model.api_key_env, workers: c.workers, cores_per_design: c.cores_per_design });
  });
  useEffect(() => { load(); }, []);
  if (!cfg) return <div className="p-10 text-muted">加载中…</div>;
  const pre = cfg.providers[form.provider] ?? {};
  const set = (k: string, v: any) => setForm({ ...form, [k]: v });

  async function save() {
    setBusy("save"); setMsg(null);
    try {
      await api.saveConfig({ model: { provider: form.provider, model: form.model, base_url: form.base_url,
        api_key_env: form.api_key_env, ...(key ? { api_key: key } : {}) },
        workers: form.workers, cores_per_design: form.cores_per_design });
      setKey(""); await load(); setMsg({ ok: true, text: "已保存" });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(""); }
  }
  async function test() {
    setBusy("test"); setMsg(null);
    try { const r = await api.testModel(); setMsg({ ok: r.ok, text: `${r.message}${r.latency_ms ? `（${r.latency_ms} ms）` : ""}` }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(""); }
  }

  return (
    <div className="mx-auto max-w-3xl px-10 py-10">
      <h1 className="text-2xl font-bold">设置</h1>
      <p className="mt-1 text-sm text-muted">模型由你选择。主流的 OpenAI 兼容接口、Anthropic 和 Gemini 都可以直接用；密钥只保存在本机。</p>

      <section className="card mt-6 space-y-5 p-6">
        <div className="font-semibold">模型</div>
        <div>
          <div className="label mb-2">服务商</div>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(cfg.providers).map(([k, v]: any) => (
              <button key={k} onClick={() => setForm({ ...form, provider: k, model: "", base_url: "", api_key_env: "" })}
                className={`rounded-xl border px-3 py-2 text-left text-sm ${form.provider === k ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
                <div className="font-medium">{v.label}</div>
                <div className="truncate text-xs text-faint">{v.protocol === "anthropic" ? "Anthropic 协议" : "OpenAI 兼容"}</div>
              </button>))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="模型名"><input className="input" value={form.model} placeholder={pre.model || "必填"} onChange={(e) => set("model", e.target.value)} /></Field>
          <Field label="接口地址"><input className="input" value={form.base_url} placeholder={pre.base_url || "https://…/v1"} onChange={(e) => set("base_url", e.target.value)} /></Field>
          <Field label="API 密钥" hint={cfg.model_resolved.has_key ? "已配置（留空则不修改）" : "未配置"}>
            <div className="relative"><KeyRound size={15} className="absolute left-3 top-2.5 text-faint" />
              <input className="input pl-9" type="password" autoComplete="off" value={key} placeholder="sk-…" onChange={(e) => setKey(e.target.value)} /></div>
          </Field>
          <Field label="或从环境变量读取" hint="设置后优先使用环境变量">
            <input className="input font-mono" value={form.api_key_env} placeholder={pre.key_env || "R2G_API_KEY"} onChange={(e) => set("api_key_env", e.target.value)} /></Field>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-primary" onClick={save} disabled={!!busy}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} 保存</button>
          <button className="btn-ghost" onClick={test} disabled={!!busy}>{busy === "test" ? <Loader2 size={15} className="animate-spin" /> : <PlugZap size={15} />} 测试连接与工具调用</button>
          {msg && <span className={`flex items-center gap-1.5 text-sm ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}{msg.text}</span>}
        </div>
      </section>

      <section className="card mt-5 space-y-4 p-6">
        <div className="font-semibold">算力</div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="同时运行的设计数"><input className="input" type="number" min={1} value={form.workers} onChange={(e) => set("workers", +e.target.value)} /></Field>
          <Field label="每个设计的核数" hint="小设计会自动降到 4 核"><input className="input" type="number" min={1} value={form.cores_per_design} onChange={(e) => set("cores_per_design", +e.target.value)} /></Field>
        </div>
        <p className="text-xs text-muted">经验：同时运行数 × 每设计核数 ≈ 机器线程数。超出很多时机器看似很忙，实际产出反而下降。</p>
      </section>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: any }) {
  return <label className="block"><div className="mb-1.5 flex items-baseline justify-between"><span className="label">{label}</span>
    {hint && <span className="text-xs text-faint">{hint}</span>}</div>{children}</label>;
}
