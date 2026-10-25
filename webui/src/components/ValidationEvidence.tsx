import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { StatusBadge } from "./ui";

type Verdict = "PASS" | "FAIL" | "UNKNOWN" | "STALE";
type Observation = {
  verdict?: string; reasons?: string[]; method?: string; verification_level?: string;
  metrics?: Record<string, unknown>; evidence?: unknown;
  checks?: Record<string, Observation | string>;
};
type Claim = Observation & {
  claim_id: string; claimed?: unknown; observed?: unknown; count_scope?: string;
};
export type Validation = {
  status?: string; verdict?: string; verification_level?: string;
  three_layer?: Record<string, Observation | string>;
  checks?: Record<string, Observation>; claims?: Claim[];
};

const LAYERS = [["final_result", "最终结果"], ["quantitative", "数量核对"], ["technical", "技术语义"]] as const;
const CHECKS = [["drc", "DRC"], ["lvs", "LVS"], ["rcx", "寄生提取 RCX"], ["sta", "静态时序 STA"],
  ["simulation", "功能仿真"], ["dataset", "数据集校验"]] as const;
const LABEL: Record<Verdict, string> = { PASS: "范围内通过", FAIL: "未通过", UNKNOWN: "证据不足", STALE: "证据已失效" };
const COLOR: Record<Verdict, string> = { PASS: "text-ok", FAIL: "text-bad", UNKNOWN: "text-warn", STALE: "text-warn" };

function verdict(value: unknown): Verdict {
  const v = typeof value === "object" && value !== null ? (value as Observation).verdict : value;
  return v === "PASS" || v === "FAIL" || v === "STALE" ? v : "UNKNOWN";
}

export const legacyValidation = (v?: Validation | null) => !v || v.status === "legacy_unreviewed";

/** Never turn an incomplete response or a producer's old passed flag into evidence. */
export function validationVerdict(v?: Validation | null): Verdict {
  const overall = verdict(v);
  if (overall !== "PASS") return overall;
  const required = [...LAYERS.map(([key]) => verdict(v?.three_layer?.[key])),
    ...["drc", "lvs", "rcx"].map((key) => verdict(v?.checks?.[key]))];
  return required.includes("STALE") ? "STALE" : required.includes("FAIL") ? "FAIL"
    : required.every((x) => x === "PASS") ? "PASS" : "UNKNOWN";
}

export function validationLabel(v?: Validation | null): string {
  return legacyValidation(v) ? "历史结果，三层未复核" : `核查${LABEL[validationVerdict(v)]}（${validationVerdict(v)}）`;
}

export function DesignStatusBadge({ status, validation }: { status: string; validation?: Validation | null }) {
  if (status !== "passed") return <StatusBadge status={status} />;
  const result = validationVerdict(validation);
  const Icon = result === "PASS" ? CheckCircle2 : result === "FAIL" ? XCircle : AlertTriangle;
  return <span className={`chip ${COLOR[result]} bg-panel2`}><Icon size={13} className="shrink-0" />
    {legacyValidation(validation) ? "历史结果，三层未复核" : `核查${LABEL[result]}`}</span>;
}

