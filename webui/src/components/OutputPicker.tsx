import { Network, Grid3x3 } from "lucide-react";
import { Output } from "../lib/api";

const OPTS: { id: Output; title: string; text: string; Icon: any }[] = [
  { id: "graph", title: "图数据集", text: "PyG 图：五种视图 + 网表图，节点/边上带拥塞、线长、时序、IR drop、RC 标签", Icon: Network },
  { id: "image", title: "图像数据集", text: "GCell 特征图：放置后的密度/RUDY/引脚通道 → 布线后的拥塞、IR drop 标签图", Icon: Grid3x3 },
];

export default function OutputPicker({ value, onChange, compact = false }: { value: Output; onChange: (o: Output) => void; compact?: boolean }) {
  return (
    <div className={`grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-2"}`}>
      {OPTS.map(({ id, title, text, Icon }) => (
        <button key={id} type="button" onClick={() => onChange(id)}
          className={`rounded-xl border px-3 py-2.5 text-left transition ${value === id ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
          <div className="flex items-center gap-2 text-sm font-medium"><Icon size={15} className={value === id ? "text-accent" : "text-muted"} />{title}</div>
          {!compact && <div className="mt-1 text-xs leading-5 text-muted">{text}</div>}
        </button>))}
    </div>
  );
}
