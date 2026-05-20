import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { Modal } from "./modal";
import { Button } from "./button";

/**
 * 统一的二次确认对话框,替代原生 window.confirm。
 *
 * 用法:
 *   const confirm = useConfirm();
 *   const ok = await confirm({ title: "确认删除?", description: "..." });
 *   if (!ok) return;
 *   del.mutate();
 *
 * 设计要点:
 * - 走原 Modal 的样式与交互(Soft Bento + Esc 关闭 + 点遮罩关闭)
 * - tone="danger" 时主按钮变红,匹配"删除/撤销"等高风险操作
 * - 异步 Promise 接口,代码读起来跟 window.confirm 一样直
 * - 单实例渲染 → 不会出现多个对话框互相挡
 */

export type ConfirmOptions = {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
};

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

type Pending = {
  opts: ConfirmOptions;
  resolve: (v: boolean) => void;
};

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<boolean>((resolve) => {
      setPending({ opts, resolve });
    });
  }, []);

  const close = (v: boolean) => {
    pending?.resolve(v);
    setPending(null);
  };

  const opts = pending?.opts;
  const tone = opts?.tone ?? "default";

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal
        open={!!pending}
        onClose={() => close(false)}
        title={opts?.title ?? ""}
        description={opts?.description}
        width={440}
        footer={
          <>
            <Button variant="secondary" onClick={() => close(false)}>
              {opts?.cancelLabel ?? "取消"}
            </Button>
            <button
              type="button"
              onClick={() => close(true)}
              className={
                tone === "danger"
                  ? "inline-flex items-center justify-center gap-2 rounded-[10px] px-5 py-2.5 text-sm font-medium font-body transition-colors bg-[var(--color-danger)] text-white hover:bg-[#dc2626]"
                  : "inline-flex items-center justify-center gap-2 rounded-[10px] px-5 py-2.5 text-sm font-medium font-body transition-colors bg-[var(--color-accent)] text-white hover:bg-[var(--color-accent-hover)]"
              }
            >
              {opts?.confirmLabel ?? "确认"}
            </button>
          </>
        }
      >
        {/* description 已在 Modal 头部展示;body 留个最小占位以保留 padding 一致性 */}
        <div className="py-2" />
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm 必须在 <ConfirmProvider /> 内调用");
  }
  return ctx;
}
