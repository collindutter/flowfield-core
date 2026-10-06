import { createContext, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import { useResource } from "./useResource";
import { request } from "./workspace";
import type { components } from "./api-schema";
import { ConfirmButton } from "./ConfirmButton";

// Keep result controllers mounted with their exact version/drafts while presenting
// their actions in the task footer. Historical results never publish actions here.
export const TaskActionHost = createContext<{
  actions: HTMLElement | null;
  context: HTMLElement | null;
}>({ actions: null, context: null });
export function TaskActionContext({ children }: { children: ReactNode }) {
  const { context } = useContext(TaskActionHost);
  return context ? createPortal(children, context) : null;
}
export function ResultActions({
  version,
  children,
  context,
}: {
  version: number;
  children?: ReactNode;
  context?: ReactNode;
}) {
  const { actions: host } = useContext(TaskActionHost);
  return (
    <>
      <TaskActionContext>{context}</TaskActionContext>
      {host && children
        ? createPortal(
            <div
              className="result-actions"
              role="group"
              aria-label={`Actions for Result ${version}`}
            >
              {children}
            </div>,
            host,
          )
        : null}
    </>
  );
}

type Run = components["schemas"]["Run"];
export function WorkerActions({
  projectId,
  runId,
  refresh,
  changed,
}: {
  projectId: string;
  runId: string | null;
  refresh: unknown;
  changed: () => void;
}) {
  const resource = useResource<Run>(
    runId ? `projects/${projectId}/runs/${runId}` : null,
    refresh,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Run | null>(null);
  const run =
    saved?.id === resource.data?.id &&
    saved &&
    saved.revision > resource.data!.revision
      ? saved
      : resource.data;
  async function act(action: string) {
    if (!run) return;
    setBusy(true);
    setError("");
    try {
      setSaved(
        await request<Run>(
          `projects/${projectId}/runs/${run.id}/${action}`,
          "POST",
          { expected_revision: run.revision, author: "human" },
        ),
      );
      resource.invalidate();
      changed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const active = run && ["preparing", "running"].includes(run.status);
  return (
    <>
      <TaskActionContext>
        {(error || resource.error) && (
          <p role="alert">{error || resource.error}</p>
        )}
        {run?.purpose === "discussion" &&
          ["stopped", "failed"].includes(run.status) && (
            <p>Continue this discussion with the coordinator.</p>
          )}
        {run?.status === "stopping" && (
          <p role="status">Stopping worker. Unfinished work is preserved.</p>
        )}
      </TaskActionContext>
      {run && (active || run.status === "uncertain") && (
        <ConfirmButton
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          title={
            run.status === "uncertain"
              ? "Reconcile the worker process?"
              : "Stop this worker?"
          }
          description="Unfinished work is preserved. The project queue remains unchanged."
          action={() => void act("stop")}
        >
          {busy
            ? "Stopping worker…"
            : run.status === "uncertain"
              ? "Reconcile stopped process"
              : "Stop worker"}
        </ConfirmButton>
      )}
      {run &&
        run.purpose !== "discussion" &&
        ["stopped", "failed"].includes(run.status) && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void act("retry")}
          >
            Retry worker
          </Button>
        )}
    </>
  );
}
