import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { request } from "./workspace";
import { useNotifications } from "./Notifications";
import type { components } from "./api-schema";

const preference = "flowfield-browser-notifications";
const receipts = "flowfield-attention-receipts";
const supported = () => "Notification" in window && !!navigator.locks;
function enabled() {
  try {
    return localStorage.getItem(preference) === "on";
  } catch {
    return false;
  }
}
export function BrowserNoticeSettings() {
  const [on, setOn] = useState(enabled);
  const [error, setError] = useState("");
  async function toggle() {
    try {
      if (on) {
        localStorage.removeItem(preference);
        setOn(false);
      } else {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          setError(
            "Notifications are blocked or not allowed. Change this site's browser permission to enable them.",
          );
          return;
        }
        localStorage.setItem(preference, "on");
        setOn(true);
        setError("");
      }
      window.dispatchEvent(new Event("flowfield-notification-preference"));
    } catch {
      setError("This browser cannot save notification preferences.");
    }
  }
  return (
    <div className="content-stack">
      <Button
        size="sm"
        variant="outline"
        disabled={!supported()}
        onClick={() => void toggle()}
      >
        {on ? "Disable browser notifications" : "Enable browser notifications"}
      </Button>
      {!supported() && <p>Browser notifications are unavailable here.</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
export function useAttentionNotifications(navigate: (href: string) => void) {
  const { notify } = useNotifications();
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
        if (supported() && enabled() && Notification.permission === "granted") {
          const items = await request<
            components["schemas"]["AttentionNotice"][]
          >("attention-notifications", "GET", undefined, controller.signal);
          if (stopped) return;
          await navigator.locks.request("flowfield-attention-notices", () => {
            if (stopped || !enabled()) return;
            const saved = localStorage.getItem(receipts);
            const seen: string[] = saved ? JSON.parse(saved) : [];
            const fresh = items.filter((item) => !seen.includes(item.key));
            // First adoption establishes a baseline, without notifying every existing item.
            localStorage.setItem(
              receipts,
              JSON.stringify(
                [...new Set([...seen, ...items.map((i) => i.key)])].slice(
                  -1000,
                ),
              ),
            );
            if (
              !saved ||
              (document.visibilityState === "visible" && document.hasFocus())
            )
              return;
            for (const item of fresh) {
              const notice = new Notification(item.title, {
                body: item.label,
                tag: item.key,
              });
              notice.onclick = () => {
                window.focus();
                navigation.current(item.href);
                notice.close();
              };
              notify(
                {
                  key: item.key,
                  title: item.title,
                  message: item.label,
                  href: item.href,
                },
                false,
              );
            }
          });
        }
      } catch {
        /* Offline reads preserve receipts and retry without repeated error banners. */
      }
      if (!stopped) timer = setTimeout(() => void poll(), 3000);
    }
    // Polling stays bounded; preference changes take effect by the next poll.
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [notify]);
}
