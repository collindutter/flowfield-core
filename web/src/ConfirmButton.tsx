import { useRef, useState, type ComponentProps, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

/** Confirm the action selected when opened, preserving its revision-bound closure. */
export function ConfirmButton({
  title,
  description,
  action,
  children,
  ...props
}: Omit<ComponentProps<typeof Button>, "onClick"> & {
  title: string;
  description: ReactNode;
  action: () => void;
}) {
  const [pending, setPending] = useState<{ action: () => void } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <>
      <Button
        {...props}
        ref={trigger}
        type="button"
        onClick={() => setPending({ action })}
      >
        {children}
      </Button>
      <Dialog
        open={!!pending}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
      >
        <DialogContent
          role="alertdialog"
          showCloseButton={false}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            cancel.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            trigger.current?.focus({ preventScroll: true });
          }}
        >
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
          <div className="actions">
            <Button
              ref={cancel}
              type="button"
              variant="outline"
              onClick={() => setPending(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={props.disabled}
              onClick={() => {
                const selected = pending;
                setPending(null);
                selected?.action();
              }}
            >
              {children}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
