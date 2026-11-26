import { useEffect, useState } from "react";
import { Save, PlugZap, CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { Toggle } from "../components/ui";
import { t, tb } from "../lib/i18n";

export default function Settings() {
  const [cfg, setCfg] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const [key, setKey] = useState("");
  const [adv, setAdv] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; where?: "model" | "compute" } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "test">("");
  const [kn, setKn] = useState<any>(null);
  const [machine, setMachine] = useState<any>(null);

  const load = () => api.config().then((c) => {
    setCfg(c);
    setForm({ provider: c.model.provider, model: c.model.model, base_url: c.model.base_url,
              api_key_env: c.model.api_key_env, thinking: c.model.thinking ?? true,
              workers: c.workers, cores_per_design: c.cores_per_design,
              min_free_gb: c.min_free_gb ?? 50, keep_intermediate: !!c.keep_intermediate });
  });
  useEffect(() => { load(); api.health().then((h) => { setKn(h.knowledge); setMachine(h.machine); }).catch(() => {}); }, []);
  if (!cfg) return <div className="p-10 text-muted">{t("加载中…")}</div>;
  const pre = cfg.providers[form.provider] ?? {};
  const set = (k: string, v: any) => setForm({ ...form, [k]: v });
  const sameProvider = form.provider === cfg.model.provider;
  const hasKey = sameProvider && cfg.model_resolved.has_key;
  const keyOptional = sameProvider && cfg.model_resolved.needs_key === false;

  const intOk = (v: any, min: number) => Number.isInteger(Number(v)) && String(v).trim() !== "" && Number(v) >= min;
  const invalid = !intOk(form.workers, 1) ? t("同时运行的设计数必须是 ≥ 1 的整数")
    : !intOk(form.cores_per_design, 1) ? t("每个设计的 CPU 核数必须是 ≥ 1 的整数")
    : !intOk(form.min_free_gb, 0) ? t("剩余空间下限必须是 ≥ 0 的整数") : "";
  const oversub = !invalid && machine?.threads && Number(form.workers) * Number(form.cores_per_design) > machine.threads;

  async function save(where: "model" | "compute" = "model"): Promise<boolean> {
    if (invalid) { setMsg({ ok: false, text: invalid, where: "compute" }); return false; }
    setBusy("save"); setMsg(null);
    try {
      await api.saveConfig({ model: { provider: form.provider, model: form.model, base_url: form.base_url,
        api_key_env: form.api_key_env, thinking: form.thinking, ...(key ? { api_key: key } : {}) },
        workers: Number(form.workers), cores_per_design: Number(form.cores_per_design),
        min_free_gb: Number(form.min_free_gb), keep_intermediate: form.keep_intermediate });
      setKey(""); await load(); window.dispatchEvent(new Event("r2g-config")); setMsg({ ok: true, text: t("已保存"), where }); return true;
    } catch (e: any) { setMsg({ ok: false, text: tb(e.message), where }); return false; } finally { setBusy(""); }
  }
  async function test() {
    // test what is on screen, not what was saved last time
    if (!(await save())) return;
    setBusy("test"); setMsg(null);
    try { const r = await api.testModel(); setMsg({ ok: r.ok, text: `${tb(r.message)}${r.latency_ms ? t("（{s} 秒）", { s: (r.latency_ms / 1000).toFixed(1) }) : ""}` }); }
    catch (e: any) { setMsg({ ok: false, text: tb(e.message) }); } finally { setBusy(""); }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <h1 className="text-2xl font-bold">{t("设置")}</h1>

      <section className="card mt-5 space-y-5 p-5 md:p-6">
        <div>
          <h2 className="font-semibold">{t("对话用的模型")}</h2>
          <p className="mt-1 text-sm text-muted">{t("只有「对话」需要模型；提交任务、签核和生成数据集不需要。密钥只保存在本机。")}</p>
        </div>
        <Field label={t("服务商")}>
          <select className="input" value={form.provider}
            onChange={(e) => setForm({ ...form, provider: e.target.value, model: "", base_url: "", api_key_env: "" })}>
            {Object.entries(cfg.providers).map(([k, v]: any) => <option key={k} value={k}>{tb(v.label)}</option>)}
          </select>
        </Field>
        <Field label={t("模型名")} hint={pre.model ? t("不填则用 {m}", { m: pre.model }) : t("必填")}>
          <input className="input" value={form.model} placeholder={pre.model || t("例如 my-model")} onChange={(e) => set("model", e.target.value)} />
        </Field>
        <Field label={t("API 密钥")} hint={hasKey ? t("✓ 已配置，不改就留空") : keyOptional ? t("本机接口，可不填") : t("未配置")}>
          <input className="input" type="password" autoComplete="off" value={key}
            placeholder={hasKey ? t("••••••••（已保存）") : t("粘贴你的 API 密钥")} onChange={(e) => setKey(e.target.value)} />
        </Field>
        {pre.thinking && (
          <div className="flex items-center justify-between gap-4 rounded-xl bg-panel2 px-4 py-3">
            <div>
              <div className="text-sm font-medium">{t("深度思考")}</div>
              <div className="text-xs text-muted">{t("开启后分析失败原因更准，每次回答多等几秒。思考过程会折叠显示在对话里。")}</div>
            </div>
            <Toggle on={form.thinking} onChange={(v) => set("thinking", v)} label={t("深度思考")} />
          </div>)}

        <div>
          <button className="flex items-center gap-1 text-sm text-muted hover:text-fg" onClick={() => setAdv(!adv)}>
            {adv ? <ChevronDown size={15} /> : <ChevronRight size={15} />} {t("高级选项")}
          </button>
          {adv && (
            <div className="mt-3 space-y-4">
              <Field label={t("接口地址")} hint={t("一般不用改")}>
                <input className="input font-mono" value={form.base_url} placeholder={pre.base_url || "https://…/v1"} onChange={(e) => set("base_url", e.target.value)} />
              </Field>
              <Field label={t("从环境变量读取密钥")} hint={t("填了就优先用它，而不是上面保存的密钥")}>
                <input className="input font-mono" value={form.api_key_env} placeholder={pre.key_env || "R2G_API_KEY"} onChange={(e) => set("api_key_env", e.target.value)} />
              </Field>
            </div>)}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <button className="btn-primary" onClick={() => save("model")} disabled={!!busy}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} {t("保存")}</button>
          <button className="btn-ghost" onClick={test} disabled={!!busy}>{busy === "test" ? <Loader2 size={15} className="animate-spin" /> : <PlugZap size={15} />} {t("保存并测试")}</button>
          {msg && msg.where !== "compute" && <span className={`flex items-center gap-1.5 text-sm ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}{msg.text}</span>}
        </div>
      </section>

      <section className="card mt-4 space-y-4 p-5 md:p-6">
        <div>
          <h2 className="font-semibold">{t("算力")}</h2>
          <p className="mt-1 text-sm text-muted">{t("网页、命令行和 MCP 共用这个上限，超出的设计会排队。")}</p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label={t("同时运行的设计数")}><input className="input" type="number" min={1} step={1} value={form.workers} aria-invalid={!intOk(form.workers, 1)} onChange={(e) => set("workers", e.target.value)} /></Field>
          <Field label={t("每个设计的 CPU 核数")}><input className="input" type="number" min={1} step={1} value={form.cores_per_design} aria-invalid={!intOk(form.cores_per_design, 1)} onChange={(e) => set("cores_per_design", e.target.value)} /></Field>
        </div>
        <p className="text-xs text-muted">
          {machine?.threads ? <>{t("本机 ")}<b>{machine.threads}</b>{t(" 个线程，建议两者相乘不超过这个数，例如同时 ")}<b>{Math.max(1, Math.floor(machine.threads / 8))}</b>{t(" 个 × 每个 8 核。")}</> : t("建议两者相乘约等于机器的线程数。")}
          {t("设得太大机器看起来很忙，实际反而更慢；如果机器还有别人在用，留出余量。")}
        </p>
        {oversub && <p role="alert" className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-warn">
          {t("{w} × {c} = {n} 个核，超过本机可用的 {m} 个线程，设计会互相抢 CPU。", { w: form.workers, c: form.cores_per_design, n: Number(form.workers) * Number(form.cores_per_design), m: machine.threads })}</p>}
        {invalid && <p role="alert" className="text-xs text-bad">{invalid}</p>}
        <div className="border-t border-line pt-4">
          <h3 className="text-sm font-semibold">{t("磁盘")}</h3>
          <p className="mt-1 text-xs text-muted">{t("当前剩余 {n} GB。剩余空间低于下限时暂停开始新设计，清理出空间后自动继续。", { n: machine?.free_gb ?? "—" })}</p>
          <div className="mt-3 grid grid-cols-2 gap-4">
            <Field label={t("剩余空间下限（GB）")}><input className="input" type="number" min={0} step={1} value={form.min_free_gb} aria-invalid={!intOk(form.min_free_gb, 0)} onChange={(e) => set("min_free_gb", e.target.value)} /></Field>
          </div>
          <div className="mt-3 flex items-center justify-between gap-4 rounded-xl bg-panel2 px-4 py-3">
            <div>
              <div className="text-sm font-medium">{t("保留通过设计的中间版图文件")}</div>
              <div className="text-xs text-muted">{t("关闭时（推荐），设计通过后删除各阶段的 ODB/DEF/GDS 副本，只留数据集、报告和日志，大批量时能省下大部分空间。")}</div>
            </div>
            <Toggle on={form.keep_intermediate} onChange={(v) => set("keep_intermediate", v)} label={t("保留中间文件")} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={() => save("compute")} disabled={!!busy || !!invalid}>{busy === "save" ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} {t("保存")}</button>
          {msg && msg.where === "compute" && <span className={`flex items-center gap-1.5 text-sm ${msg.ok ? "text-ok" : "text-bad"}`}>{msg.ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}{msg.text}</span>}
        </div>
      </section>

      {kn && (
        <section className="card mt-4 p-5 md:p-6">
          <h2 className="font-semibold">{t("修复经验库")}</h2>
          <p className="mt-1 text-sm text-muted">{t("每跑完一个设计都会记下结果，下次遇到类似问题修得更准。只保存在本机，不会上传。")}</p>
          <div className="mt-3 text-sm">{t("已记录 ")}<b>{kn.runs ?? "—"}</b>{t(" 次运行")}</div>
        </section>)}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: any }) {
  return <label className="block"><div className="mb-1.5 flex items-baseline justify-between gap-3"><span className="text-sm font-medium">{label}</span>
    {hint && <span className="truncate text-xs text-faint">{hint}</span>}</div>{children}</label>;
}
