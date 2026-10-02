import { useNotifications } from "./Notifications";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import { WorkspaceLink as Link } from "./WorkspaceLink";
import type { components } from "./api-schema";
import { request } from "./workspace";
import { useResource } from "./useResource";
import { projectHref } from "./navigation";

type Settings = components["schemas"]["WorkerSettings"];
type Model = components["schemas"]["ModelOption"];

export function QueueControls({
  projectId,
  refresh,
}: {
  projectId: string;
  refresh: unknown;
}) {
  const { notify } = useNotifications();
  const path = `projects/${projectId}`;
  const resource = useResource<Settings>(`${path}/workers`, refresh);
  const occupancy = useResource<components["schemas"]["WorkerOccupancy"]>(
    `${path}/workers/occupancy`,
    refresh,
  );
  const [busy, setBusy] = useState(false);
  const data = resource.data;
  const problem = data?.problem || resource.error || occupancy.error;
  useEffect(() => {
    if (problem)
      notify(
        {
          key: `queue-status:${projectId}`,
          project_id: projectId,
          title: "Worker queue needs attention",
          message: problem,
          href: projectHref(projectId) + "/edit/workers",
          action: "Worker settings",
        },
        false,
      );
  }, [problem, projectId, notify]);
  return (
    <div className="queue-controls" aria-label="Worker queue">
      {data && (
        <>
          <span className="muted">
            Queue {data.enabled ? "enabled" : "paused"}
            {occupancy.data && ` · ${occupancy.data.active} active`}
            {!!occupancy.data?.uncertain &&
              ` · ${occupancy.data.uncertain} uncertain`}
          </span>
          <div className="queue-actions">
            <Button
              size="xs"
              variant="outline"
              className="quiet"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const value = await request<Settings>(
                    `${path}/queue`,
                    "POST",
                    {
                      expected_revision: data.revision,
                      enabled: !data.enabled,
                    },
                  );
                  resource.invalidate();
                  resource.setData(value);
                } catch (e) {
                  notify({
                    key: `queue-action:${projectId}`,
                    project_id: projectId,
                    title: data.enabled
                      ? "Queue could not pause"
                      : "Queue could not start",
                    message: (e as Error).message,
                    href: projectHref(projectId) + "/edit/workers",
                    action: "Worker settings",
                  });
                } finally {
                  setBusy(false);
                }
              }}
            >
              {data.enabled ? "Pause queue" : "Run queue"}
            </Button>
            {!data.model && (
              <Button size="xs" variant="outline" asChild>
                <Link to={projectHref(projectId) + "/edit/workers"}>
                  Choose model
                </Link>
              </Button>
            )}
          </div>
        </>
      )}
      {problem && (
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            notify({
              key: `queue-status:${projectId}`,
              project_id: projectId,
              title: "Worker queue needs attention",
              message: problem,
              href: projectHref(projectId) + "/edit/workers",
              action: "Worker settings",
            })
          }
        >
          Queue needs attention
        </Button>
      )}
    </div>
  );
}

export function WorkerSettings({
  projectId,
  onDirty,
}: {
  projectId: string;
  onDirty: (value: boolean) => void;
}) {
  const path = `projects/${projectId}/workers`;
  const resource = useResource<Settings>(path, projectId);
  const [modelRetry, setModelRetry] = useState(0);
  const catalog = useResource<Model[]>("worker-models", modelRetry);
  const models = catalog.data ?? [];
  const [draft, setDraft] = useState<{
    model: string;
    effort: string;
    cap: number;
  } | null>(null);
  const model = draft?.model ?? resource.data?.model ?? "";
  const effort = draft?.effort ?? resource.data?.effort ?? "";
  const cap = draft?.cap ?? resource.data?.max_parallel ?? 1;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty =
    !!resource.data &&
    (model !== (resource.data.model ?? "") ||
      effort !== (resource.data.effort ?? "") ||
      cap !== resource.data.max_parallel);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  const selected = models.find((item) => item.id === model);
  return (
    <section
      className="worker-settings content-stack"
      data-space="section"
      aria-label="Worker settings"
    >
      <p>
        Choose the model for task workers. The coordinating conversation uses
        its own model. Workers can download and install project dependencies in
        their separate workspaces.
      </p>
      {catalog.loading && <p role="status">Loading available models…</p>}
      {!catalog.loading && (catalog.error || !models.length) && (
        <Alert variant={catalog.error ? "destructive" : "default"}>
          <AlertDescription>
            {catalog.error ||
              "No models are available from this Codex installation."}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setModelRetry((value) => value + 1)}
            >
              Reload models
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!resource.data) return;
          setBusy(true);
          setError("");
          try {
            const updated = await request<Settings>(path, "PUT", {
              expected_revision: resource.data.revision,
              model,
              effort,
              max_parallel: cap,
            });
            resource.invalidate();
            resource.setData(updated);
            setDraft(null);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset
          disabled={busy}
          className="content-stack"
          data-space="section"
        >
          <Label className="field block">
            Model
            <NativeSelect
              aria-label="Model"
              value={model}
              required
              disabled={catalog.loading}
              onChange={(event) => {
                setDraft({ model: event.target.value, effort: "", cap });
              }}
            >
              <option value="">Choose a model</option>
              {model && !selected && <option value={model}>{model}</option>}
              {models.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </NativeSelect>
          </Label>
          <Label className="field block">
            Reasoning effort
            <NativeSelect
              aria-label="Reasoning effort"
              value={effort}
              required
              disabled={catalog.loading || !model}
              onChange={(event) =>
                setDraft({ model, effort: event.target.value, cap })
              }
            >
              <option value="">Choose an effort</option>
              {effort && !selected?.efforts.includes(effort) && (
                <option value={effort}>{effort}</option>
              )}
              {selected?.efforts.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </NativeSelect>
          </Label>
          <Label className="field block">
            Maximum parallel workers
            <Input
              type="number"
              min={1}
              max={16}
              value={cap}
              onChange={(event) =>
                setDraft({ model, effort, cap: Number(event.target.value) })
              }
            />
          </Label>
          <div className="actions editor-actions">
            <Button size="sm" disabled={!dirty || !model || !effort}>
              Save worker settings
            </Button>
          </div>
        </fieldset>
      </form>
      {(error || resource.error) && (
        <>
          <Alert variant="destructive">
            <AlertDescription>{error || resource.error}</AlertDescription>
          </Alert>
          <Button
            size="sm"
            variant="outline"
            className="quiet"
            disabled={busy}
            onClick={async () => {
              if (
                dirty &&
                !window.confirm(
                  "Discard your worker setting edits and load the saved settings?",
                )
              )
                return;
              try {
                resource.invalidate();
                resource.setData(await request<Settings>(path));
                setDraft(null);
                setError("");
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Load latest settings
          </Button>
        </>
      )}
    </section>
  );
}
