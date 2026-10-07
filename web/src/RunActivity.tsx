import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { request, label } from "./workspace";
import type { components } from "./api-schema";
import { Disclosure } from "./DetailLayout";
import { UsageSummary } from "./UsageSummary";
import { ContextRing } from "./ContextRing";
import { MessageSquare, Wrench, Terminal, AlignLeft, Info } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type Page = components["schemas"]["RunActivityPage"];
const activityIcons = {
  agent: MessageSquare,
  tool: Wrench,
  command: Terminal,
  output: AlignLeft,
  status: Info,
};
export function RunActivity({
  projectId,
  runId,
  secondary = false,
}: {
  projectId: string;
  runId: string;
  secondary?: boolean;
}) {
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState("");
  const pane = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    let alive = true,
      revision = -1;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const value = await request<Page>(
          `projects/${projectId}/runs/${runId}/activity?after=${revision}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!alive) return;
        revision = value.revision;
        setPage((old) => ({
          ...value,
          items: value.changed ? value.items : (old?.items ?? []),
        }));
        setError("");
        if (!value.active) return;
      } catch (e) {
        if (!alive) return;
        setError((e as Error).message);
      }
      timer = setTimeout(() => void poll(), 1000);
    }
    void poll();
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [projectId, runId]);
  useLayoutEffect(() => {
    if (follow.current && pane.current)
      pane.current.scrollTop = pane.current.scrollHeight;
  }, [page]);
  return (
    <div className="run-activity content-stack" data-space="tight">
      {page?.context && (
        <div className="activity-context">
          <ContextRing context={page.context} active={page.active} />
          <span className="detail-metadata">Context</span>
        </div>
      )}
      {error && (
        <p role="alert">
          Activity disconnected. Retrying; saved output is kept. {error}
        </p>
      )}
      {!page && !error && <p className="muted">Loading activity…</p>}
      {page && !page.supported && (
        <p className="muted">
          {page.active
            ? "Waiting for public worker activity…"
            : "This attempt has no recorded activity feed."}
        </p>
      )}
      {page?.supported && (
        <ActivityContainer
          secondary={secondary}
          onReveal={() => {
            requestAnimationFrame(() => {
              if (follow.current && pane.current)
                pane.current.scrollTop = pane.current.scrollHeight;
            });
          }}
        >
          <div
            ref={pane}
            className="run-activity-output"
            role="region"
            aria-label="Worker activity"
            tabIndex={0}
            onScroll={() => {
              const element = pane.current!;
              follow.current =
                element.scrollHeight -
                  element.scrollTop -
                  element.clientHeight <
                24;
            }}
          >
            {page.omitted && (
              <p className="muted">Earlier activity was omitted.</p>
            )}
            <ActivityEntries items={page.items} />{" "}
          </div>
        </ActivityContainer>
      )}
      {page && <UsageSummary usage={page.usage} active={page.active} />}
    </div>
  );
}

function ActivityContainer({
  secondary,
  children,
  onReveal,
}: {
  secondary: boolean;
  children: React.ReactNode;
  onReveal: () => void;
}) {
  return secondary ? (
    <Disclosure
      summary={<>Worker activity</>}
      onToggle={(event) => {
        if (event.currentTarget.open) onReveal();
      }}
    >
      {children}
    </Disclosure>
  ) : (
    <>{children}</>
  );
}

export function ActivityEntries({ items }: { items: Page["items"] }) {
  return (
    <>
      {items.map((item) => {
        const Icon = activityIcons[item.kind];
        return (
          <div
            key={item.key}
            className="run-activity-entry"
            data-kind={item.kind}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  tabIndex={0}
                  className="run-activity-icon"
                  aria-label={label(item.kind)}
                >
                  <Icon size={14} aria-hidden="true" />
                </span>
              </TooltipTrigger>
              <TooltipContent>{label(item.kind)}</TooltipContent>
            </Tooltip>
            <div>
              {item.omitted && <p className="muted">Some output omitted.</p>}
              <pre>{item.preview || item.text}</pre>
              {item.abridged && (
                <Disclosure summary={<>Retained output</>}>
                  <pre>{item.text}</pre>
                </Disclosure>
              )}
            </div>
          </div>
        );
      })}
    </>
  );
}
