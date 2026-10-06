import { useEffect, useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { request } from "./workspace";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Popover } from "radix-ui";
import { ChevronDown, Zap } from "lucide-react";
import { ContentStack } from "./DetailLayout";

type Settings = components["schemas"]["AgentSettingsView"];
type Choice = components["schemas"]["AgentChoice-Output"];
type Model = components["schemas"]["ModelOption"];

export function AgentModelFields({
  model,
  effort,
  mode,
  fast,
  models,
  loading,
  change,
}: {
  model: string;
  effort: string;
  mode: string;
  fast: boolean;
  models: Model[];
  loading: boolean;
  change: (model: string, effort: string, mode: string, fast: boolean) => void;
}) {
  const selected = models.find((item) => item.id === model);
  return (
    <>
      <Label className="field block">
        Model
        <NativeSelect
          aria-label="Model"
          value={loading ? "" : model}
          required
          disabled={loading || !models.length}
          onChange={(event) => change(event.target.value, "", mode, false)}
        >
          <option value="">
            {loading
              ? "Loading models…"
              : !models.length
                ? "Models unavailable"
                : "Choose a model"}
          </option>
          {!loading && model && !selected && (
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
          value={loading ? "" : effort}
          required
          disabled={loading || !model || !selected?.efforts.length}
          onChange={(event) => change(model, event.target.value, mode, fast)}
        >
          <option value="">
            {loading
              ? "Loading efforts…"
              : !model
                ? "Select a model first"
                : !selected?.efforts.length
                  ? "Efforts unavailable"
                  : "Choose an effort"}
          </option>
          {!loading && effort && !selected?.efforts.includes(effort) && (
            <option value={effort}>{effort} (unavailable)</option>
          )}
          {selected?.efforts.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </NativeSelect>
      </Label>
      {!!selected?.modes?.length && (
        <Label className="field block">
          Access mode
          <NativeSelect
            aria-label="Access mode"
            value={loading ? "" : mode}
            required
            disabled={loading}
            onChange={(event) =>
              change(model, effort, event.target.value, fast)
            }
          >
            <option value="">
              {loading ? "Loading modes…" : "Choose a mode"}
            </option>
            {!loading &&
              mode &&
              !selected.modes.some((item) => item.id === mode) && (
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
      {(selected?.fast || fast) && (
        <div>
          <Button
            type="button"
            size="sm"
            variant={fast ? "secondary" : "outline"}
            aria-label="Fast mode"
            aria-pressed={fast}
            disabled={loading || !selected?.fast}
            onClick={() => change(model, effort, mode, !fast)}
          >
            <Zap size={14} /> Fast
          </Button>
          <p className="detail-metadata">
            {loading
              ? "Loading fast mode…"
              : selected?.fast
                ? selected.fast_description ||
                  "Faster responses, increased usage."
                : "Fast mode is unavailable. Choose a supported model."}
          </p>
        </div>
      )}
    </>
  );
}

type AgentSettingsProps = {
  projectId: string;
  path: string;
  refresh: unknown;
  onDirty: (value: boolean) => void;
  coordinator?: boolean;
  onReady?: (choice: Choice | null) => void;
  onSaved?: () => void;
  onSaveError?: () => void;
  compact?: boolean;
};

function useAgentSettingsContent({
  path,
  refresh,
  onDirty,
  coordinator = false,
  onReady,
  onSaved,
  onSaveError,
  compact = false,
}: AgentSettingsProps) {
  const resource = useResource<Settings>(path, refresh);
  const [retry, setRetry] = useState(0);
  const catalog = useResource<Model[]>(
    retry ? "worker-models?refresh=true" : "worker-models",
    retry,
    180000,
  );
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
  const fast = choice?.fast ?? false;
  const stale = !!(draft && data && draft.revision !== data.revision);
  useEffect(() => {
    onDirty(!!draft || busy);
    return () => onDirty(false);
  }, [draft, busy, onDirty]);
  useEffect(() => {
    const saved = data?.effective?.choice;
    const available =
      !catalog.data ||
      catalog.data.some(
        (item) =>
          item.id === saved?.model &&
          item.efforts.includes(saved.effort) &&
          !!item.modes?.some((mode) => mode.id === saved.mode) &&
          (!saved.fast || item.fast),
      );
    onReady?.(!resource.error && available ? (saved ?? null) : null);
  }, [data, resource.error, catalog.data, onReady]);
  function change(model: string, effort: string, mode: string, fast: boolean) {
    if (data)
      setDraft({
        revision: draft?.revision ?? data.revision,
        selection: {
          harness: "codex",
          model,
          effort,
          mode: mode || null,
          fast,
        },
      });
  }
  async function save(reset = false, next = selection) {
    if (!data) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await request<Settings>(
        path,
        "PUT",
        {
          expected_revision: draft?.revision ?? data.revision,
          selection: reset ? null : next,
        },
        undefined,
        180000,
      );
      resource.invalidate();
      resource.setData(updated);
      setDraft(null);
      setNotice(reset ? "Using project defaults." : "Settings saved.");
      onSaved?.();
    } catch (error) {
      setError((error as Error).message);
      onSaveError?.();
    } finally {
      setBusy(false);
    }
  }
  const fields = (
    <ContentStack space="section">
      {!compact && (
        <p>
          {coordinator
            ? "Model and effort for your next message."
            : "Changes apply to the next worker attempt."}
        </p>
      )}
      {!coordinator && (
        <p className="detail-metadata">
          {data?.selection ? "Task override" : "Using project defaults"}
        </p>
      )}
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
          data-space={compact ? "content" : "section"}
        >
          <AgentModelFields
            model={model}
            effort={effort}
            mode={mode}
            fast={fast}
            models={catalog.data ?? []}
            loading={catalog.loading || resource.loading}
            change={change}
          />
          {coordinator && (
            <p className="muted">
              Access applies to this project directory. Requested setup changes
              happen here directly.
            </p>
          )}
          <div className="actions">
            <Button
              size="sm"
              disabled={
                !draft ||
                stale ||
                !catalog.data?.some(
                  (item) =>
                    item.id === model &&
                    item.efforts.includes(effort) &&
                    !!item.modes?.some((choice) => choice.id === mode) &&
                    (!fast || item.fast),
                )
              }
            >
              {busy ? "Saving…" : "Save"}
            </Button>
            {!coordinator && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={stale || (!data?.selection && !draft)}
                onClick={() => void save(true)}
              >
                Use defaults
              </Button>
            )}
            {(draft || error || resource.error) && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={async () => {
                  if (!stale && !error && !resource.error) {
                    setDraft(null);
                    return;
                  }
                  if (
                    draft &&
                    !window.confirm(
                      "Discard your edits and load the saved settings?",
                    )
                  )
                    return;
                  try {
                    resource.invalidate();
                    resource.setData(await request<Settings>(path));
                    resource.setError("");
                    setDraft(null);
                    setError("");
                  } catch (error) {
                    setError((error as Error).message);
                  }
                }}
              >
                {stale || error || resource.error ? "Reload" : "Cancel"}
              </Button>
            )}
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
      {notice && <p role="status">{notice}</p>}
    </ContentStack>
  );
  const saved = data?.effective?.choice;
  const selected = catalog.data?.find((item) => item.id === saved?.model);
  const fastControl =
    selected?.fast && saved ? (
      <Button
        type="button"
        size="icon-sm"
        variant={saved.fast ? "secondary" : "ghost"}
        aria-label="Fast mode"
        aria-pressed={saved.fast ?? false}
        title={
          selected.fast_description ||
          "Fast mode: faster responses, increased usage"
        }
        disabled={
          !!draft ||
          busy ||
          catalog.loading ||
          resource.loading ||
          !!resource.error
        }
        onClick={() => void save(false, { ...saved, fast: !saved.fast })}
      >
        <Zap size={14} />
      </Button>
    ) : null;
  return { fields, fastControl };
}

export function AgentSettingsControl({
  open,
  onOpenChange,
  label,
  autoOpened = false,
  ...props
}: AgentSettingsProps & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string;
  autoOpened?: boolean;
}) {
  const { fields, fastControl } = useAgentSettingsContent({
    ...props,
    compact: true,
    onSaved: () => onOpenChange(false),
    onSaveError: () => onOpenChange(true),
  });
  return (
    <>
      <Popover.Root open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="composer-model"
            title={label}
          >
            <span>{label}</span>
            <ChevronDown size={12} />
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            side="top"
            align="start"
            sideOffset={8}
            className="composer-settings"
            onFocusOutside={(event) => {
              // A newly activated workspace tab can receive focus after its popup mounts.
              if (autoOpened) event.preventDefault();
            }}
            aria-label={
              props.coordinator
                ? "Coordinator model settings"
                : "Worker model settings"
            }
          >
            {fields}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {fastControl}
    </>
  );
}
