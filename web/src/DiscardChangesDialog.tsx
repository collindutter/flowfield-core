import { useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

/** Route blocking owns the decision; Escape cannot dismiss a newly opened prompt. */
export function DiscardChangesDialog({
  open,
  cancel,
  discard,
}: {
  open: boolean;
  cancel: () => void;
  discard: () => void;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) cancel();
      }}
    >
      <DialogContent
        role="alertdialog"
        showCloseButton={false}
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          previousFocus.current = document.activeElement as HTMLElement;
          keep.current?.focus();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          if (previousFocus.current?.isConnected)
            previousFocus.current.focus({ preventScroll: true });
        }}
      >
        <DialogTitle>Discard your unsaved edits?</DialogTitle>
        <DialogDescription>Your changes have not been saved.</DialogDescription>
        <div className="actions">
          <Button ref={keep} variant="outline" onClick={cancel}>
            Keep editing
          </Button>
          <Button onClick={discard}>Discard changes</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
