// A small, safe Markdown renderer for chat replies: paragraphs, headings, lists, quotes, fenced code with a Copy
// button, `inline code`, **bold**, *italic* and http(s) links. It builds React elements only (never HTML strings),
// so nothing a model or a web page writes can inject markup.
import { Check, Copy } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { cx } from "./ui";

type ListItem = { text: string; checked?: boolean; sub?: { ordered: boolean; items: ListItem[] } };

type Block =
  | { t: "code"; lang: string; text: string }
  | { t: "h"; level: number; text: string }
  | { t: "ul" | "ol"; items: ListItem[] }
  | { t: "quote"; text: string }
  | { t: "hr" }
  | { t: "table"; headers: string[]; rows: string[][] }
  | { t: "p"; text: string };

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const HR_RE = /^\s*(?:[-*_]\s*){3,}$/;
const LIST_RE = /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$/;

function parseTask(raw: string): { text: string; checked?: boolean } {
  const m = raw.match(/^\[( |x|X)\]\s+(.*)$/);
  return m ? { text: m[2], checked: m[1].toLowerCase() === "x" } : { text: raw };
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[i])) body.push(lines[i++]);
      i++; // the closing fence (a missing one just ends the block at the end of the text)
      out.push({ t: "code", lang: fence[2], text: body.join("\n").replace(/\n+$/, "") });
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      out.push({ t: "h", level: h[1].length, text: h[2] });
      i++;
      continue;
    }
    if (HR_RE.test(line)) {
      out.push({ t: "hr" });
      i++;
      continue;
    }
    const listMatch = line.match(LIST_RE);
    if (listMatch) {
      const baseIndent = listMatch[1].length;
      const ordered = Boolean(listMatch[3]);
      const items: ListItem[] = [];
      while (i < lines.length) {
        const m = lines[i].match(LIST_RE);
        if (!m || m[1].length < baseIndent) break;
        if (m[1].length > baseIndent && items.length > 0) {
          const subOrdered = Boolean(m[3]);
          const subIndent = m[1].length;
          const subItems: ListItem[] = [];
          while (i < lines.length) {
            const sm = lines[i].match(LIST_RE);
            if (!sm || sm[1].length < subIndent) break;
            if (Boolean(sm[3]) !== subOrdered) break;
            subItems.push(parseTask(sm[4]));
            i++;
          }
          items[items.length - 1].sub = { ordered: subOrdered, items: subItems };
          continue;
        }
        if (Boolean(m[3]) !== ordered) break;
        const parsed = parseTask(m[4]);
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !LIST_RE.test(lines[i])) parsed.text += " " + lines[i++].trim();
        items.push(parsed);
      }
      out.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    if (/^>\s?/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
      out.push({ t: "quote", text: q.join(" ") });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const headers = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
      out.push({ t: "table", headers, rows });
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const p: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(`{3,}|~{3,})/.test(lines[i]) && !/^(#{1,4})\s/.test(lines[i]) && !HR_RE.test(lines[i]) && !LIST_RE.test(lines[i]) && !/^>/.test(lines[i]) && !(lines[i].includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))) p.push(lines[i++]);
    out.push({ t: "p", text: p.join("\n") });
  }
  return out;
}

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(~~[^~\n]+~~)|(\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))|(\*[^*\s][^*\n]*\*)|(https?:\/\/[^\s<>)]+)/g;

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const s = m[0];
    if (m[1]) out.push(<code key={k++} className="num rounded-md bg-canvas px-1.5 py-0.5 text-[0.88em] text-ink">{s.slice(1, -1)}</code>);
    else if (m[2] || m[3]) out.push(<strong key={k++} className="font-semibold text-ink">{s.slice(2, -2)}</strong>);
    else if (m[4]) out.push(<del key={k++} className="text-faint">{s.slice(2, -2)}</del>);
    else if (m[5]) {
      const [, label, url] = s.match(/^\[([^\]]+)\]\((.+)\)$/)!;
      out.push(<a key={k++} href={url} target="_blank" rel="noreferrer noopener" className="text-flow underline underline-offset-2">{label}</a>);
    } else if (m[6]) out.push(<em key={k++}>{s.slice(1, -1)}</em>);
    else out.push(<a key={k++} href={s.replace(/[.,;:!?]+$/, "")} target="_blank" rel="noreferrer noopener" className="text-flow underline underline-offset-2">{s}</a>);
    last = at + s.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function lines(text: string): ReactNode[] {
  return text.split("\n").map((l, j, a) => (
    <Fragment key={j}>
      {inline(l)}
      {j < a.length - 1 && <br />}
    </Fragment>
  ));
}

export function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1600);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  };
  return (
    <div className="my-2.5 overflow-hidden rounded-xl border border-line bg-canvas">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5 text-[11.5px] text-faint">
        <span className="num">{lang || "text"}</span>
        <button onClick={copy} className="flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-muted transition-colors hover:text-ink">
          {done ? <Check size={13} className="text-flow" /> : <Copy size={13} />}
          {done ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="num scroll-thin max-h-[420px] overflow-auto px-3.5 py-3 text-[12.5px] leading-[1.65] text-ink">{text}</pre>
    </div>
  );
}

function ListBlock({ ordered, items }: { ordered: boolean; items: ListItem[] }) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={cx("space-y-1 pl-5 marker:text-faint", ordered ? "list-decimal" : "list-disc")}>
      {items.map((it, j) => (
        <li key={j} className={it.checked !== undefined ? "list-none -ml-5 flex flex-col gap-1" : undefined}>
          {it.checked !== undefined ? (
            <span className="flex items-start gap-2">
              <span
                aria-hidden
                className={cx(
                  "mt-1 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border text-[10px]",
                  it.checked ? "border-flow bg-flow/20 text-flow" : "border-line-strong",
                )}
              >
                {it.checked ? "✓" : ""}
              </span>
              <span className={it.checked ? "text-muted line-through" : undefined}>{inline(it.text)}</span>
            </span>
          ) : (
            inline(it.text)
          )}
          {it.sub && <ListBlock ordered={it.sub.ordered} items={it.sub.items} />}
        </li>
      ))}
    </Tag>
  );
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cx("space-y-2 break-words [&>:first-child]:mt-0 [&>:last-child]:mb-0", className)}>
      {parseBlocks(text).map((b, k) => {
        switch (b.t) {
          case "code":
            return <CodeBlock key={k} lang={b.lang} text={b.text} />;
          case "h":
            return <div key={k} className={cx("font-semibold text-ink", b.level <= 2 ? "pt-1 text-[16px]" : "text-[14.5px]")}>{inline(b.text)}</div>;
          case "ul":
            return <ListBlock key={k} ordered={false} items={b.items} />;
          case "ol":
            return <ListBlock key={k} ordered={true} items={b.items} />;
          case "hr":
            return <hr key={k} className="my-2 border-line" />;
          case "quote":
            return <blockquote key={k} className="border-l-2 border-line-strong pl-3 text-muted">{inline(b.text)}</blockquote>;
          case "table":
            return (
              <div key={k} className="scroll-thin my-2 overflow-x-auto rounded-xl border border-line bg-canvas">
                <table className="w-full border-collapse text-left text-[12.5px]">
                  <thead className="border-b border-line bg-raised text-[11.5px] font-medium text-muted">
                    <tr>
                      {b.headers.map((h, j) => (
                        <th key={j} className="px-3 py-2 whitespace-nowrap">{inline(h)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {b.rows.map((r, ri) => (
                      <tr key={ri} className="hover:bg-raised/40">
                        {b.headers.map((_, j) => (
                          <td key={j} className="px-3 py-1.5 align-top text-ink whitespace-nowrap">{inline(r[j] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default:
            return <p key={k}>{lines(b.text)}</p>;
        }
      })}
    </div>
  );
}
