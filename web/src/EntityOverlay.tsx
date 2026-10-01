import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

const OverlayClose = createContext<(() => void) | null>(null);
export const useOverlayClose = () => useContext(OverlayClose);
const OverlayTitleHost = createContext<HTMLElement | null>(null);
const OverlayBody = createContext<HTMLElement | null>(null);
const OverlayFooterHost = createContext<HTMLElement | null>(null);
export const useOverlayBody = () => useContext(OverlayBody);
export function OverlayFooter({ children }: { children: ReactNode }) {
  const host = useContext(OverlayFooterHost);
  return host ? createPortal(children, host) : null;
}
export function OverlayHeading({ children }: { children: ReactNode }) {
  const host = useContext(OverlayTitleHost);
  return host ? createPortal(children, host) : children;
}

// Radix owns modal behavior. A stable portal host retains the editor's React state
// while a related question temporarily replaces its modal layer.
export function EntityOverlay({
  children,
  title,
  close,
  wide = false,
  suspended = false,
  identity,
}: {
  children: ReactNode;
  title: string;
  close: () => void;
  wide?: boolean;
  suspended?: boolean;
  identity?: string;
}) {
  const [host] = useState(() => {
    const node = document.createElement("div");
    node.style.display = "contents";
    return node;
  });
  const [titleHost] = useState(() => document.createElement("div"));
  const [body, setBody] = useState<HTMLElement | null>(null);
  const [footer, setFooter] = useState<HTMLDivElement | null>(null);
  const attachTitle = useCallback(
    (node: HTMLDivElement | null) => {
      if (node) node.appendChild(titleHost);
    },
    [titleHost],
  );
  const content = useRef<HTMLDivElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const pausedFocus = useRef<HTMLElement | null>(null);
  const scrollPosition = useRef(0);
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      if (node) {
        node.appendChild(host);
        node.scrollTop = scrollPosition.current;
      } else {
        scrollPosition.current = content.current?.scrollTop ?? 0;
        if (host.contains(document.activeElement))
          pausedFocus.current = document.activeElement as HTMLElement;
      }
      content.current = node;
      setBody(node);
    },
    [host],
  );
  useLayoutEffect(() => {
    scrollPosition.current = 0;
    if (content.current) content.current.scrollTop = 0;
  }, [identity]);
  return (
    <OverlayClose.Provider value={close}>
      <OverlayTitleHost.Provider value={titleHost}>
        <OverlayBody.Provider value={body}>
          <OverlayFooterHost.Provider value={footer}>
            {createPortal(children, host)}
          </OverlayFooterHost.Provider>
        </OverlayBody.Provider>
      </OverlayTitleHost.Provider>
      <Dialog
        open={!suspended}
        onOpenChange={(open) => {
          if (!open && !suspended) close();
        }}
      >
        <DialogContent
          showCloseButton={false}
          aria-label={title}
          aria-describedby={undefined}
          className={`entity-overlay duration-0 flex flex-col gap-0 overflow-hidden p-0 max-w-none sm:max-w-none top-8 translate-y-0 h-[calc(100dvh-64px)] ${wide ? "wide w-[min(1080px,calc(100vw-80px))]" : "w-[min(760px,calc(100vw-64px))]"} max-[700px]:top-0 max-[700px]:left-0 max-[700px]:translate-x-0 max-[700px]:w-screen max-[700px]:h-dvh max-[700px]:rounded-none`}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            if (!opener.current)
              opener.current = document.activeElement as HTMLElement;
            (pausedFocus.current?.isConnected
              ? pausedFocus.current
              : content.current
            )?.focus({ preventScroll: true });
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (!suspended && opener.current?.isConnected)
              opener.current.focus({ preventScroll: true });
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogTitle className="sr-only">{title}</DialogTitle>
          <div className="entity-overlay-title" ref={attachTitle} />
          <div className="entity-overlay-body" ref={attach} tabIndex={-1} />
          <div className="entity-overlay-footer" ref={setFooter} />
        </DialogContent>
      </Dialog>
    </OverlayClose.Provider>
  );
}
