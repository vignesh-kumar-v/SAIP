// Pieces of the Chat page: the conversation items, the action card and the rewind confirm.
import * as Dialog from "@radix-ui/react-dialog";
import { Activity, ClipboardCopy, FilePenLine, FileText, GitBranch, Globe, MonitorSmartphone, Pencil, Undo2, Wrench } from "lucide-react";
import { useState } from "react";
import type { ChatAction, ChatItem, ChatView, RewindPreview } from "../lib/types";
import { Button, Pill, Tip, cx } from "./ui";

/** Older servers send only `messages` and no tools: build items from those. */
export function itemsOf(chat: ChatView): ChatItem[] {
  if (chat.items) return chat.items;
  return chat.messages.map((m, k) => ({ id: k + 1, kind: m.role, at: m.at, text: m.text }));
}

export function toolsSummary(tools: string[]): string {
  const has = (n: string) => tools.includes(n);
  const parts: string[] = [];
  if (has("read_file") || tools.some((t) => t.startsWith("git_"))) parts.push("read files and git");
  if (has("read_screen")) parts.push("read your screen");
  if (has("web_search")) parts.push("search the web");
  if (has("write_file")) parts.push("propose file writes");
  if (has("copy_to_clipboard")) parts.push("propose clipboard copies");
  if (!parts.length) return "";
  const last = parts.pop()!;
  return "can " + (parts.length ? `${parts.join(", ")} and ${last}` : last);
}

function toolIcon(name: string) {
  if (name === "read_file") return FileText;
  if (name === "web_search" || name === "fetch_page") return Globe;
  if (name.startsWith("git_")) return GitBranch;
  if (name === "read_screen") return MonitorSmartphone;
  if (name === "activity_summary") return Activity;
  return Wrench;
}

export function ToolLine({ item }: { item: ChatItem }) {
  const t = item.tool;
  const Icon = toolIcon(t?.name ?? "");
  const ok = t?.ok !== false;
  return (
    <div className={cx("flex items-center justify-center gap-1.5 px-4 text-[12px]", ok ? "text-faint" : "text-stuck")}>
      <Icon size={13} className="shrink-0" />
      <span className="min-w-0 truncate">{t?.label || item.text}</span>
    </div>
  );
}

// ---- the action card ----
function DiffText({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, k) => {
        const tone = line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "text-faint" : line.startsWith("+") ? "text-flow" : line.startsWith("-") ? "text-stuck" : "";
        return (
          <span key={k} className={cx("block", tone)}>
            {line || " "}
          </span>
        );
      })}
    </>
  );
}

const STATE_PILL: Record<ChatAction["state"], { tone: "flow" | "stuck" | "muted" | "danger"; label: string }> = {
  pending: { tone: "stuck", label: "Waiting for you" },
  approved: { tone: "flow", label: "Done" },
  declined: { tone: "muted", label: "Declined" },
  undone: { tone: "muted", label: "Undone" },
  failed: { tone: "danger", label: "Failed" },
};

export function ActionCard({ item, busy, locked, onDecide }: { item: ChatItem; busy: boolean; locked: boolean; onDecide: (approve: boolean) => void }) {
  const a = item.action;
  const [view, setView] = useState<"changes" | "full">("changes");
  if (!a) return null;
  const write = a.name === "write_file";
  const Icon = write ? FilePenLine : ClipboardCopy;
  const pill = STATE_PILL[a.state];
  const showDiff = write && a.diff != null && view === "changes";
  return (
    <div className="mx-auto w-full max-w-[min(640px,92%)] overflow-hidden rounded-2xl border border-line bg-raised">
      <div className="flex items-center gap-2.5 px-4 py-3">
        <Icon size={16} className="shrink-0 text-muted" />
        <div className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-ink">{a.title}</div>
        <Pill tone={pill.tone}>{pill.label}</Pill>
      </div>
      <div className="border-t border-line px-4 py-3">
        <div className="mb-2 flex items-center gap-3">
          {write && a.path && <span className="num min-w-0 flex-1 truncate text-[12px] text-muted">{a.path}{!a.exists && <span className="text-faint"> · new file</span>}</span>}
          {!write && <span className="flex-1 text-[12px] text-muted">Text to copy</span>}
          {write && a.diff != null && (
            <div className="flex shrink-0 gap-0.5 rounded-lg border border-line bg-canvas p-0.5">
              {(["changes", "full"] as const).map((v) => (
                <button key={v} onClick={() => setView(v)} className={cx("rounded-md px-2 py-0.5 text-[11.5px] transition-colors", view === v ? "bg-raised text-ink" : "text-faint hover:text-ink")}>
                  {v === "changes" ? "Changes" : "Full file"}
                </button>
              ))}
            </div>
          )}
        </div>
        <pre className="num scroll-thin max-h-[320px] overflow-auto rounded-xl border border-line bg-canvas px-3 py-2.5 text-[12px] leading-relaxed whitespace-pre text-ink">
          {showDiff ? <DiffText text={a.diff!} /> : a.content}
        </pre>
      </div>
      <div className="border-t border-line px-4 py-2.5">
        {a.state === "pending" ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" busy={busy} disabled={locked} onClick={() => onDecide(true)}>
              {write ? "Approve: write this file" : "Approve: copy to clipboard"}
            </Button>
            <Button variant="ghost" size="sm" disabled={busy || locked} onClick={() => onDecide(false)}>
              Don't
            </Button>
          </div>
        ) : (
          <p className={cx("text-[12.5px]", a.state === "failed" ? "text-danger" : "text-muted")}>
            {a.state === "approved" ? a.detail || "Done." : a.state === "declined" ? "Not done." : a.state === "undone" ? "Undone by a rewind." : a.detail || "That did not work."}
          </p>
        )}
      </div>
    </div>
  );
}

