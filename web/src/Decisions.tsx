import { Alert, AlertDescription } from "@/components/ui/alert";
import { Timestamp } from "./Timestamp";
import { Button } from "@/components/ui/button";
import { usePage } from "./useResource";
import {
  Attribution,
  CloseControl,
  CollectionLayout,
  CollectionRow,
} from "./Presentation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { QuestionLink } from "./QuestionLink";
import { OverlayHeading } from "./EntityOverlay";
import { RelatedActivity } from "./Activity";
import { Markdown, MarkdownField } from "./Markdown";
import { projectHref } from "./navigation";
import { request, type ActivityEntry, type ActivityPage } from "./workspace";

function decisionHref(projectId: string, id?: string) {
  return `${projectHref(projectId)}/decisions${id ? `/${encodeURIComponent(id)}` : ""}`;
}

export function DecisionDetail({
  projectId,
  identity,
  refresh,
  onDirty,
  open,
}: {
  projectId: string;
  identity: string;
  refresh: unknown;
  onDirty: (dirty: boolean) => void;
  open: (path: string) => void;
}) {
  const endpoint = `projects/${encodeURIComponent(projectId)}/activity`;
  const [entry, setEntry] = useState<ActivityEntry | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [withdrawing, setWithdrawing] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [body, setBody] = useState("");
  const [id, setId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const dirty = replacing ? body !== entry?.body : !!body;
  const form = useRef<HTMLFormElement>(null);
  const composing = identity === "new" || replacing || withdrawing;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (composing) form.current?.querySelector("textarea")?.focus();
  }, [composing]);
  useEffect(() => {
    if (identity === "new") return;
    let active = true;
    request<ActivityEntry>(`${endpoint}/${encodeURIComponent(identity)}`)
      .then((result) => {
        if (!active) return;
        if (result.kind !== "decision" || result.task_id !== null)
          throw new Error("Project decision not found.");
        setEntry(result);
        setError("");
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [endpoint, identity, refresh, retry]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const saved = await request<ActivityEntry>(
        withdrawing ? `${endpoint}/${identity}/withdraw` : endpoint,
        "POST",
        withdrawing
          ? { id, reason: body, author: "human" }
          : {
              id,
              kind: "decision",
              task_id: null,
              body,
              author: "human",
              supersedes: replacing ? entry?.id : null,
            },
      );
      if (mounted.current) {
        flushSync(() => {
          setBody("");
          onDirty(false);
          setReplacing(false);
          setWithdrawing(false);
        });
        open(decisionHref(projectId, withdrawing ? identity : saved.id));
      }
    } catch (e) {
      if (mounted.current) setError((e as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section
      className="editor decision-detail content-stack"
      data-space="section"
      aria-label="Decision"
    >
      <OverlayHeading>
        <div className="decision-heading overlay-heading">
          <div className="content-stack" data-space="tight">
            {composing && (
              <h2>
                {withdrawing
                  ? "Withdraw decision"
                  : replacing
                    ? "Replace decision"
                    : "New decision"}
              </h2>
            )}
            {entry && (
              <p className="decision-meta">
                <span>{entry.author}</span>
                <span aria-hidden="true"> · </span>
                <Timestamp date={entry.created_at} />
              </p>
            )}
            {!composing && (entry?.withdrawn_by || entry?.superseded_by) && (
              <span className="decision-state">
                {entry.withdrawn_by ? "Withdrawn" : "Superseded"}
              </span>
            )}
          </div>
          <CloseControl
            label="Close decision"
            close={() => open(decisionHref(projectId))}
          />
        </div>
      </OverlayHeading>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            {error}{" "}
            <Button
              size="sm"
              variant="link"
              className="text-button"
              onClick={() => setRetry((v) => v + 1)}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {!entry && !composing && !error && <p>Loading decision…</p>}
      {entry && (
        <>
          <div className="agreement-content decision-body">
            <Markdown>{entry.body}</Markdown>
          </div>
          {entry.supersedes && (
            <RelatedActivity
              path={endpoint}
              id={entry.supersedes}
              label="Earlier decision"
            />
          )}
          {entry.superseded_by && (
            <RelatedActivity
              path={endpoint}
              id={entry.superseded_by}
              label="Replacement decision"
            />
          )}
        </>
      )}
      {entry?.question_id && (
        <QuestionLink id={entry.question_id}>Open question</QuestionLink>
      )}
      {entry?.withdrawn_by && (
        <RelatedActivity
          path={endpoint}
          id={entry.withdrawn_by}
          label="Withdrawal"
        />
      )}
      {entry && !composing && !entry.superseded_by && !entry.withdrawn_by && (
        <div className="actions">
          <Button
            size="sm"
            variant="outline"
            className="quiet"
            onClick={() => {
              setBody(entry.body);
              setReplacing(true);
            }}
          >
            Replace decision
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="quiet"
            onClick={() => setWithdrawing(true)}
          >
            Withdraw
          </Button>
        </div>
      )}
      {composing && (
        <form
          className="decision-form content-stack"
          data-space="section"
          ref={form}
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {(replacing || withdrawing) &&
            (entry?.superseded_by || entry?.withdrawn_by) && (
              <p className="notice">
                This decision was replaced or withdrawn while you were writing.
                Your draft is preserved.
              </p>
            )}
          <fieldset
            disabled={busy}
            className="content-stack"
            data-space="section"
          >
            <MarkdownField
              label={
                withdrawing ? "Reason for withdrawal" : "Decision and rationale"
              }
              value={body}
              onChange={(value) => {
                setBody(value);
                setId(crypto.randomUUID());
              }}
              previewEnabled={false}
            />
            <div className="actions">
              <Button
                size="sm"
                disabled={
                  !body.trim() ||
                  (replacing && !dirty) ||
                  !!entry?.superseded_by ||
                  !!entry?.withdrawn_by
                }
              >
                {busy
                  ? "Saving…"
                  : withdrawing
                    ? "Withdraw"
                    : replacing
                      ? "Save replacement"
                      : "Save decision"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                type="button"
                className="quiet"
                onClick={() => {
                  if (
                    dirty &&
                    !window.confirm("Discard your unsaved decision?")
                  )
                    return;
                  setBody("");
                  onDirty(false);
                  setReplacing(false);
                  setWithdrawing(false);
                  if (identity === "new") open(decisionHref(projectId));
                }}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}

export function Decisions({
  projectId,
  identity,
  refresh,
  open,
}: {
  projectId: string;
  identity?: string;
  refresh: unknown;
  open: (path: string) => void;
}) {
  const [retry, setRetry] = useState(0);
  const url = `context/projects/${encodeURIComponent(projectId)}/activity?kind=decision&current_only=true&limit=20`;
  const {
    data: page,
    error,
    loadingMore: busy,
    older,
  } = usePage<ActivityPage>(url, `${refresh}:${retry}`);
  return (
    <section className="collection-view" aria-label="Project decisions">
      <CollectionLayout>
        <div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>
                {error}{" "}
                <Button size="sm" onClick={() => setRetry((v) => v + 1)}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}
          {!page ? (
            !error && <p>Loading decisions…</p>
          ) : !page.items.length ? (
            <p className="collection-empty">
              No decisions yet. Your coordinator records agreed choices and
              their rationale here; manual entry is also available.
            </p>
          ) : (
            <ul className="decision-cards">
              {page.items.map((entry) => (
                <li key={entry.id}>
                  <CollectionRow
                    timestamp={entry.created_at}
                    selected={entry.id === identity}
                    href={decisionHref(projectId, entry.id)}
                    open={() => open(decisionHref(projectId, entry.id))}
                  >
                    <div className="decision-excerpt">
                      <Markdown links={false}>{entry.body}</Markdown>
                    </div>
                    <Attribution
                      author={entry.author}
                      date={entry.created_at}
                    />
                  </CollectionRow>
                </li>
              ))}
            </ul>
          )}
          {page?.next_cursor && (
            <Button
              size="sm"
              variant="outline"
              className="quiet"
              disabled={busy}
              onClick={() => void older()}
            >
              Load older
            </Button>
          )}
        </div>
      </CollectionLayout>
    </section>
  );
}
