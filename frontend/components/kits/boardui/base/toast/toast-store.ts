import type { ComponentType, ReactNode } from "react";
import type { NotificationAction, NotificationAvatar, NotificationStatus } from "@/components/kits/boardui/base/notification/notification";

// the store and the `toast` api live apart from the Toaster, so calling toast() never loads the renderer

type IconComponent = ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;

export type ToastId = string | number;

export interface ToastOptions {
  /** Reuse an id to replace a toast that is still showing. */
  id?: ToastId;
  description?: ReactNode;
  status?: NotificationStatus;
  /** Custom leading icon; defaults to the status icon. */
  icon?: IconComponent;
  avatar?: NotificationAvatar;
  /** Small buttons under the message. Pressing one also dismisses the toast. */
  actions?: NotificationAction[];
  /** Milliseconds before it dismisses itself; `Infinity` keeps it until closed. Default from the Toaster (5000). */
  duration?: number;
  /** Show the close button. Default true. */
  dismissible?: boolean;
  /** Called once the toast has left the screen. */
  onDismiss?: () => void;
}

export interface ToastRecord extends ToastOptions {
  id: ToastId;
  title: ReactNode;
  dismissed: boolean;
}


let toasts: ToastRecord[] = [];

export function snapshot(): ToastRecord[] {
  return toasts;
}

// past the limit, the oldest toasts play their exit and make room
export function trim(limit: number) {
  const active = toasts.filter((item) => !item.dismissed);
  if (active.length <= limit) return;
  const overflow = new Set(active.slice(0, active.length - limit).map((item) => item.id));
  toasts = toasts.map((item) => (overflow.has(item.id) ? { ...item, dismissed: true } : item));
  emit();
}
let nextId = 1;
const listeners = new Set<() => void>();

export function emit() {
  for (const listener of listeners) listener();
}

export function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function show(title: ReactNode, options: ToastOptions = {}): ToastId {
  const id = options.id ?? nextId++;
  const record: ToastRecord = { ...options, id, title, dismissed: false };
  const existing = toasts.findIndex((item) => item.id === id);
  toasts = existing >= 0 ? toasts.map((item, index) => (index === existing ? record : item)) : [...toasts, record];
  emit();
  return id;
}

export function remove(id: ToastId) {
  const record = toasts.find((item) => item.id === id);
  toasts = toasts.filter((item) => item.id !== id);
  emit();
  record?.onDismiss?.();
}

/** Show a toast. Returns its id, for `toast.update` and `toast.dismiss`. */
export function toast(title: ReactNode, options?: ToastOptions): ToastId {
  return show(title, options);
}

toast.success = (title: ReactNode, options?: Omit<ToastOptions, "status">) => show(title, { ...options, status: "success" });
toast.error = (title: ReactNode, options?: Omit<ToastOptions, "status">) => show(title, { ...options, status: "error" });
toast.info = (title: ReactNode, options?: Omit<ToastOptions, "status">) => show(title, { ...options, status: "information" });

/** Change a toast that is showing. A new `duration` restarts its countdown. */
toast.update = (id: ToastId, options: ToastOptions & { title?: ReactNode }) => {
  const current = toasts.find((item) => item.id === id && !item.dismissed);
  if (!current) return;
  const { title = current.title, ...rest } = options;
  show(title, { ...current, ...rest, id });
};

/** Dismiss one toast, or every toast when called without an id. */
toast.dismiss = (id?: ToastId) => {
  toasts = toasts.map((item) => (id === undefined || item.id === id ? { ...item, dismissed: true } : item));
  emit();
};
