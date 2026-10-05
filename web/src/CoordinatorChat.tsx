import { useCallback, useEffect, useRef, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Markdown } from "./Markdown";
import { Timestamp } from "./Timestamp";
import { AgentSettingsEditor } from "./AgentSettings";
import { ContentStack, Disclosure } from "./DetailLayout";
import { WorkspaceLink } from "./WorkspaceLink";
import { useFeedScroll } from "./useFeedScroll";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { DiscardChangesDialog } from "./DiscardChangesDialog";
import { Plus, Settings2 } from "lucide-react";

type History = components["schemas"]["CoordinatorHistory"];
type Conversation = components["schemas"]["CoordinatorConversation"];
type Page = components["schemas"]["CoordinatorPage"];
type Turn = components["schemas"]["CoordinatorTurn"];
export type ChatDrafts = Map<string, { id: string; text: string }>;

export function CoordinatorChat({
  projectId,
  refresh,
  drafts,
  onSettingsDirty,
}: {
  projectId: string;
  onSettingsDirty: (dirty: boolean) => void;
  refresh: unknown;
  drafts: ChatDrafts;
}) {
  const base = `projects/${projectId}/coordinator`;
  const [retry, setRetry] = useState(0);
  const history = useResource<History>(base, `${refresh}:${retry}`);
  const [olderHistory, setOlderHistory] = useState<Conversation[]>([]);
  const [before, setBefore] = useState<number | null | undefined>();
  const [loadingOlder, setLoadingOlder] = useState(false);
  const historyItems = [
    ...new Map(
      [...(history.data?.items ?? []), ...olderHistory].map((item) => [
        item.id,
        item,
      ]),
    ).values(),
  ].sort((a, b) => b.number - a.number);
  const cursor = before === undefined ? history.data?.next_before : before;
  const [selection, setSelection] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const dirty = useCallback(
    (value: boolean) => {
      setSettingsDirty(value);
      onSettingsDirty(value);
    },
    [onSettingsDirty],
  );
  function switchConversation(action: () => void) {
    if (settingsDirty) setPending(() => action);
    else action();
  }
  const current = history.data?.items[0]?.id;
  const selected = selection ?? current;
  async function older() {
    if (!cursor || loadingOlder) return;
    setLoadingOlder(true);
    setError("");
    try {
      const page = await request<History>(`${base}?before=${cursor}`);
      setOlderHistory((items) => [...items, ...page.items]);
      setBefore(page.next_before);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setLoadingOlder(false);
    }
  }
  async function create() {
    if (drafts.get(`${projectId}:${current}`)?.text.trim()) {
      setError("Send or clear your draft before starting a new conversation.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const conversation = await request<Conversation>(base, "POST");
      history.invalidate();
      history.setData((previous) => ({
        items: [conversation, ...(previous?.items ?? [])],
        next_before: previous?.next_before ?? null,
      }));
      setSelection(conversation.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <DiscardChangesDialog
        open={!!pending}
        cancel={() => setPending(null)}
        discard={() => {
          pending?.();
          setPending(null);
        }}
      />
      <header className="workspace-pane-header">
        <h2>Coordinator Chat</h2>
        <Button
          variant="ghost"
          size="icon"
          aria-label="New conversation"
          disabled={busy || !history.data}
          onClick={() => switchConversation(() => void create())}
        >
          <Plus size={16} />
        </Button>
      </header>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {history.error && (
        <Alert variant="destructive">
          <AlertDescription>
            {history.error}{" "}
            <Button variant="link" onClick={() => setRetry((n) => n + 1)}>
              Retry chat
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {selected ? (
        <>
          <div className="coordinator-history">
            <NativeSelect
              aria-label="Conversation history"
              value={selected}
              onChange={(event) => {
                const value = event.target.value;
                switchConversation(() => setSelection(value));
              }}
            >
              {historyItems.map((item, index) => (
                <option key={item.id} value={item.id}>
                  {index === 0
                    ? "Current conversation"
                    : `Conversation from ${new Date(item.created_at).toLocaleString()}`}
                </option>
              ))}
            </NativeSelect>
            {cursor && (
              <Button
                size="sm"
                variant="ghost"
                disabled={loadingOlder}
                onClick={() => void older()}
              >
                Older conversations
              </Button>
            )}
          </div>
          <ChatConversation
            key={selected}
            projectId={projectId}
            id={selected}
            current={selected === current}
            refresh={refresh}
            drafts={drafts}
            onSettingsDirty={dirty}
          />
        </>
      ) : (
        <div className="coordinator-empty">
          <h3>Shape your next task</h3>
          <p>Discuss the plan, capture tasks, and follow the work here.</p>
          <Button
            size="sm"
            disabled={busy || !history.data}
            onClick={() => switchConversation(() => void create())}
          >
            Start conversation
          </Button>
          <p className="detail-metadata">
            Choose a model in{" "}
            <WorkspaceLink to={`/projects/${projectId}/edit/coordinator`}>
              Coordinator settings
            </WorkspaceLink>{" "}
            and enable Use Local host in{" "}
            <WorkspaceLink to={`/projects/${projectId}/edit/integration`}>
              Integration settings
            </WorkspaceLink>{" "}
            before sending. Worker delivery can be configured later.
          </p>
        </div>
      )}
    </>
  );
}

function ChatConversation({
  projectId,
  id,
  current,
  refresh,
  drafts,
  onSettingsDirty,
}: {
  projectId: string;
  onSettingsDirty: (dirty: boolean) => void;
  id: string;
  current: boolean;
  refresh: unknown;
  drafts: ChatDrafts;
}) {
  const base = `projects/${projectId}/coordinator`;
  const path = `${base}/${id}`;
  const [tick, setTick] = useState(0);
  const resource = useResource<Page>(path, `${refresh}:${tick}`);
  const [earlier, setEarlier] = useState<Turn[]>([]);
  const [before, setBefore] = useState<number | null | undefined>(undefined);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsOpened, setSettingsOpened] = useState(false);
  const key = `${projectId}:${id}`;
  const [draft, setDraft] = useState(
    () => drafts.get(key) ?? { id: crypto.randomUUID(), text: "" },
  );
  const [pane, setPane] = useState<HTMLDivElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const page = resource.data;
  const active = page?.active;
  const running = !!active && active.status !== "uncertain";
  const cursor = before === undefined ? page?.next_before : before;
  const turns = [
    ...new Map(
      [...earlier, ...(page?.items ?? [])].map((turn) => [turn.id, turn]),
    ).values(),
  ].sort((a, b) => a.number - b.number);
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
    if (!draft.text.trim() || busy || active || !current) return;
    setBusy(true);
    setError("");
    try {
      await request(`${path}/messages`, "POST", draft);
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
      const result = await request<Page>(`${path}?before=${cursor}`);
      setEarlier((previous) => [...result.items, ...previous]);
      setBefore(result.next_before);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingEarlier(false);
    }
  }
  return (
    <>
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
                    <ContentStack>
                      {turn.activity.items
                        .filter((entry) => entry.kind !== "agent")
                        .map((entry) => (
                          <pre className="evidence-output" key={entry.key}>
                            {entry.text}
                          </pre>
                        ))}
                    </ContentStack>
                  </Disclosure>
                )}
                {turn.activity.omitted && (
                  <p className="muted">
                    Earlier output was omitted to keep this turn bounded.
                  </p>
                )}
                {turn.notice && <p role="status">{turn.notice}</p>}
                <Disclosure summary="Turn settings">
                  <p className="detail-metadata">
                    {(turn.applied ?? turn.settings).choice.model} ·{" "}
                    {(turn.applied ?? turn.settings).choice.effort} ·{" "}
                    {turn.applied ? "Read-only" : "Settings requested"}
                  </p>
                </Disclosure>
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="coordinator-composer content-stack">
        {(error || resource.error) && (
          <Alert variant="destructive">
            <AlertDescription>
              {error || resource.error}{" "}
              <Button variant="link" onClick={() => setTick((n) => n + 1)}>
                Refresh chat
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {active?.status === "uncertain" && (
          <Alert>
            <AlertDescription>
              {active.notice}
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
        {current ? (
          <>
            <Textarea
              aria-label="Message coordinator"
              placeholder="Plan the next step…"
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
                disabled={busy || !!active || !page || !draft.text.trim()}
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
                aria-expanded={settingsOpen}
                onClick={() => {
                  setSettingsOpened(true);
                  setSettingsOpen((value) => !value);
                }}
              >
                <Settings2 size={14} />
                Settings
              </Button>
            </div>
            {settingsOpened && (
              <div className="coordinator-settings" hidden={!settingsOpen}>
                <AgentSettingsEditor
                  projectId={projectId}
                  path={`${path}/settings`}
                  refresh={refresh}
                  coordinator
                  conversation
                  onDirty={onSettingsDirty}
                />
              </div>
            )}
            <p className="detail-metadata">
              Read-only files · Planning tools · Recent chat and current project
              state inform each turn.
            </p>
          </>
        ) : (
          <>
            {draft.text && (
              <Textarea
                aria-label="Unsent message from this conversation"
                readOnly
                value={draft.text}
              />
            )}
            <p className="muted">
              This conversation is retained history. Select the current
              conversation to continue.
            </p>
          </>
        )}
      </div>
    </>
  );
}
