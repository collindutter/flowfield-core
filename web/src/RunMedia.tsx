import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { DetailSection, Disclosure } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { useResource } from "./useResource";
import "./media.css";

// Local API types until the generated schema includes media endpoints.
export type Artifact = {
  id: string;
  project_id: string;
  task_id: string;
  run_id: string;
  name: string;
  title: string | null;
  description: string | null;
  mime: string;
  size: number;
  created_at: string;
  href: string;
};
export type BrowserStatus = {
  active: boolean;
  recording: boolean;
  url: string | null;
  problem: string | null;
};

export function ArtifactGallery({
  projectId,
  taskId,
  runId,
  refresh,
  activeRunId,
}: {
  projectId: string;
  taskId?: string;
  runId?: string;
  refresh: unknown;
  activeRunId?: string;
}) {
  const path = `projects/${projectId}/${runId ? `runs/${runId}` : `tasks/${taskId}`}/artifacts`;
  const [retry, setRetry] = useState(0);
  const tick = useMemo(() => ({ refresh, retry }), [refresh, retry]);
  const { data, error, loading } = useResource<Artifact[]>(path, tick, 10000, {
    projectId,
    attemptId: activeRunId,
  });
  return (
    <DetailSection title="Artifacts" aria-label="Artifacts">
      {loading && !data && <p className="muted">Loading artifacts…</p>}
      {error && (
        <div role="alert">
          <p>Could not refresh artifacts. {error}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRetry((value) => value + 1)}
          >
            Retry artifacts
          </Button>
        </div>
      )}
      {data?.length === 0 && <p className="muted">No artifacts saved yet.</p>}
      {!!data?.length && (
        <ul className="artifact-gallery">
          {data.map((artifact) => {
            const title = artifact.title || artifact.name;
            return (
              <li key={artifact.id} className="artifact-card content-stack">
                {artifact.mime.startsWith("image/") && (
                  <a
                    href={artifact.href}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`View ${title}`}
                  >
                    <img src={artifact.href} alt={title} loading="lazy" />
                  </a>
                )}
                {["video/webm", "video/mp4"].includes(artifact.mime) && (
                  <video
                    controls
                    preload="metadata"
                    aria-label={title}
                    src={artifact.href}
                  />
                )}
                <strong>{title}</strong>
                {artifact.description && <p>{artifact.description}</p>}
                <p className="detail-metadata">
                  <Timestamp date={artifact.created_at} /> · {artifact.mime} ·{" "}
                  {artifact.size.toLocaleString()} bytes
                </p>
                <a
                  href={`${artifact.href}?download=true`}
                  download={artifact.name}
                >
                  Download {artifact.name}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </DetailSection>
  );
}

function useVisible() {
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", changed);
    return () => document.removeEventListener("visibilitychange", changed);
  }, []);
  return visible;
}

export function RunLive({
  projectId,
  runId,
  refresh,
}: {
  projectId: string;
  runId: string;
  refresh: unknown;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Disclosure
      summary={<>Live browser · read-only</>}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      {open && (
        <BrowserPreview projectId={projectId} runId={runId} refresh={refresh} />
      )}
    </Disclosure>
  );
}

function BrowserPreview({
  projectId,
  runId,
  refresh,
}: {
  projectId: string;
  runId: string;
  refresh: unknown;
}) {
  const visible = useVisible();
  const [revision, setRevision] = useState(0);
  const tick = useMemo(() => ({ refresh, revision }), [refresh, revision]);
  const path = `projects/${projectId}/runs/${runId}/browser`;
  const status = useResource<BrowserStatus>(
    visible ? path : null,
    tick,
    10000,
    {
      projectId,
      attemptId: runId,
    },
  );
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(
      () => setRevision((value) => value + 1),
      2000,
    );
    return () => window.clearInterval(timer);
  }, [visible]);
  // useResource clears its error at refresh start while retaining old data.
  // A failed connection is not live again until a status read succeeds.
  const [disconnected, setDisconnected] = useState("");
  if (status.error && status.error !== disconnected)
    setDisconnected(status.error);
  if (!status.loading && !status.error && disconnected) setDisconnected("");
  const statusError = status.error || disconnected;
  const connected = visible && !statusError && !!status.data?.active;
  return (
    <div className="browser-preview content-stack">
      <p className="muted">
        View only. The worker controls this browser; no mouse or keyboard input
        is sent.
      </p>
      {!visible ? (
        <p role="status">Preview paused while this tab is hidden.</p>
      ) : statusError ? (
        <p role="status">Browser disconnected. Retrying… {statusError}</p>
      ) : !status.data ? (
        <p role="status">Connecting to browser…</p>
      ) : !status.data.active ? (
        <p role="status">
          Browser inactive. Waiting for the worker to open it…
        </p>
      ) : null}
      {visible && status.data?.problem && (
        <p role="status">{status.data.problem}</p>
      )}
      {visible && status.data && !statusError && (
        <p className="detail-metadata">
          {status.data.url || "No page URL available"} ·{" "}
          {status.data.recording ? "Recording" : "Not recording"}
        </p>
      )}
      {connected && <BrowserFrames key={path} path={path} />}
    </div>
  );
}

function BrowserFrames({ path }: { path: string }) {
  const [frame, setFrame] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let disposed = false;
    let pending: AbortController | null = null;
    let timer: number | undefined;
    let objectUrl: string | null = null;
    const clear = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      setFrame(null);
    };
    async function capture() {
      const controller = new AbortController();
      pending = controller;
      const started = Date.now();
      const deadline = window.setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(`/api/${path}/frame`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok)
          throw new Error(
            response.status === 409
              ? "Frame unavailable."
              : `Frame request failed (${response.status}).`,
          );
        const blob = await response.blob();
        if (disposed) return;
        if (blob.type !== "image/jpeg")
          throw new Error("Invalid browser frame.");
        clear();
        objectUrl = URL.createObjectURL(blob);
        setFrame(objectUrl);
        setProblem("");
      } catch (error) {
        if (disposed) return;
        clear();
        setProblem(
          controller.signal.aborted
            ? "Frame request timed out."
            : (error as Error).message,
        );
      } finally {
        window.clearTimeout(deadline);
        if (!disposed)
          timer = window.setTimeout(
            () => void capture(),
            Math.max(500 - (Date.now() - started), 0),
          );
      }
    }
    void capture();
    return () => {
      disposed = true;
      pending?.abort();
      window.clearTimeout(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return (
    <>
      <p role="status">
        {problem
          ? `Browser disconnected. Retrying… ${problem}`
          : frame
            ? "Live · read-only"
            : "Waiting for a browser frame…"}
      </p>
      {frame && (
        <img
          className="browser-frame"
          src={frame}
          alt="Read-only worker browser preview"
          onError={() => {
            setFrame(null);
            setProblem("Could not display browser frame.");
          }}
        />
      )}
    </>
  );
}
