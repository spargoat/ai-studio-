
import { useState, useEffect, useCallback } from "react";
import { X, AlertCircle, CheckCircle, Info } from "lucide-react";

interface ToastMessage {
  id: number;
  type: "error" | "success" | "info";
  message: string;
  action?: { label: string; href: string };
  duration?: number;
}

let nextId = 0;
let addToastFn: ((type: ToastMessage["type"], message: string, opts?: { action?: { label: string; href: string }; duration?: number }) => void) | null = null;

/** Show a toast from anywhere — no hook needed. */
export function toast(type: ToastMessage["type"], message: string, opts?: { action?: { label: string; href: string }; duration?: number }) {
  addToastFn?.(type, message, opts);
}

toast.error = (msg: string, opts?: { action?: { label: string; href: string }; duration?: number }) => toast("error", msg, opts);
toast.success = (msg: string, opts?: { action?: { label: string; href: string }; duration?: number }) => toast("success", msg, opts);
toast.info = (msg: string, opts?: { action?: { label: string; href: string }; duration?: number }) => toast("info", msg, opts);

const ICONS = {
  error: AlertCircle,
  success: CheckCircle,
  info: Info,
};

const COLORS = {
  error: "border-red-500/30 bg-red-500/10 text-red-400",
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  info: "border-blue-500/30 bg-blue-500/10 text-blue-400",
};

export default function ToastContainer() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const addToast = useCallback((type: ToastMessage["type"], message: string, opts?: { action?: { label: string; href: string }; duration?: number }) => {
    const id = ++nextId;
    const duration = opts?.duration ?? 5000;
    setToasts((prev) => [...prev, { id, type, message, action: opts?.action, duration }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
  }, []);

  useEffect(() => {
    addToastFn = addToast;
    return () => { addToastFn = null; };
  }, [addToast]);

  const dismiss = (id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-[4.5rem] right-4 z-[9999] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => {
        const Icon = ICONS[t.type];
        return (
          <div
            key={t.id}
            className={`flex items-start gap-2 px-4 py-3 rounded-lg border ${COLORS[t.type]}`}
          >
            <Icon size={16} className="mt-0.5 shrink-0" />
            <div className="flex-1">
              <p className="text-sm">{t.message}</p>
              {t.action && (
                <a
                  href={t.action.href}
                  className="inline-block mt-1.5 text-xs font-medium underline underline-offset-2 hover:opacity-80 transition-opacity"
                >
                  {t.action.label}
                </a>
              )}
            </div>
            <button onClick={() => dismiss(t.id)} className="shrink-0 opacity-60 hover:opacity-100">
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}


