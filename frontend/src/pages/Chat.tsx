import * as Menu from "@radix-ui/react-dropdown-menu";
import { ArrowUp, Check, Eye, MessageSquarePlus, MoreHorizontal, Pencil, Reply, Search, Square, Trash2, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { ActionCard, AssistantBubble, ConfirmDelete, ConfirmRewind, ToolLine, UserBubble, itemsOf, toolsSummary, withQuote } from "../components/ChatParts";
import { Button, Card, Empty, Mark, cx } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { ago } from "../lib/format";
import { navigate } from "../lib/router";
import { useLive } from "../lib/store";
import { useJson } from "../lib/hooks";
import type { ChatItem, ChatView as Chat, RewindPreview, RewindResult } from "../lib/types";

type Confirm = { item: number; message?: string; text: string; preview: RewindPreview };
type Listing = { chats: { id: number; title: string; created: number; count: number; last: string }[]; available: boolean };

const STARTERS = ["Help me think through what to do next", "Explain what I'm looking at", "What am I missing?"];

function renderMasked(text: string) {
  return text.split(/(\[hidden\])/g).map((chunk, idx) =>
    chunk === "[hidden]" ? (
      <span key={idx} className="rounded bg-stuck/20 px-1 py-0.5 font-semibold text-stuck">
        [hidden]
      </span>
    ) : (
      chunk
    )
  );
}

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
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [toDelete, setToDelete] = useState<number[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [search, setSearch] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renamingTitle, setRenamingTitle] = useState("");
  const [showContext, setShowContext] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

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

  const stop = async () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (current) {
      post("/api/chats/stop", { id: current.id }).catch(() => {});
    }
    setSending(false);
    setActing(null);
    setRewinding(false);
  };

  const saveRename = async (id: number, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) {
      setRenamingId(null);
      return;
    }
    try {
      const res = await post<{ ok: boolean; chat: Chat; chats: Listing["chats"] }>("/api/chats/rename", { id, title: trimmed });
      if (current?.id === id) {
        setCurrent((prev) => (prev ? { ...prev, title: res.chat.title } : null));
      }
      list.reload();
    } catch (e: any) {
      setError(e.message);
    }
    setRenamingId(null);
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
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await post<Chat>(`/api/chats/${chat.id}`, { message: m }, controller.signal);
      setCurrent((prev) => ({
        ...res,
        context: res.context ?? prev?.context,
        screen: res.screen ?? prev?.screen,
      }));
      list.reload();
    } catch (e: any) {
      if (e.name !== "AbortError") {
        setError(e.message);
      }
    } finally {
      abortRef.current = null;
      setSending(false);
      box.current?.focus();
    }
  };

  const decide = async (item: ChatItem, approve: boolean) => {
    if (!current || working) return;
    setActing(item.id);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await post<Chat>(`/api/chats/${current.id}/actions/${item.id}`, { approve }, controller.signal);
      setCurrent((prev) => ({
        ...res,
        context: res.context ?? prev?.context,
        screen: res.screen ?? prev?.screen,
      }));
      list.reload();
    } catch (e: any) {
      if (e.name !== "AbortError") {
        setError(e.message);
      }
    } finally {
      abortRef.current = null;
      setActing(null);
    }
  };

  const doRewind = async (itemId: number, text: string, message?: string) => {
    if (!current) return;
    setRewinding(true);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const r = await post<RewindResult>(
        `/api/chats/${current.id}/rewind`,
        message === undefined ? { item: itemId } : { item: itemId, message },
        controller.signal
      );
      setCurrent((prev) => ({
        ...r.chat,
        context: r.chat.context ?? prev?.context,
        screen: r.chat.screen ?? prev?.screen,
      }));
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
      if (e.name !== "AbortError") {
        setError(e.message);
      }
    } finally {
      abortRef.current = null;
      setRewinding(false);
    }
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

  const chats = list.data?.chats ?? [];
  const stopSelecting = () => {
    setSelecting(false);
    setSelected(new Set());
  };
  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = chats.length > 0 && chats.every((c) => selected.has(c.id));
  // The chat being answered right now cannot be deleted until the reply is in.
  const busyWith = (ids: number[]) => working && current !== null && ids.includes(current.id);

  const remove = async () => {
    if (!toDelete || busyWith(toDelete)) return;
    setDeleting(true);
    setError(null);
    try {
      await post("/api/chats/delete", { ids: toDelete });
      if (current && toDelete.includes(current.id)) {
        setCurrent(null);
        setEditing(null);
        setQuote(null);
        navigate("/chat", true);
      }
      stopSelecting();
      list.reload();
    } catch (e: any) {
      setError(e instanceof ApiError && e.status === 404
        ? "This SAIP is older than the page: stop it (Ctrl-C) and start it again with `saip run`."
        : e.message);
    }
    setToDelete(null);
    setDeleting(false);
  };

  const available = list.data?.available ?? true;

  const filteredChats = search.trim()
    ? chats.filter(
        (c) =>
          c.title.toLowerCase().includes(search.trim().toLowerCase()) ||
          (c.last && c.last.toLowerCase().includes(search.trim().toLowerCase()))
      )
    : chats;

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[460px] gap-5 lg:grid-cols-[260px_1fr]">
      <Card className="hidden min-h-0 flex-col p-2 lg:flex">
        <div className="m-1 mb-2 flex gap-1.5">
          <Button variant="subtle" size="sm" className="flex-1" disabled={selecting} onClick={() => (setCurrent(null), create())}>
            <MessageSquarePlus size={15} /> New chat
          </Button>
          {chats.length > 0 && (
            <Button variant="ghost" size="sm" aria-pressed={selecting} onClick={() => (selecting ? stopSelecting() : setSelecting(true))}>
              {selecting ? "Done" : "Select"}
            </Button>
          )}
        </div>
        {chats.length > 0 && !selecting && (
          <div className="relative mx-1 mb-2">
            <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search chats…"
              aria-label="Search chats"
              className="h-8 w-full rounded-lg border border-line bg-canvas pl-7 pr-7 text-[12.5px] text-ink placeholder:text-faint focus:border-flow focus:outline-none"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute top-1/2 right-2 -translate-y-1/2 text-faint hover:text-ink"
              >
                <X size={12} />
              </button>
            )}
          </div>
        )}
        {selecting && (
          <div className="flex items-center justify-between px-3 pb-1.5 text-[12px]">
            <span className="text-faint">{selected.size} selected</span>
            <button
              onClick={() => setSelected(allSelected ? new Set() : new Set(chats.map((c) => c.id)))}
              className="rounded-md px-1 text-muted transition-colors hover:text-ink"
            >
              {allSelected ? "Select none" : "Select all"}
            </button>
          </div>
        )}
        {error && !current && <p className="px-3 pb-2 text-[12px] text-stuck">{error}</p>}
        {list.data?.chats.length === 0 && <p className="px-3 py-2 text-[12.5px] text-faint">Conversations start when you press “Help me”, or here.</p>}
        {filteredChats.length === 0 && search.trim() && <p className="px-3 py-2 text-[12.5px] text-faint">No matching chats.</p>}
        <ul className="scroll-thin min-h-0 flex-1 space-y-0.5 overflow-auto">
          {filteredChats.map((c) => {
            const on = selected.has(c.id);
            if (renamingId === c.id) {
              return (
                <li key={c.id} className="px-1 py-1">
                  <div className="flex items-center gap-1.5 rounded-xl border border-flow bg-canvas px-2.5 py-1.5 shadow-sm">
                    <input
                      autoFocus
                      value={renamingTitle}
                      onChange={(e) => setRenamingTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          saveRename(c.id, renamingTitle);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setRenamingId(null);
                        }
                      }}
                      onBlur={() => saveRename(c.id, renamingTitle)}
                      className="min-w-0 flex-1 bg-transparent text-[13px] text-ink focus:outline-none"
                    />
                  </div>
                </li>
              );
            }
            return (
              <li key={c.id} className="group relative">
                <button
                  onClick={() => (selecting ? toggle(c.id) : open(c.id))}
                  role={selecting ? "checkbox" : undefined}
                  aria-checked={selecting ? on : undefined}
                  className={cx(
                    "relative flex w-full items-start gap-2.5 rounded-xl px-3 py-2 text-left transition-colors",
                    selecting ? (on ? "bg-raised text-ink" : "text-muted hover:text-ink") : current?.id === c.id ? "text-ink" : "text-muted hover:text-ink",
                    !selecting && "pr-9",
                  )}
                >
                  {!selecting && current?.id === c.id && <motion.span layoutId="chat-on" className="absolute inset-0 rounded-xl bg-raised" transition={{ type: "spring", stiffness: 400, damping: 34 }} />}
                  {selecting && (
                    <span className={cx("relative mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-[5px] border transition-colors", on ? "border-ink bg-ink text-canvas" : "border-line-strong")}>
                      {on && <Check size={12} strokeWidth={3} />}
                    </span>
                  )}
                  <span className="relative min-w-0 flex-1">
                    <span className="block truncate text-[13.5px]">{c.title}</span>
                    <span className="block truncate text-[11.5px] text-faint">{c.last || "empty"}</span>
                  </span>
                </button>
                {!selecting && (
                  <Menu.Root>
                    <Menu.Trigger asChild>
                      <button
                        aria-label={`Actions for “${c.title}”`}
                        className={cx(
                          "absolute top-1/2 right-1.5 -translate-y-1/2 rounded-lg p-1.5 text-faint transition-opacity hover:text-ink focus-visible:opacity-100 data-[state=open]:opacity-100",
                          current?.id === c.id ? "opacity-80" : "opacity-50 group-hover:opacity-100",
                        )}
                      >
                        <MoreHorizontal size={14} />
                      </button>
                    </Menu.Trigger>
                    <Menu.Portal>
                      <Menu.Content side="right" align="start" sideOffset={6} className="z-50 min-w-[140px] rounded-xl border border-line bg-raised p-1 text-[13px] shadow-2xl">
                        <Menu.Item
                          disabled={busyWith([c.id])}
                          onSelect={() => {
                            setRenamingId(c.id);
                            setRenamingTitle(c.title);
                          }}
                          className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-ink outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-40 data-[highlighted]:bg-canvas"
                        >
                          <Pencil size={14} /> Rename
                        </Menu.Item>
                        <Menu.Item
                          disabled={busyWith([c.id])}
                          onSelect={() => setToDelete([c.id])}
                          className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-danger outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-40 data-[highlighted]:bg-canvas"
                        >
                          <Trash2 size={14} /> Delete
                        </Menu.Item>
                      </Menu.Content>
                    </Menu.Portal>
                  </Menu.Root>
                )}
              </li>
            );
          })}
        </ul>
        {selecting && (
          <div className="mt-2 border-t border-line px-1 pt-2">
            <Button variant="danger" size="sm" className="w-full" disabled={selected.size === 0 || busyWith([...selected])} onClick={() => setToDelete([...selected])}>
              <Trash2 size={14} /> {selected.size > 1 ? `Delete ${selected.size} chats` : "Delete"}
            </Button>
          </div>
        )}
      </Card>
      <ConfirmDelete
        titles={toDelete ? toDelete.map((id) => chats.find((c) => c.id === id)?.title ?? "A chat") : null}
        busy={deleting}
        onConfirm={remove}
        onCancel={() => setToDelete(null)}
      />

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
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11.5px] text-faint">
                  <span>started {ago(Date.now() / 1000 - current.created)}</span>
                  <span>·</span>
                  <button
                    type="button"
                    onClick={() => setShowContext((v) => !v)}
                    aria-expanded={showContext}
                    aria-label="What SAIP sees"
                    className={cx(
                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors focus:outline-none",
                      showContext
                        ? "border-flow bg-flow/10 text-flow"
                        : "border-line bg-canvas text-muted hover:border-line-strong hover:text-ink"
                    )}
                  >
                    <Eye size={12} />
                    <span>{current.has_screen ? "sees your screen text" : "activity summary only"}</span>
                    <span className="opacity-70">· What SAIP sees</span>
                  </button>
                  {toolsSummary(current.tools ?? []) && <span>· {toolsSummary(current.tools ?? [])}</span>}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                disabled={busyWith([current.id])}
                onClick={() => setToDelete([current.id])}
                aria-label={`Delete “${current.title}”`}
                className="text-muted hover:text-danger"
              >
                <Trash2 size={14} />
              </Button>
            </header>
            <AnimatePresence>
              {showContext && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden border-b border-line bg-raised/40 px-5 py-3"
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] font-medium text-ink">What SAIP sees</span>
                      <button
                        type="button"
                        onClick={() => setShowContext(false)}
                        className="text-[11.5px] text-faint hover:text-ink"
                      >
                        Close
                      </button>
                    </div>
                    {!current.context && !current.screen ? (
                      <p className="text-[12px] text-faint italic">No activity snapshot was captured yet.</p>
                    ) : (
                      <div className="space-y-2.5">
                        {current.context && (
                          <div>
                            <div className="mb-1 text-[11px] font-medium text-faint">Activity snapshot</div>
                            <pre className="num scroll-thin max-h-36 overflow-auto rounded-lg border border-line bg-canvas p-2.5 text-[11.5px] leading-relaxed whitespace-pre-wrap text-muted">
                              {renderMasked(current.context)}
                            </pre>
                          </div>
                        )}
                        {current.screen && (
                          <div>
                            <div className="mb-1 text-[11px] font-medium text-faint">Screen text</div>
                            <pre className="num scroll-thin max-h-36 overflow-auto rounded-lg border border-line bg-canvas p-2.5 text-[11.5px] leading-relaxed whitespace-pre-wrap text-muted">
                              {renderMasked(current.screen)}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
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
              {working ? (
                <Button
                  type="button"
                  variant="subtle"
                  onClick={stop}
                  aria-label="Stop generating"
                  className="h-[42px] w-[42px] px-0 text-danger hover:bg-danger/10"
                >
                  <Square size={16} className="fill-current" />
                </Button>
              ) : (
                <Button type="submit" variant="primary" disabled={!text.trim() || working || pending} aria-label="Send" className="h-[42px] w-[42px] px-0">
                  <ArrowUp size={18} />
                </Button>
              )}
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
