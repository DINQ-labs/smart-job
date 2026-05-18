import type { Block } from "@/content/docs";

export function headings(blocks: Block[]): { id: string; text: string }[] {
  const out: { id: string; text: string }[] = [];
  let n = 0;
  for (const b of blocks) {
    if (b.t === "h2") {
      out.push({ id: `sec-${n}`, text: b.text });
      n += 1;
    }
  }
  return out;
}

export default function DocBody({ blocks }: { blocks: Block[] }) {
  let h2 = 0;
  return (
    <div className="space-y-5">
      {blocks.map((b, i) => {
        switch (b.t) {
          case "h2": {
            const id = `sec-${h2}`;
            h2 += 1;
            return (
              <h2
                key={i}
                id={id}
                className="scroll-mt pt-4 text-xl font-semibold tracking-tight text-white"
              >
                {b.text}
              </h2>
            );
          }
          case "p":
            return (
              <p key={i} className="text-[15px] leading-7 text-slate-300">
                {b.text}
              </p>
            );
          case "ul":
            return (
              <ul key={i} className="space-y-2">
                {b.items.map((item, j) => (
                  <li key={j} className="flex gap-3 text-[15px] leading-7 text-slate-300">
                    <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            );
          case "pre":
            return (
              <pre
                key={i}
                className="overflow-x-auto rounded-xl border border-white/10 bg-ink-900 p-5 font-mono text-xs leading-relaxed text-slate-300"
              >
                {b.text}
              </pre>
            );
          case "table":
            return (
              <div key={i} className="overflow-hidden rounded-xl border border-white/10">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-white/10 bg-white/5 text-slate-400">
                      {b.head.map((h, j) => (
                        <th key={j} className="px-4 py-2.5 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((row, j) => (
                      <tr key={j} className="border-b border-white/5 last:border-0">
                        {row.map((cell, k) => (
                          <td
                            key={k}
                            className={`px-4 py-2.5 align-top ${
                              k === 0 ? "font-medium text-white" : "text-slate-400"
                            }`}
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "img":
            return (
              <img
                key={i}
                src={b.src}
                alt={b.alt}
                loading="lazy"
                className="w-full rounded-xl border border-white/10"
              />
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
