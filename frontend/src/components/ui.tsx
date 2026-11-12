import * as Switch from "@radix-ui/react-switch";
import * as Tooltip from "@radix-ui/react-tooltip";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { forwardRef, useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import type { State } from "../lib/store";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export const STATE_COLOR: Record<State, string> = {
  flow: "var(--color-flow)",
  stuck: "var(--color-stuck)",
  paused: "var(--color-pause)",
  waiting: "var(--color-pause)",
  offline: "var(--faint)",
};

// ---- the SAIP mark: a line that loops back on itself once (same shape as the tray icon) ----
const MARK = "M2.5 19.3 C8.5 19.3 13.4 17.4 15 12.4 C16.4 8 14.4 4.6 12 4.6 C9.6 4.6 7.9 7.8 9.2 12 C10.6 16.7 15.5 19.3 21.5 19.3";

export function Mark({ state = "flow", size = 24, draw = false, className }: { state?: State; size?: number; draw?: boolean; className?: string }) {
  const color = STATE_COLOR[state];
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} className={className} aria-hidden>
      <motion.circle
        cx={20.2}
        cy={5.2}
        r={2.6}
        initial={false}
        animate={{ opacity: state === "stuck" ? 1 : 0, scale: state === "stuck" ? 1 : 0 }}
        style={{ fill: color, transformOrigin: "20.2px 5.2px" }}
        transition={{ type: "spring", stiffness: 380, damping: 18 }}
      />
      <motion.path
        d={MARK}
        fill="none"
        strokeWidth={2.3}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={draw ? { pathLength: 0 } : false}
        animate={{ pathLength: 1, stroke: color, opacity: state === "offline" ? 0.5 : 1 }}
        transition={{ pathLength: { duration: 1.4, ease: [0.22, 1, 0.36, 1] }, stroke: { duration: 0.6 } }}
        strokeDasharray={state === "paused" ? "13 3 40" : undefined}
      />
    </svg>
  );
}

// ---- surfaces ----
export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("rounded-2xl border border-line bg-surface", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHead({ title, hint, right }: { title: ReactNode; hint?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pt-4 pb-3">
      <div className="min-w-[200px] flex-1">
        <h2 className="text-[13px] font-medium tracking-wide text-ink">{title}</h2>
        {hint && <p className="mt-0.5 text-[12.5px] leading-snug text-muted">{hint}</p>}
      </div>
      {right}
    </div>
  );
}

// ---- controls ----
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "subtle" | "danger" | "stuck";
  size?: "sm" | "md" | "lg";
  busy?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "subtle", size = "md", busy, className, children, disabled, ...rest },
  ref,
) {
  const sizes = { sm: "h-8 px-3 text-[13px]", md: "h-9 px-4 text-[13.5px]", lg: "h-11 px-5 text-[14.5px]" };
  const variants = {
    primary: "bg-ink text-canvas hover:opacity-90",
    stuck: "bg-stuck text-[#1a1004] hover:brightness-110",
    subtle: "bg-raised text-ink border border-line hover:border-line-strong",
    ghost: "text-muted hover:text-ink hover:bg-raised",
    danger: "bg-transparent text-danger border border-[color-mix(in_oklab,var(--color-danger)_40%,transparent)] hover:bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)]",
  };
  return (
    <button
      ref={ref}
      disabled={disabled || busy}
      className={cx(
        "relative inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium transition-all duration-200 ease-out-quint active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45",
        sizes[size],
        variants[variant],
        className,
      )}
      {...rest}
    >
      {busy && <Spinner className="absolute" />}
      <span className={cx("inline-flex items-center gap-2", busy && "opacity-0")}>{children}</span>
    </button>
  );
});

export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" className={cx("animate-spin", className)} aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".2" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      className={cx(
        "h-10 w-full rounded-xl border border-line bg-canvas px-3.5 text-[14px] text-ink placeholder:text-faint transition-colors focus:border-flow focus:outline-none",
        className,
      )}
      {...rest}
    />
  );
});

export function Field({ label, hint, children, error }: { label: string; hint?: ReactNode; children: ReactNode; error?: string | null }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12.5px] font-medium text-muted">{label}</span>
      {children}
      {error ? <span className="mt-1.5 block text-[12.5px] text-danger">{error}</span> : hint ? <span className="mt-1.5 block text-[12.5px] text-faint">{hint}</span> : null}
    </label>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <Switch.Root
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      aria-label={label}
      className="relative h-6 w-10 shrink-0 cursor-pointer rounded-full border border-line-strong bg-raised transition-colors duration-300 data-[state=checked]:border-flow data-[state=checked]:bg-flow disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Switch.Thumb className="block h-[18px] w-[18px] translate-x-[2px] rounded-full bg-ink shadow-sm transition-transform duration-300 ease-out-quint will-change-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-white" />
    </Switch.Root>
  );
}

export function Pill({ tone = "muted", children, className }: { tone?: "flow" | "stuck" | "muted" | "danger"; children: ReactNode; className?: string }) {
  const tones = {
    flow: "bg-flow-soft text-flow",
    stuck: "bg-stuck-soft text-stuck",
    muted: "bg-raised text-muted border border-line",
    danger: "bg-[color-mix(in_oklab,var(--color-danger)_14%,transparent)] text-danger",
  };
  return <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-medium", tones[tone], className)}>{children}</span>;
}

export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <Tooltip.Root delayDuration={250}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          sideOffset={6}
          className="z-50 max-w-xs rounded-lg border border-line bg-raised px-2.5 py-1.5 text-[12.5px] leading-snug text-ink shadow-xl"
        >
          {content}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

/** A number that eases to each new value. */
export function Count({ value, digits = 2, className }: { value: number; digits?: number; className?: string }) {
  const mv = useMotionValue(value);
  const text = useTransform(mv, (v) => v.toFixed(digits));
  useEffect(() => {
    const c = animate(mv, value, { duration: 0.6, ease: [0.22, 1, 0.36, 1] });
    return () => c.stop();
  }, [value, mv]);
  return <motion.span className={cx("num", className)}>{text}</motion.span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-5 pb-5 text-[13px] leading-relaxed text-muted">{children}</div>;
}

export function StatusDot({ status }: { status: string }) {
  const c = status === "ok" || status === "changed" ? "bg-flow" : status === "failed" ? "bg-danger" : status === "skipped" ? "bg-pause" : "bg-stuck";
  return <span className={cx("inline-block h-2 w-2 shrink-0 rounded-full", c)} />;
}
