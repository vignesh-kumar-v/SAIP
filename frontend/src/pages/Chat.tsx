import { ArrowUp, MessageSquarePlus } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Button, Card, Empty, Mark, Spinner, cx } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { ago } from "../lib/format";
import { navigate } from "../lib/router";
import { useLive } from "../lib/store";
import { useJson } from "../lib/hooks";

type Message = { role: "user" | "assistant"; text: string; at: number };
type Chat = { id: number; title: string; created: number; messages: Message[]; has_screen: boolean };
type Listing = { chats: { id: number; title: string; created: number; count: number; last: string }[]; available: boolean };

const STARTERS = ["Help me think through what to do next", "Explain what I'm looking at", "What am I missing?"];

export function Chat() {
  const helpCount = useLive((s) => s.log.filter((e) => e.kind === "help").length);
  const list = useJson<Listing>(`/api/chats?v=${helpCount}`);
  const sessionFromUrl = Number(new URLSearchParams(location.search).get("session")) || null;
  const [current, setCurrent] = useState<Chat | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const open = async (id: number) => {
    setError(null);
    try {
      setCurrent(await get<Chat>(`/api/chats/${id}`));
      navigate(`/chat?session=${id}`, true);
    } catch (e: any) {
      setError(e.message);
    }
  };
  const create = async (first?: string) => {
    setError(null);
    try {
      const c = await post<Chat>("/api/chats", { title: first ? first.slice(0, 50) : "New chat" });
      setCurrent(c);
      navigate(`/chat?session=${c.id}`, true);
      list.reload();
      if (first) send(first, c);
    } catch (e: any) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!list.data) return;
    if (sessionFromUrl && current?.id !== sessionFromUrl) open(sessionFromUrl);
    else if (!current && !sessionFromUrl && list.data.chats[0]) open(list.data.chats[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.data]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [current?.messages.length, sending]);

  const send = async (message: string, chat: Chat | null = current) => {
    const m = message.trim();
    if (!m || !chat || sending) return;
    setText("");
    setSending(true);
    setError(null);
    setCurrent({ ...chat, messages: [...chat.messages, { role: "user", text: m, at: Date.now() / 1000 }] });
    try {
      setCurrent(await post<Chat>(`/api/chats/${chat.id}`, { message: m }));
      list.reload();
    } catch (e: any) {
      setError(e.message);
    }
    setSending(false);
    box.current?.focus();
  };

  const available = list.data?.available ?? true;

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[460px] gap-5 lg:grid-cols-[260px_1fr]">
      <Card className="scroll-thin hidden min-h-0 flex-col overflow-auto p-2 lg:flex">
        <Button variant="subtle" size="sm" className="m-1 mb-2" onClick={() => create()}>
          <MessageSquarePlus size={15} /> New chat
        </Button>
        {list.data?.chats.length === 0 && <p className="px-3 py-2 text-[12.5px] text-faint">Conversations start when you press “Help me”, or here.</p>}
        <ul className="space-y-0.5">
          {list.data?.chats.map((c) => (
            <li key={c.id}>
              <button onClick={() => open(c.id)} className={cx("relative w-full rounded-xl px-3 py-2 text-left transition-colors", current?.id === c.id ? "text-ink" : "text-muted hover:text-ink")}>
                {current?.id === c.id && <motion.span layoutId="chat-on" className="absolute inset-0 rounded-xl bg-raised" transition={{ type: "spring", stiffness: 400, damping: 34 }} />}
                <span className="relative block truncate text-[13.5px]">{c.title}</span>
                <span className="relative block truncate text-[11.5px] text-faint">{c.last || "empty"}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="flex min-h-0 flex-col overflow-hidden">
        {!current ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-5 px-6 text-center">
            <Mark size={34} draw />
            <div>
              <h1 className="text-[22px] font-semibold tracking-tight">Talk it through</h1>
              <p className="mx-auto mt-1.5 max-w-[46ch] text-[14px] leading-relaxed text-muted">
                SAIP's assistant knows what you were doing in the last few minutes. It only talks: it never types or changes anything on your machine.
              </p>
            </div>
            {!available ? (
              <Empty>No model is connected. Add one in Settings, then come back.</Empty>
            ) : (
              <div className="flex flex-wrap justify-center gap-2">
                {STARTERS.map((s) => (
                  <button key={s} onClick={() => create(s)} className="rounded-full border border-line px-3.5 py-1.5 text-[13px] text-muted transition-colors hover:border-line-strong hover:text-ink">
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <header className="flex items-center gap-3 border-b border-line px-5 py-3">
              <Mark size={20} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[14px] font-medium">{current.title}</div>
                <div className="text-[11.5px] text-faint">
                  started {ago(Date.now() / 1000 - current.created)} · {current.has_screen ? "sees your screen text" : "activity summary only"}
                </div>
              </div>
            </header>
            <div className="scroll-thin flex-1 space-y-4 overflow-auto px-5 py-5">
              <AnimatePresence initial={false}>
                {current.messages.map((m, k) => (
                  <motion.div key={k} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className={cx("flex", m.role === "user" ? "justify-end" : "justify-start")}>
                    <div className={cx("max-w-[min(640px,88%)] rounded-2xl px-4 py-2.5 text-[14.5px] leading-relaxed whitespace-pre-wrap", m.role === "user" ? "bg-ink text-canvas" : "border border-line bg-raised text-ink")}>
                      {m.text}
                    </div>
                  </motion.div>
                ))}
                {sending && (
                  <motion.div key="typing" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex">
                    <div className="flex items-center gap-1.5 rounded-2xl border border-line bg-raised px-4 py-3">
                      {[0, 1, 2].map((d) => (
                        <motion.span key={d} className="h-1.5 w-1.5 rounded-full bg-muted" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1.1, repeat: Infinity, delay: d * 0.18 }} />
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              <div ref={end} />
            </div>
            {error && <div className="mx-5 mb-2 rounded-xl border border-stuck/40 bg-stuck-soft px-3.5 py-2 text-[13px] text-ink">{error}</div>}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(text);
              }}
              className="flex items-end gap-2 border-t border-line p-3"
            >
              <textarea
                ref={box}
                value={text}
                rows={1}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(text);
                  }
                }}
                placeholder="Ask something, or say where you're stuck"
                className="max-h-40 min-h-[42px] flex-1 resize-none rounded-xl border border-line bg-canvas px-3.5 py-2.5 text-[14.5px] text-ink placeholder:text-faint focus:border-flow focus:outline-none"
              />
              <Button type="submit" variant="primary" disabled={!text.trim() || sending} aria-label="Send" className="h-[42px] w-[42px] px-0">
                {sending ? <Spinner /> : <ArrowUp size={18} />}
              </Button>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}
