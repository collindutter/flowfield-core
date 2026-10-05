import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Markdown } from "./Markdown";
import { Timestamp } from "./Timestamp";
import { AgentSettingsEditor } from "./AgentSettings";
import { Disclosure } from "./DetailLayout";
import { ActivityEntries } from "./RunActivity";
import { useFeedScroll } from "./useFeedScroll";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Settings2 } from "lucide-react";
type Page = components["schemas"]["CoordinatorPage"];
type Turn = components["schemas"]["CoordinatorTurn"];
type Choice = components["schemas"]["AgentChoice-Output"];
export type ChatDrafts = Map<string, { id: string; text: string }>;

function mergeTurns(previous: Turn[], updates: Turn[]) {
  return [
    ...new Map(
      [...previous, ...updates].map((turn) => [turn.id, turn]),
    ).values(),
  ].sort((a, b) => a.number - b.number);
}

export function CoordinatorChat({
  projectId,
  refresh,
  drafts,
  onSettingsDirty,
}: {
  projectId: string;
  refresh: unknown;
  drafts: ChatDrafts;
  onSettingsDirty: (dirty: boolean) => void;
}) {
  const base = `projects/${projectId}/coordinator`;
  const [history, setHistory] = useState<{ page: Page | null; items: Turn[] }>({
    page: null,
    items: [],
  });
  const latest = history.items.at(-1)?.number;
  const path = latest ? `${base}?after=${latest}` : base;
  const [tick, setTick] = useState(0);
  const resource = useResource<Page>(path, `${refresh}:${tick}`);
  const [before, setBefore] = useState<number | null | undefined>(undefined);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [choice, setChoice] = useState<Choice | null>(null);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const dirty = useCallback(
    (value: boolean) => {
      setSettingsDirty(value);
      onSettingsDirty(value);
    },
    [onSettingsDirty],
  );
  const key = projectId;
  const [draft, setDraft] = useState(
    () => drafts.get(key) ?? { id: crypto.randomUUID(), text: "" },
  );
  const [pane, setPane] = useState<HTMLDivElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const page = resource.data ?? history.page;
  if (page && history.page !== page) {
    // Preserve already displayed pages as the latest server window moves forward.
    setHistory({ page, items: mergeTurns(history.items, page.items) });
    if (before === undefined) setBefore(page.next_before ?? null);
  }
  const active = page?.active;
  const running = !!active && active.status !== "uncertain";
  const cursor = before;
  const turns = history.items;
  const scroll = useFeedScroll(content, !!page, undefined, true, pane);
  useEffect(() => {
    if (!running) return;
    const interval = window.setInterval(() => setTick((n) => n + 1), 600);
    return () => window.clearInterval(interval);
  }, [running]);
  function update(text: string) {
    const next = {
      id: draft.text === text ? draft.id : crypto.randomUUID(),
      text,
    };
    drafts.set(key, next);
    setDraft(next);
  }
  async function send() {
    if (
      !draft.text.trim() ||
      busy ||
      active ||
      !page ||
      !choice ||
      settingsDirty
    )
      return;
    setBusy(true);
    setError("");
    try {
      await request(`${base}/messages`, "POST", draft);
      update("");
      setTick((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
      setTick((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }
  async function action(turn: Turn, operation: "stop" | "confirm-stopped") {
    if (
      operation === "confirm-stopped" &&
      !window.confirm(
        "Confirm you have stopped any remaining coordinator process on the host. This does not stop it for you.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await request(`${base}/turns/${turn.id}/${operation}`, "POST");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setTick((n) => n + 1);
    }
  }
  async function older() {
    if (!cursor || loadingEarlier) return;
    setLoadingEarlier(true);
    setError("");
    scroll.readingEarlier();
    try {
      const result = await request<Page>(`${base}?before=${cursor}`);
      setHistory((previous) => ({
        ...previous,
        items: mergeTurns(result.items, previous.items),
      }));
      setBefore(result.next_before);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingEarlier(false);
    }
  }
  return (
    <>
      <header className="workspace-pane-header">
        <h2>Coordinator Chat</h2>
      </header>
      <div
        ref={setPane}
        className="coordinator-scroll"
        role="region"
        aria-label="Coordinator conversation"
        tabIndex={0}
      >
        <div ref={content} className="content-stack" data-space="section">
          {cursor && (
            <Button
              size="sm"
              variant="outline"
              disabled={loadingEarlier}
              onClick={() => void older()}
            >
              Load earlier messages
            </Button>
          )}
          {page && !turns.length && (
            <p className="muted">What would you like to work on?</p>
          )}
          {turns.map((turn) => (
            <article
              key={turn.id}
              data-message-id={turn.id}
              className="coordinator-turn"
            >
              <div className="coordinator-message coordinator-human">
                <div className="detail-metadata">
                  You · <Timestamp date={turn.created_at} />
                </div>
                <Markdown>{turn.text}</Markdown>
              </div>
              <div className="coordinator-message">
                <div className="detail-metadata">
                  Coordinator · {turn.status}
                </div>
                {turn.activity.items
                  .filter((entry) => entry.kind === "agent")
                  .map((entry) => (
                    <div key={entry.key}>
                      <Markdown>{entry.text}</Markdown>
                      {entry.omitted && (
                        <p className="muted">Some output was omitted.</p>
                      )}
                    </div>
                  ))}
                {turn.activity.items.some(
                  (entry) => entry.kind !== "agent",
                ) && (
                  <Disclosure summary="Activity">
                    <ActivityEntries
                      items={turn.activity.items.filter(
                        (entry) => entry.kind !== "agent",
                      )}
                    />
                  </Disclosure>
                )}
                {turn.activity.omitted && (
                  <p className="muted">
                    Earlier output was omitted to keep this turn bounded.
                  </p>
                )}
                {turn.notice && <p role="status">{turn.notice}</p>}
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="coordinator-composer content-stack">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {resource.error && (
          <Alert variant="destructive">
            <AlertDescription>
              {resource.error}{" "}
              <Button variant="link" onClick={() => setTick((n) => n + 1)}>
                Retry loading messages
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {active?.status === "uncertain" && (
          <Alert>
            <AlertDescription>
              {active.notice}{" "}
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void action(active, "confirm-stopped")}
              >
                Confirm coordinator stopped
              </Button>
            </AlertDescription>
          </Alert>
        )}
        <div
          className="coordinator-settings"
          hidden={!settingsOpen && !!choice}
        >
          <AgentSettingsEditor
            projectId={projectId}
            path={`projects/${projectId}/coordinator-settings`}
            refresh={refresh}
            coordinator
            onDirty={dirty}
            onReady={setChoice}
          />
        </div>
        <Textarea
          aria-label="Message coordinator"
          placeholder={
            choice ? "Plan the next step…" : "Choose a model to start planning…"
          }
          rows={3}
          maxLength={16000}
          value={draft.text}
          disabled={busy}
          onChange={(event) => update(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.metaKey || event.ctrlKey) &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className="actions">
          <Button
            size="sm"
            disabled={
              busy ||
              !!active ||
              !page ||
              !choice ||
              settingsDirty ||
              !draft.text.trim()
            }
            onClick={() => void send()}
          >
            Send
          </Button>
          {running && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || active.status === "stopping"}
              onClick={() => void action(active, "stop")}
            >
              {active.status === "stopping" ? "Stopping…" : "Stop"}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={settingsOpen || !choice}
            onClick={() => setSettingsOpen((value) => !value)}
          >
            <Settings2 size={14} />
            {choice ? `${choice.model} · ${choice.effort}` : "Model settings"}
          </Button>
        </div>
      </div>
    </>
  );
}
