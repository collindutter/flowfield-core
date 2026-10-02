import { useEffect, useRef } from "react";
import { request } from "./workspace";
import type { components } from "./api-schema";

export const browserNoticesSupported = () => "Notification" in window;

export function useBrowserNotifications(navigate: (href: string) => void) {
  const navigation = useRef(navigate);
  useEffect(() => {
    navigation.current = navigate;
  }, [navigate]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const controller = new AbortController();
    async function poll() {
      try {
        if (
          browserNoticesSupported() &&
          Notification.permission === "granted"
        ) {
          const items = await request<components["schemas"]["Notification"][]>(
            "notifications/browser/claim",
            "POST",
            {
              deliver: !(
                document.visibilityState === "visible" && document.hasFocus()
              ),
            },
            controller.signal,
          );
          if (stopped) return;
          for (const item of items) {
            const notice = new Notification(item.title, {
              body: item.message,
              tag: item.key,
            });
            notice.onclick = () => {
              window.focus();
              const href = item.actions[0]?.href;
              if (href?.startsWith("/")) navigation.current(href);
              else if (href) window.open(href, "_blank", "noopener,noreferrer");
              notice.close();
            };
          }
        }
      } catch {
        /* The persistent feed remains available if desktop delivery fails. */
      }
      if (!stopped) timer = setTimeout(() => void poll(), 3000);
    }
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
}
