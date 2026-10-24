import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";

export type Params = Record<string, any>;
type Desc = { defaults: Params; fields: Record<string, { label: string; unit: string; min: number; max: number; help: string }>; source: string };

const ORDER = ["utilization", "die_side_um", "io_pct", "setup_uncertainty_pct", "hold_uncertainty_ns",
               "area_allowance", "pdn_floor_um", "place_density_lb_addon"];

/** r2g's measured defaults (shown, not hidden), or the user's own values. */
export default function ParamsPicker({ value, onChange }: { value: Params | null; onChange: (p: Params | null) => void }) {
  const [d, setD] = useState<Desc | null>(null);
  useEffect(() => { fetch("/api/params").then((r) => r.json()).then(setD).catch(() => {}); }, []);
  if (!d) return <div className="text-sm text-muted">加载中…</div>;
  const custom = value !== null;
  const tiers: [number | null, number][] = (value?.period_tiers ?? d.defaults.period_tiers);
  // "<100 个单元 0.5 ns；100–999 个 1 ns；…；≥5000 个 3 ns"
  const tierText = (t: [number | null, number][]) => t.map(([u, ns], i) => {
    const lo = i === 0 ? null : t[i - 1][0];
    const range = u === null ? `≥${lo}` : lo === null ? `<${u}` : `${lo}–${u - 1}`;
    return `${range} 个单元 ${ns} ns`;
  }).join("；");
  const set = (k: string, v: any) => onChange({ ...(value ?? {}), [k]: v });
  const fixedPeriod = value?.period_ns !== undefined && value?.period_ns !== null && value?.period_ns !== "";

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {[["r2g 经验参数（推荐）", false, "在 1249 个 nangate45 设计上实测定下的参数，和知识库学习时的条件一致"],
          ["自定义", true, "按自己的需要改时钟、利用率、die 等，没改的项仍用经验值"]].map(([t, c, s]) => (
          <button key={String(c)} type="button" onClick={() => onChange(c ? (value ?? {}) : null)} aria-pressed={custom === c}
            className={`rounded-xl border px-4 py-3 text-left ${custom === c ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
            <div className="font-medium">{t as string}</div>
            <div className="mt-1 text-xs leading-5 text-muted">{s as string}</div>
          </button>))}
      </div>

      {!custom && (
        <div className="rounded-xl bg-panel2 px-4 py-3 text-xs leading-6 text-muted">
          <div>时钟：按综合后单元数分档 —— {tierText(d.defaults.period_tiers)}</div>
          <div>利用率 {d.defaults.utilization * 100}% · die 按面积、引脚数和电源网格下限（core ≥ {d.defaults.pdn_floor_um} µm）自动算 · 面积余量 ×{d.defaults.area_allowance}</div>
          <div>时序约束：建立不确定度 {d.defaults.setup_uncertainty_pct}% 周期，保持 {d.defaults.hold_uncertainty_ns * 1000} ps，输入输出延迟 {d.defaults.io_pct}% 周期</div>
        </div>)}

      {custom && (
        <div className="space-y-4 rounded-xl border border-line p-4">
          <div>
            <div className="flex items-center gap-3 text-sm font-medium">时钟周期
              <label className="flex items-center gap-1.5 text-xs font-normal text-muted">
                <input type="radio" checked={!fixedPeriod} onChange={() => set("period_ns", null)} /> 按单元数分档</label>
              <label className="flex items-center gap-1.5 text-xs font-normal text-muted">
                <input type="radio" checked={fixedPeriod} onChange={() => set("period_ns", 2.0)} /> 所有设计用同一个</label>
            </div>
            {fixedPeriod ? (
              <div className="mt-2 flex items-center gap-2">
                <input className="input w-32" type="number" step="0.1" value={value?.period_ns} onChange={(e) => set("period_ns", e.target.value)} />
                <span className="text-sm text-muted">ns</span>
              </div>) : (
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {tiers.map(([u, ns], i) => (
                  <div key={i} className="rounded-lg bg-panel2 px-3 py-2">
                    <div className="text-[11px] text-muted">{u === null ? `≥ ${tiers[i - 1]?.[0] ?? 0} 个单元` : `< ${u} 个单元`}</div>
                    <div className="mt-1 flex items-center gap-1">
                      <input className="input px-2 py-1" type="number" step="0.1" value={ns}
                        onChange={(e) => set("period_tiers", tiers.map((t, j) => (j === i ? [t[0], e.target.value === "" ? "" : +e.target.value] : t)))} />
                      <span className="text-xs text-muted">ns</span>
                    </div>
                  </div>))}
              </div>)}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {ORDER.map((k) => {
              const f = d.fields[k]; const def = d.defaults[k];
              const changed = value?.[k] !== undefined && value?.[k] !== null && value?.[k] !== "" && +value[k] !== def;
              return (
                <label key={k} className="block">
                  <div className="mb-1 flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium">{f.label}{f.unit && <span className="font-normal text-faint"> （{f.unit}）</span>}</span>
                    {changed && <span className="text-xs text-accent2">已改</span>}
                  </div>
                  <input className="input" type="number" step="any" min={f.min} max={f.max}
                    placeholder={def === null ? "自动 / 不设" : `经验值 ${def}`} value={value?.[k] ?? ""}
                    onChange={(e) => set(k, e.target.value === "" ? null : e.target.value)} />
                  <div className="mt-1 text-[11px] leading-4 text-faint">{f.help}（范围 {f.min}–{f.max}）</div>
                </label>);
            })}
          </div>
          <button type="button" className="flex items-center gap-1.5 text-sm text-muted hover:text-fg" onClick={() => onChange({})}>
            <RotateCcw size={14} /> 全部恢复为经验值</button>
        </div>)}
    </div>
  );
}

/** Only the keys the user actually set; {} means "custom but nothing changed". */
export function cleanParams(p: Params | null): Params | undefined {
  if (!p) return undefined;
  const out: Params = {};
  for (const [k, v] of Object.entries(p)) if (v !== null && v !== "" && v !== undefined) out[k] = v;
  return Object.keys(out).length ? out : undefined;
}
