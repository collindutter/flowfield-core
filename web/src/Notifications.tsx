import { ContentStack, DetailHeading } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { WorkspaceLink } from "./WorkspaceLink";
import { BrowserNoticeSettings } from "./BrowserNotices";

type Notice = {
  key: string;
  title: string;
  message: string;
  href?: string;
  action?: string;
};
type Entry = Notice & { time: string };
const Context = createContext<{
  notify: (notice: Notice, open?: boolean) => void;
  show: () => void;
  count: number;
} | null>(null);
export function useNotifications() {
  const value = useContext(Context);
  if (!value) throw new Error("Notifications provider missing");
  return value;
}
export function NotificationProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Entry[]>([]);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const show = useCallback(() => {
    if (document.activeElement instanceof HTMLElement)
      opener.current = document.activeElement;
    setOpen(true);
  }, []);
  const notify = useCallback(
    (notice: Notice, reveal = true) => {
      setItems((current) =>
        [
          { ...notice, time: new Date().toISOString() },
          ...current.filter((item) => item.key !== notice.key),
        ].slice(0, 30),
      );
      if (reveal) show();
    },
    [show],
  );
  return (
    <Context.Provider value={{ notify, show, count: items.length }}>
      {children}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          aria-describedby={undefined}
          className="notification-drawer w-full sm:max-w-md"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            opener.current?.focus();
          }}
        >
          <SheetHeader>
            <SheetTitle>Notifications</SheetTitle>
          </SheetHeader>
          <div className="notification-items">
            <BrowserNoticeSettings />
            {!items.length && <p className="muted">No notifications.</p>}
            {items.map((item) => (
              <Alert key={item.key}>
                <AlertTitle>
                  <DetailHeading entry titleAs="h3" title={item.title} />
                </AlertTitle>
                <AlertDescription>
                  <ContentStack>
                    <p>{item.message}</p>
                    <span className="detail-metadata">
                      <Timestamp date={item.time} />
                    </span>
                    {item.href && (
                      <Button size="sm" variant="outline" asChild>
                        <WorkspaceLink
                          to={item.href}
                          onClick={() => setOpen(false)}
                        >
                          {item.action ?? "Open"}
                        </WorkspaceLink>
                      </Button>
                    )}
                  </ContentStack>
                </AlertDescription>
              </Alert>
            ))}
            {!!items.length && (
              <Button size="sm" variant="outline" onClick={() => setItems([])}>
                Clear notifications
              </Button>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </Context.Provider>
  );
}
export function NotificationButton() {
  const { show, count } = useNotifications();
  return (
    <Button
      size="sm"
      variant="ghost"
      className="notification-button px-0"
      onClick={show}
    >
      <Bell aria-hidden="true" /> Notifications
      {count ? <span>{count}</span> : null}
    </Button>
  );
}
