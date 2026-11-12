// A small, safe Markdown renderer for chat replies: paragraphs, headings, lists, quotes, fenced code with a Copy
// button, `inline code`, **bold**, *italic* and http(s) links. It builds React elements only (never HTML strings),
// so nothing a model or a web page writes can inject markup.
import { Check, Copy } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { cx } from "./ui";

type Block =
  | { t: "code"; lang: string; text: string }
  | { t: "h"; level: number; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "quote"; text: string }
  | { t: "p"; text: string };

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
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
      const items: string[] = [];
      while (i < lines.length && re.test(lines[i])) {
        let item = lines[i].replace(re, "$1");
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
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
    if (!line.trim()) {
      i++;
      continue;
    }
    const p: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(`{3,}|~{3,})/.test(lines[i]) && !/^(#{1,4})\s/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && !/^>/.test(lines[i])) p.push(lines[i++]);
    out.push({ t: "p", text: p.join("\n") });
  }
  return out;
}

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))|(\*[^*\s][^*\n]*\*)|(https?:\/\/[^\s<>)]+)/g;

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
    else if (m[4]) {
      const [, label, url] = s.match(/^\[([^\]]+)\]\((.+)\)$/)!;
      out.push(<a key={k++} href={url} target="_blank" rel="noreferrer noopener" className="text-flow underline underline-offset-2">{label}</a>);
    } else if (m[5]) out.push(<em key={k++}>{s.slice(1, -1)}</em>);
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
            return <ul key={k} className="list-disc space-y-1 pl-5 marker:text-faint">{b.items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ul>;
          case "ol":
            return <ol key={k} className="list-decimal space-y-1 pl-5 marker:text-faint">{b.items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ol>;
          case "quote":
            return <blockquote key={k} className="border-l-2 border-line-strong pl-3 text-muted">{inline(b.text)}</blockquote>;
          default:
            return <p key={k}>{lines(b.text)}</p>;
        }
      })}
    </div>
  );
}
