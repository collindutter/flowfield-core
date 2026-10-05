import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ContentStack, Disclosure } from "./DetailLayout";

type Settings = components["schemas"]["AgentSettingsView"];
type Choice = components["schemas"]["AgentChoice-Output"];
type Model = components["schemas"]["ModelOption"];

export function AgentModelFields({
  model,
  effort,
  mode,
  modesEnabled = true,
  models,
  loading,
  change,
}: {
  model: string;
  effort: string;
  mode: string;
  modesEnabled?: boolean;
  models: Model[];
  loading: boolean;
  change: (model: string, effort: string, mode: string) => void;
}) {
  const selected = models.find((item) => item.id === model);
  return (
    <>
      <Label className="field block">
        Model
        <NativeSelect
          aria-label="Model"
          value={model}
          required
          disabled={loading}
          onChange={(event) => change(event.target.value, "", mode)}
        >
          <option value="">Choose a model</option>
          {model && !selected && (
            <option value={model}>{model} (unavailable)</option>
          )}
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
          disabled={loading || !model}
          onChange={(event) => change(model, event.target.value, mode)}
        >
          <option value="">Choose an effort</option>
          {effort && !selected?.efforts.includes(effort) && (
            <option value={effort}>{effort} (unavailable)</option>
          )}
          {selected?.efforts.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </NativeSelect>
      </Label>
      {modesEnabled && !!selected?.modes?.length && (
        <Label className="field block">
          Native access mode
          <NativeSelect
            aria-label="Native access mode"
            value={mode}
            required
            disabled={loading}
            onChange={(event) => change(model, effort, event.target.value)}
          >
            <option value="">Choose a mode</option>
            {mode && !selected.modes.some((item) => item.id === mode) && (
              <option value={mode}>{mode} (unavailable)</option>
            )}
            {selected.modes.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
          <span className="detail-metadata">
            {selected.modes.find((item) => item.id === mode)?.description}
          </span>
        </Label>
      )}
    </>
  );
}

export function AgentSettingsEditor({
  projectId,
  path,
  refresh,
  onDirty,
  coordinator = false,
}: {
  projectId: string;
  path: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
  coordinator?: boolean;
}) {
  const resource = useResource<Settings>(path, refresh);
  const runtime = useResource<components["schemas"]["IntegrationSettings"]>(
    coordinator ? null : `projects/${projectId}/integration`,
    refresh,
  );
  const local = coordinator || runtime.data?.runtime === "local";
  const [retry, setRetry] = useState(0);
  const catalog = useResource<Model[]>("worker-models", retry);
  const [draft, setDraft] = useState<{
    revision: number;
    selection: Choice | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const data = resource.data;
  const selection = draft ? draft.selection : data?.selection;
  const choice = selection ?? data?.effective?.choice;
  const model = choice?.model ?? "";
  const effort = choice?.effort ?? "";
  const mode = choice?.mode ?? "";
  const stale = !!(draft && data && draft.revision !== data.revision);
  useEffect(() => {
    onDirty(!!draft);
    return () => onDirty(false);
  }, [draft, onDirty]);
  function change(model: string, effort: string, mode: string) {
    if (data)
      setDraft({
        revision: draft?.revision ?? data.revision,
        selection: { harness: "codex", model, effort, mode: mode || null },
      });
  }
  async function save(reset = false) {
    if (!data) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await request<Settings>(path, "PUT", {
        expected_revision: draft?.revision ?? data.revision,
        selection: reset ? null : selection,
      });
      resource.invalidate();
      resource.setData(updated);
      setDraft(null);
      setNotice(
        reset
          ? coordinator
            ? "Default cleared."
            : "Using project defaults."
          : "Settings saved.",
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <ContentStack space="section">
      <p>
        {coordinator
          ? "Defaults for built-in Coordinator Chat when it becomes available. These do not change your standalone coding agent."
          : "Changes apply to the next worker attempt, including replies and retries. Running attempts keep their settings."}
      </p>
      {!coordinator && (
        <p className="detail-metadata">
          {data?.selection ? "Task override" : "Using project defaults"}
          {data?.effective
            ? ` · Codex · ${data.effective.choice.model} · ${data.effective.choice.effort}`
            : " · Choose a model in project worker settings first."}
        </p>
      )}
      <p className="detail-metadata">
        Harness: Codex.{" "}
        {coordinator
          ? "Coordinator Chat is not active yet."
          : local
            ? "Native tool decisions never approve code delivery. Replies use read-only access."
            : "Select Local in Integration settings before starting workers or choosing native modes."}
      </p>
      {(catalog.error || (!catalog.loading && !catalog.data?.length)) && (
        <Alert>
          <AlertDescription>
            {catalog.error ||
              "No models are available from this Codex installation."}{" "}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRetry(retry + 1)}
            >
              Reload models
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <fieldset
          disabled={busy || !data}
          className="content-stack"
          data-space="section"
        >
          <AgentModelFields
            model={model}
            effort={effort}
            mode={mode}
            modesEnabled={local}
            models={catalog.data ?? []}
            loading={catalog.loading}
            change={change}
          />
          <div className="actions">
            <Button
              size="sm"
              disabled={
                !draft ||
                stale ||
                !catalog.data?.some(
                  (item) => item.id === model && item.efforts.includes(effort),
                )
              }
            >
              Save agent settings
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={stale || (!data?.selection && !draft)}
              onClick={() => void save(true)}
            >
              {coordinator ? "Clear default" : "Use project defaults"}
            </Button>
          </div>
        </fieldset>
      </form>
      {(error || resource.error || stale) && (
        <Alert variant="destructive">
          <AlertDescription>
            {error ||
              resource.error ||
              "Settings changed elsewhere. Load the latest settings before saving."}
          </AlertDescription>
        </Alert>
      )}
      {(draft || error || resource.error) && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={async () => {
            if (
              draft &&
              !window.confirm("Discard your edits and load the saved settings?")
            )
              return;
            try {
              resource.invalidate();
              resource.setData(await request<Settings>(path));
              setDraft(null);
              setError("");
            } catch (error) {
              setError((error as Error).message);
            }
          }}
        >
          Load latest settings
        </Button>
      )}
      {notice && <p role="status">{notice}</p>}
    </ContentStack>
  );
}

export function TaskAgentSettings({
  projectId,
  taskId,
  refresh,
  onDirty,
}: {
  projectId: string;
  taskId: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
}) {
  const [opened, setOpened] = useState(false);
  return (
    <Disclosure
      summary="Worker settings"
      onToggle={(event) => {
        if (event.currentTarget.open) setOpened(true);
      }}
    >
      {opened && (
        <AgentSettingsEditor
          projectId={projectId}
          path={`projects/${projectId}/tasks/${taskId}/agent-settings`}
          refresh={refresh}
          onDirty={onDirty}
        />
      )}
    </Disclosure>
  );
}
