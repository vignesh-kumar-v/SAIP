import { ArrowUp, MessageSquarePlus, Reply, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { ActionCard, AssistantBubble, ConfirmRewind, ToolLine, UserBubble, itemsOf, toolsSummary, withQuote } from "../components/ChatParts";
import { Button, Card, Empty, Mark, Spinner, cx } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { ago } from "../lib/format";
import { navigate } from "../lib/router";
import { useLive } from "../lib/store";
import { useJson } from "../lib/hooks";
import type { ChatItem, ChatView as Chat, RewindPreview, RewindResult } from "../lib/types";

type Confirm = { item: number; message?: string; text: string; preview: RewindPreview };
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
  const [acting, setActing] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [rewinding, setRewinding] = useState(false);
  const [slow, setSlow] = useState(false);
  const [quote, setQuote] = useState<string | null>(null);
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
      setError(e instanceof ApiError && e.status === 404
        ? "This SAIP is older than the page: stop it (Ctrl-C) and start it again with `saip run`."
        : e instanceof ApiError ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!list.data) return;
    if (sessionFromUrl && current?.id !== sessionFromUrl) open(sessionFromUrl);
    else if (!current && !sessionFromUrl && list.data.chats[0]) open(list.data.chats[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.data]);
  const items = current ? itemsOf(current) : [];
  const changeable = !!current?.items;
  const pending = items.some((i) => i.kind === "action" && i.action?.state === "pending");
  const working = sending || acting !== null || rewinding;
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items.length, working, pending]);
  useEffect(() => {
    setSlow(false);
    if (!working) return;
    const t = setTimeout(() => setSlow(true), 6000);
    return () => clearTimeout(t);
  }, [working]);

  const submit = () => {
    if (!text.trim() || working || pending) return;
    const message = withQuote(quote, text.trim());
    setQuote(null);
    send(message);
  };
  const startQuote = (q: string) => {
    setQuote(q.length > 700 ? q.slice(0, 700).trimEnd() + "…" : q);
    setTimeout(() => box.current?.focus(), 0);
  };

  const send = async (message: string, chat: Chat | null = current) => {
    const m = message.trim();
    if (!m || !chat || working) return;
    setText("");
    setSending(true);
    setError(null);
    const now = Date.now() / 1000;
    const shown = itemsOf(chat);
    setCurrent({
      ...chat,
      items: chat.items ? [...chat.items, { id: Math.max(0, ...shown.map((i) => i.id)) + 1, kind: "user", at: now, text: m }] : undefined,
      messages: [...chat.messages, { role: "user", text: m, at: now }],
    });
    try {
      setCurrent(await post<Chat>(`/api/chats/${chat.id}`, { message: m }));
      list.reload();
    } catch (e: any) {
      setError(e.message);
    }
    setSending(false);
    box.current?.focus();
  };

  const decide = async (item: ChatItem, approve: boolean) => {
    if (!current || working) return;
    setActing(item.id);
    setError(null);
    try {
      setCurrent(await post<Chat>(`/api/chats/${current.id}/actions/${item.id}`, { approve }));
      list.reload();
    } catch (e: any) {
      setError(e.message);
    }
    setActing(null);
  };

  const doRewind = async (itemId: number, text: string, message?: string) => {
    if (!current) return;
    setRewinding(true);
    setError(null);
    try {
      const r = await post<RewindResult>(`/api/chats/${current.id}/rewind`, message === undefined ? { item: itemId } : { item: itemId, message });
      setCurrent(r.chat);
      setEditing(null);
      setConfirm(null);
      if (message === undefined) {
        setText(text);
        setTimeout(() => box.current?.focus(), 0);
      }
      if (r.skipped.length) setError(`Left alone because it changed since: ${r.skipped.join(", ")}`);
      list.reload();
    } catch (e: any) {
      setConfirm(null);
      setError(e.message);
    }
    setRewinding(false);
  };
  // Look at what a rewind would do first; an edit that drops nothing else goes straight through.
  const startRewind = async (item: ChatItem, message?: string) => {
    if (!current || working) return;
    setError(null);
    try {
      const preview = await get<RewindPreview>(`/api/chats/${current.id}/rewind?item=${item.id}`);
      const plain = message !== undefined && preview.drops <= 1 && !preview.undo.length && !preview.cannot_undo.length;
      if (plain) await doRewind(item.id, item.text, message);
      else setConfirm({ item: item.id, message, text: item.text, preview });
    } catch (e: any) {
      setError(e.message);
    }
  };

  const available = list.data?.available ?? true;

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[460px] gap-5 lg:grid-cols-[260px_1fr]">
      <Card className="scroll-thin hidden min-h-0 flex-col overflow-auto p-2 lg:flex">
        <Button variant="subtle" size="sm" className="m-1 mb-2" onClick={() => (setCurrent(null), create())}>
          <MessageSquarePlus size={15} /> New chat
        </Button>
        {error && !current && <p className="px-3 pb-2 text-[12px] text-stuck">{error}</p>}
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
                SAIP's assistant knows what you were doing in the last few minutes. It only talks, unless you let it use tools: it can then propose an action, and nothing happens until you approve it.
              </p>
            </div>
            {error && <div className="max-w-md rounded-xl border border-stuck/40 bg-stuck-soft px-3.5 py-2 text-[13px] text-ink">{error}</div>}
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
                  {toolsSummary(current.tools ?? []) && <> · {toolsSummary(current.tools ?? [])}</>}
                </div>
              </div>
            </header>
            <div className="scroll-thin flex-1 space-y-4 overflow-auto px-5 py-5">
              <AnimatePresence initial={false}>
                {items.map((it) => (
                  <motion.div key={it.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
                    {it.kind === "user" ? (
                      <UserBubble
                        item={it}
                        canChange={changeable}
                        editing={editing === it.id}
                        disabled={working || pending}
                        onEdit={() => setEditing(it.id)}
                        onCancel={() => setEditing(null)}
                        onSend={(t) => startRewind(it, t.trim())}
                        onRewind={() => startRewind(it)}
                      />
                    ) : it.kind === "assistant" ? (
                      <AssistantBubble text={it.text} onQuote={startQuote} disabled={working || pending} />
                    ) : it.kind === "tool" ? (
                      <ToolLine item={it} />
                    ) : (
                      <ActionCard item={it} busy={acting === it.id} locked={working} onDecide={(ok) => decide(it, ok)} />
                    )}
                  </motion.div>
                ))}
                {working && (
                  <motion.div key="typing" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex">
                    <div className="flex items-center gap-1.5 rounded-2xl border border-line bg-raised px-4 py-3">
                      {[0, 1, 2].map((d) => (
                        <motion.span key={d} className="h-1.5 w-1.5 rounded-full bg-muted" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1.1, repeat: Infinity, delay: d * 0.18 }} />
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              {slow && <p className="text-center text-[12px] text-faint">Still working: the model may be using tools.</p>}
              <div ref={end} />
            </div>
            {error && <div className="mx-5 mb-2 rounded-xl border border-stuck/40 bg-stuck-soft px-3.5 py-2 text-[13px] text-ink">{error}</div>}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
              className="flex flex-wrap items-end gap-2 border-t border-line p-3"
            >
              <AnimatePresence initial={false}>
                {quote && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="w-full overflow-hidden">
                    <div className="flex items-start gap-2.5 rounded-xl border border-line bg-raised px-3 py-2">
                      <Reply size={14} className="mt-1 shrink-0 text-flow" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[11.5px] font-medium text-flow">Replying to</div>
                        <p className="line-clamp-3 text-[13px] leading-snug whitespace-pre-wrap text-muted">{quote}</p>
                      </div>
                      <button type="button" onClick={() => setQuote(null)} aria-label="Remove the quote" className="rounded-md p-1 text-faint transition-colors hover:text-ink">
                        <X size={14} />
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              <textarea
                ref={box}
                value={text}
                rows={1}
                disabled={pending}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit();
                  }
                }}
                placeholder={quote ? "Write your reply" : "Ask something, or say where you're stuck"}
                className="max-h-40 min-h-[42px] flex-1 resize-none rounded-xl border border-line bg-canvas px-3.5 py-2.5 text-[14.5px] text-ink placeholder:text-faint focus:border-flow focus:outline-none disabled:opacity-50"
              />
              <Button type="submit" variant="primary" disabled={!text.trim() || working || pending} aria-label="Send" className="h-[42px] w-[42px] px-0">
                {working ? <Spinner /> : <ArrowUp size={18} />}
              </Button>
              {pending && <p className="w-full px-1 text-[12px] text-faint">Approve or decline the proposed action first.</p>}
            </form>
            <ConfirmRewind
              preview={confirm?.preview ?? null}
              edit={confirm?.message !== undefined}
              busy={rewinding}
              onConfirm={() => confirm && doRewind(confirm.item, confirm.text, confirm.message)}
              onCancel={() => setConfirm(null)}
            />
          </>
        )}
      </Card>
    </div>
  );
}
