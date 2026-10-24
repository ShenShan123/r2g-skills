import { Network, Grid3x3 } from "lucide-react";
import { Output } from "../lib/api";

const OPTS: { id: Output; title: string; text: string; Icon: any }[] = [
  { id: "graph", title: "图数据集", text: "给 GNN 用：电路转成图，节点和边上带拥塞、线长、时序、IR drop 等标签", Icon: Network },
  { id: "image", title: "图像数据集", text: "给 CNN 用：版图切成网格特征图，标签是布线后的拥塞和 IR drop 图", Icon: Grid3x3 },
];

export default function OutputPicker({ value, onChange }: { value: Output; onChange: (o: Output) => void }) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {OPTS.map(({ id, title, text, Icon }) => (
        <button key={id} type="button" onClick={() => onChange(id)} aria-pressed={value === id}
          className={`rounded-xl border px-4 py-3 text-left transition ${value === id ? "border-accent bg-accent/10" : "border-line hover:bg-panel2"}`}>
          <div className="flex items-center gap-2 font-medium">
            <Icon size={16} className={value === id ? "text-accent" : "text-muted"} />{title}
            <span className={`ml-auto h-4 w-4 rounded-full border-2 ${value === id ? "border-accent bg-accent shadow-[inset_0_0_0_2px_rgb(var(--panel))]" : "border-line"}`} />
          </div>
          <div className="mt-1 text-xs leading-5 text-muted">{text}</div>
        </button>))}
    </div>
  );
}