// ---- a message from the person, with edit and rewind ----
export function UserBubble({
  item,
  canChange,
  editing,
  disabled,
  onEdit,
  onCancel,
  onSend,
  onRewind,
}: {
  item: ChatItem;
  canChange: boolean;
  editing: boolean;
  disabled: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSend: (text: string) => void;
  onRewind: () => void;
}) {
  const [draft, setDraft] = useState(item.text);
  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="w-full max-w-[min(640px,88%)] rounded-2xl border border-line-strong bg-raised p-2.5">
          <textarea
            autoFocus
            value={draft}
            rows={Math.min(8, Math.max(2, draft.split("\n").length))}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onCancel();
              if (e.key === "Enter" && !e.shiftKey && draft.trim()) {
                e.preventDefault();
                onSend(draft);
              }
            }}
            className="w-full resize-none rounded-xl border border-line bg-canvas px-3 py-2 text-[14.5px] leading-relaxed text-ink focus:border-flow focus:outline-none"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" disabled={!draft.trim() || disabled} onClick={() => onSend(draft)}>
              Send
            </Button>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="group flex items-center justify-end gap-1.5">
      {canChange && (
        <div className="flex gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 pointer-coarse:opacity-100 max-sm:opacity-100">
          <Tip content="Edit this message and send it again">
            <button disabled={disabled} onClick={() => (setDraft(item.text), onEdit())} aria-label="Edit message" className="rounded-lg p-1.5 text-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40">
              <Pencil size={14} />
            </button>
          </Tip>
          <Tip content="Rewind the chat to this message">
            <button disabled={disabled} onClick={onRewind} aria-label="Rewind to this message" className="rounded-lg p-1.5 text-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40">
              <Undo2 size={14} />
            </button>
          </Tip>
        </div>
      )}
      <div className="max-w-[min(640px,88%)] rounded-2xl bg-ink px-4 py-2.5 text-[14.5px] leading-relaxed whitespace-pre-wrap text-canvas">{item.text}</div>
    </div>
  );
}

// ---- confirm ----
export function ConfirmRewind({
  preview,
  edit,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: RewindPreview | null;
  edit: boolean;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const n = preview ? Math.max(0, preview.drops - 1) : 0;
  return (
    <Dialog.Root open={preview !== null} onOpenChange={(o) => !o && !busy && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(440px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-line bg-raised p-5 shadow-2xl focus:outline-none">
          <Dialog.Title className="text-[16px] font-semibold tracking-tight text-ink">{edit ? "Send the edited message?" : "Rewind to this message?"}</Dialog.Title>
          <Dialog.Description asChild>
            <div className="mt-2.5 space-y-3 text-[13.5px] leading-relaxed text-muted">
              <p>
                This removes {n} later {n === 1 ? "message" : "messages"} from this chat.
              </p>
              {preview && preview.undo.length > 0 && (
                <div>
                  <p>These files will be put back as they were:</p>
                  <ul className="mt-1.5 space-y-1">
                    {preview.undo.map((u) => (
                      <li key={u.path} className="flex items-baseline gap-2 text-[12.5px]">
                        <span className="num min-w-0 flex-1 truncate text-ink">{u.path}</span>
                        <span className="shrink-0 text-faint">{u.effect === "delete" ? "deleted" : "restored"}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {preview && preview.cannot_undo.length > 0 && (
                <div>
                  <p>These cannot be undone:</p>
                  <ul className="mt-1.5 list-inside list-disc space-y-1 text-[12.5px] text-stuck">
                    {preview.cannot_undo.map((c) => (
                      <li key={c}>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </Dialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" busy={busy} onClick={onConfirm}>
              {edit ? "Send" : "Rewind"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
