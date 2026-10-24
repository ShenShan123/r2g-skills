import { useEffect, useState } from "react";
import { Save, PlugZap, CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { Toggle } from "../components/ui";

export default function Settings() {
  const [cfg, setCfg] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const [key, setKey] = useState("");
  const [adv, setAdv] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "test">("");
  const [kn, setKn] = useState<any>(null);

  const load = () => api.config().then((c) => {
    setCfg(c);
    setForm({ provider: c.model.provider, model: c.model.model, base_url: c.model.base_url,
              api_key_env: c.model.api_key_env, thinking: c.model.thinking ?? true,
              workers: c.workers, cores_per_design: c.cores_per_design });
  });
  useEffect(() => { load(); api.health().then((h) => setKn(h.knowledge)).catch(() => {}); }, []);
  if (!cfg) return <div className="p-10 text-muted">加载中…</div>;
  const pre = cfg.providers[form.provider] ?? {};
  const set = (k: string, v: any) => setForm({ ...form, [k]: v });
  const sameProvider = form.provider === cfg.model.provider;
  const hasKey = sameProvider && cfg.model_resolved.has_key;

  async function save(): Promise<boolean> {
    setBusy("save"); setMsg(null);
    try {
      await api.saveConfig({ model: { provider: form.provider, model: form.model, base_url: form.base_url,
        api_key_env: form.api_key_env, thinking: form.thinking, ...(key ? { api_key: key } : {}) },
        workers: form.workers, cores_per_design: form.cores_per_design });
      setKey(""); await load(); setMsg({ ok: true, text: "已保存" }); return true;
    } catch (e: any) { setMsg({ ok: false, text: e.message }); return false; } finally { setBusy(""); }
  }
  async function test() {
    // test what is on screen, not what was saved last time
    if (!(await save())) return;
    setBusy("test"); setMsg(null);
    try { const r = await api.testModel(); setMsg({ ok: r.ok, text: `${r.message}${r.latency_ms ? `（${(r.latency_ms / 1000).toFixed(1)} 秒）` : ""}` }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(""); }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 md:px-8 md:py-10">
      <h1 className="text-2xl font-bold">设置</h1>

      <section className="card mt-5 space-y-5 p-5 md:p-6">
        <div>
          <h2 className="font-semibold">对话用的模型</h2>
          <p className="mt-1 text-sm text-muted">只有「对话」需要模型；提交任务、签核和生成数据集不需要。密钥只保存在本机。</p>
        </div>
        <Field label="服务商">
          <select className="input" value={form.provider}
            onChange={(e) => setForm({ ...form, provider: e.target.value, model: "", base_url: "", api_key_env: "" })}>
            {Object.entries(cfg.providers).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </Field>
        <Field label="模型名" hint={pre.model ? `不填则用 ${pre.model}` : "必填"}>
          <input className="input" value={form.model} placeholder={pre.model || "例如 my-model"} onChange={(e) => set("model", e.target.value)} />
        </Field>
        <Field label="API 密钥" hint={hasKey ? "✓ 已配置，不改就留空" : "未配置"}>
          <input className="input" type="password" autoComplete="off" value={key}
            placeholder={hasKey ? "••••••••（已保存）" : "粘贴你的 API 密钥"} onChange={(e) => setKey(e.target.value)} />
        </Field>
        {pre.thinking && (
          <div className="flex items-center justify-between gap-4 rounded-xl bg-panel2 px-4 py-3">
            <div>
              <div className="text-sm font-medium">深度思考</div>
              <div className="text-xs text-muted">开启后分析失败原因更准，每次回答多等几秒。思考过程会折叠显示在对话里。</div>
            </div>
            <Toggle on={form.thinking} onChange={(v) => set("thinking", v)} label="深度思考" />
          </div>)}

        <div>
          <button className="flex items-center gap-1 text-sm text-muted hover:text-fg" onClick={() => setAdv(!adv)}>
            {adv ? <ChevronDown size={15} /> : <ChevronRight size={15} />} 高级选项
          </button>
          {adv && (
            <div className="mt-3 space-y-4">
              <Field label="接口地址" hint="一般不用改">
                <input className="input font-mono" value={form.base_url} placeholder={pre.base_url || "https://…/v1"} onChange={(e) => set("base_url", e.target.value)} />
              </Field>
              <Field label="从环境变量读取密钥" hint="填了就优先用它，而不是上面保存的密钥">
                <input className="input font-mono" value={form.api_key_env} placeholder={pre.key_env || "R2G_API_KEY"} onChange={(e) => set("api_key_env", e.target.value)} />
              </Field>
            </div>)}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <button className="btn-primary" onClick={save} disabled={!!busy}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} 保存</button>
          <button className="btn-ghost" onClick={test} disabled={!!busy}>{busy === "test" ? <Loader2 size={15} className="animate-spin" /> : <PlugZap size={15} />} 保存并测试</button>
          {msg && <span className={`flex items-center gap-1.5 text-sm ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}{msg.text}</span>}
        </div>
      </section>

      <section className="card mt-4 space-y-4 p-5 md:p-6">
        <div>
          <h2 className="font-semibold">算力</h2>
          <p className="mt-1 text-sm text-muted">网页、命令行和 MCP 共用这个上限，超出的设计会排队。</p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="同时运行的设计数"><input className="input" type="number" min={1} value={form.workers} onChange={(e) => set("workers", +e.target.value)} /></Field>
          <Field label="每个设计的 CPU 核数"><input className="input" type="number" min={1} value={form.cores_per_design} onChange={(e) => set("cores_per_design", +e.target.value)} /></Field>
        </div>
        <p className="text-xs text-muted">建议：两者相乘约等于机器的线程数。设得太大机器看起来很忙，实际反而更慢。改完点上面的「保存」。</p>
      </section>

      {kn && (
        <section className="card mt-4 p-5 md:p-6">
          <h2 className="font-semibold">修复经验库</h2>
          <p className="mt-1 text-sm text-muted">每跑完一个设计都会记下结果，下次遇到类似问题修得更准。只保存在本机，不会上传。</p>
          <div className="mt-3 text-sm">已记录 <b>{kn.runs ?? "—"}</b> 次运行</div>
        </section>)}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: any }) {
  return <label className="block"><div className="mb-1.5 flex items-baseline justify-between gap-3"><span className="text-sm font-medium">{label}</span>
    {hint && <span className="truncate text-xs text-faint">{hint}</span>}</div>{children}</label>;
}