export function ValidationSummary({ value }: { value?: Validation | null }) {
  if (legacyValidation(value)) return null;
  return <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="三层核查摘要">
    {LAYERS.map(([key, label]) => <span key={key} className={COLOR[verdict(value?.three_layer?.[key])]}>
      {label}：{verdict(value?.three_layer?.[key])}</span>)}
  </div>;
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "UNKNOWN（未提供）";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function RecordDetails({ value, label }: { value: unknown; label: string }) {
  if (value === undefined || value === null || (Array.isArray(value) && !value.length)) return null;
  return <details className="mt-2 text-xs text-muted">
    <summary className="cursor-pointer">{label}</summary>
    <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-bg p-2">{display(value)}</pre>
  </details>;
}

/** Read-only projection of host evidence; the table verdict judges the claim, not the design. */
export function ValidationEvidence({ value: supplied, loadError, historical = false }: {
  value?: Validation | null; loadError?: string; historical?: boolean;
}) {
  const value = loadError ? undefined : supplied;
  const result = validationVerdict(value);
  return <section aria-label="核查证据" className="space-y-3">
    <div>
      <h3 className="font-semibold">核查证据</h3>
      <p className={`mt-1 text-sm ${COLOR[result]}`}>{legacyValidation(value) && !historical ? "核查证据不足（UNKNOWN）" : validationLabel(value)}</p>
      {loadError && <p role="alert" className="mt-1 text-sm text-warn">报告读取失败：{loadError}。当前无法确认最新证据。</p>}
      <p className="mt-1 text-xs text-muted">通过仅指已列检查的范围，不代表制造签核。STA 是静态时序分析，不是功能仿真；数据集校验也不等于 RTL 功能验证。</p>
    </div>
    <div>
      <p className="label mb-2">三层核查（DRC / LVS / RCX）</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {LAYERS.map(([key, label]) => {
          const layer = value?.three_layer?.[key];
          return <div key={key} className="rounded-xl bg-panel2 p-3">
            <div className="text-xs text-muted">{label}</div>
            <div className={`mt-1 text-sm font-medium ${COLOR[verdict(layer)]}`}>{LABEL[verdict(layer)]} · {verdict(layer)}</div>
            {layer && typeof layer === "object" && <>
              {layer.checks && <div className="mt-2 space-y-1 text-xs">
                {Object.entries(layer.checks).map(([name, check]) => <p key={name} className={COLOR[verdict(check)]}>{name.toUpperCase()}：{verdict(check)}</p>)}
              </div>}
              <RecordDetails value={layer.reasons} label="判定原因" />
              <RecordDetails value={layer.evidence ?? layer.checks} label="分项核查证据" />
            </>}
          </div>;
        })}
      </div>
      <p className="mt-2 break-words text-xs text-muted">验证层级：{({ transistor_hierarchical: "层级晶体管比较", structure_only: "结构比较", unverified: "UNKNOWN（未确认）" } as Record<string, string>)[value?.verification_level ?? "unverified"] ?? value?.verification_level}</p>
    </div>
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {CHECKS.map(([key, label]) => {
        const check = value?.checks?.[key];
        return <div key={key} className="min-w-0 rounded-xl border border-line p-3">
          <div className="text-xs text-muted">{label}</div>
          <div className={`mt-1 text-sm font-medium ${COLOR[verdict(check)]}`}>{LABEL[verdict(check)]} · {verdict(check)}</div>
          <p className="mt-1 break-words text-xs text-muted">方法：{check?.method ?? "UNKNOWN（未提供）"}</p>
          {check?.metrics && Object.keys(check.metrics).length
            ? <RecordDetails value={check.metrics} label="观测数量与计量范围" />
            : <p className="mt-1 text-xs text-muted">观测数量：UNKNOWN（未提供）</p>}
          <RecordDetails value={check?.reasons} label="判定原因" />
          <RecordDetails value={check?.evidence} label="原始证据" />
        </div>;
      })}
    </div>
    <div>
      <h4 className="text-sm font-medium">声称与实证</h4>
      <p className="mt-1 text-xs text-muted">本表判断声称是否得到证据支持。“失败”的声称得到支持，也不表示设计通过。</p>
      {value?.claims?.length ? <div className="mt-2 overflow-x-auto rounded-xl border border-line">
        <table className="w-full min-w-[540px] text-left text-xs">
          <thead className="bg-panel2 text-muted"><tr>{["声称", "实际观察", "声称核对", "证据与方法"].map((label) => <th key={label} className="p-3 font-medium">{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-line">{value.claims.map((claim, index) => <tr key={`${claim.claim_id}:${index}`} className="align-top">
            <td className="max-w-48 break-words p-3"><span className="font-mono text-faint">{claim.claim_id}</span><pre className="mt-1 whitespace-pre-wrap break-words">{display(claim.claimed)}</pre></td>
            <td className="max-w-48 break-words p-3"><pre className="whitespace-pre-wrap break-words">{display(claim.observed)}</pre>{claim.count_scope && <p className="mt-1 text-muted">计量范围：{claim.count_scope}</p>}</td>
            <td className={`p-3 ${COLOR[verdict(claim)]}`}>{verdict(claim) === "PASS" ? "有证据支持" : verdict(claim) === "FAIL" ? "与实证不符" : LABEL[verdict(claim)]}<span className="mt-1 block font-mono">{verdict(claim)}</span></td>
            <td className="max-w-64 break-all p-3 text-muted">{claim.method ?? "UNKNOWN"}<RecordDetails value={claim.evidence} label="证据路径与摘要" /><RecordDetails value={claim.reasons} label="判定原因" /></td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="mt-2 rounded-xl bg-panel2 p-3 text-sm text-muted">UNKNOWN：尚无声称与实证核对表。</p>}
    </div>
  </section>;
}
