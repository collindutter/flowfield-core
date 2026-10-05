import { ContentStack, DetailSection } from "./DetailLayout";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useParams, useLocation, useNavigate } from "react-router";
import { projectHref } from "./navigation";
import { DetailHeader, DetailTabs } from "./Presentation";
import { Markdown, MarkdownField } from "./Markdown";
import { EditorFeedback, useRecordEditor } from "./useRecordEditor";
import type { Project } from "./workspace";
import { useId, useState } from "react";
import { WorkerSettings } from "./Workers";
import { AgentSettingsEditor } from "./AgentSettings";
import { Disclosure } from "./DetailLayout";
import { ProjectGuidance } from "./ProjectGuidance";
import { IntegrationSettings } from "./Integration";

const fields = (record?: Project) => ({
  name: record?.name ?? "",
  description: record?.description ?? "",
  task_prefix: record?.task_prefix ?? "",
});
export function ProjectEditor({
  incoming,
  path,
  hasTasks,
  onDirty,
  saved,
  close,
}: {
  incoming: Project;
  path: string;
  hasTasks: boolean;
  onDirty: (value: boolean) => void;
  saved: (record: Project) => void;
  close: () => void;
}) {
  const id = useId();
  const { projectTab } = useParams();
  const tab =
    projectTab === "workers" ||
    projectTab === "integration" ||
    projectTab === "coordinator"
      ? projectTab
      : "info";
  const navigate = useNavigate();
  const location = useLocation();
  const [workerDirty, setWorkerDirty] = useState(false);
  const [coordinatorDirty, setCoordinatorDirty] = useState(false);
  const [coordinatorOpened, setCoordinatorOpened] = useState(false);
  const [integrationDirty, setIntegrationDirty] = useState(false);
  const state = useRecordEditor({
    incoming,
    fields,
    path: () => path,
    onDirty,
    saved,
    otherDirty: workerDirty || integrationDirty || coordinatorDirty,
  });
  const {
    values,
    loaded,
    editing,
    busy,
    dirty,
    change,
    element: editor,
    cancel,
  } = state;
  return (
    <section ref={editor} className="editor" aria-label="Edit project">
      <DetailHeader title={values.name} close={close} />
      <DetailTabs
        id={id}
        title="Project settings"
        tabs={["info", "coordinator", "workers", "integration"]}
        active={tab}
        change={(value) =>
          void navigate(
            projectHref(incoming.id) +
              "/edit" +
              (value === "info" ? "" : "/" + value),
            { state: location.state },
          )
        }
      />
      <EditorFeedback state={state} />
      <div
        role="tabpanel"
        id={`${id}-info`}
        aria-labelledby={`${id}-info-tab`}
        hidden={tab !== "info"}
        className="content-stack"
        data-space="section"
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void state.run("PUT", {
              ...values,
              expected_revision: loaded!.revision,
              author: "human",
            });
          }}
        >
          <fieldset
            disabled={busy}
            className="content-stack"
            data-space="section"
          >
            {editing ? (
              <>
                <Label className="field block">
                  Name
                  <Input
                    value={values.name}
                    maxLength={200}
                    required
                    onChange={(event) => change("name", event.target.value)}
                  />
                </Label>
                <Label className="field block">
                  Prefix
                  <Input
                    value={values.task_prefix}
                    required
                    pattern="[A-Za-z]{3}"
                    minLength={3}
                    maxLength={3}
                    disabled={hasTasks}
                    onChange={(event) =>
                      change("task_prefix", event.target.value.toUpperCase())
                    }
                  />
                </Label>
                <p>
                  {hasTasks
                    ? "Fixed after the first task to preserve keys and links."
                    : "Choose a unique three-letter prefix before creating tasks."}
                </p>
                <MarkdownField
                  label="Description"
                  value={values.description}
                  onChange={(value) => change("description", value)}
                  previewEnabled={false}
                />
              </>
            ) : (
              <ContentStack space="section">
                <DetailSection title="Prefix">
                  <p>{values.task_prefix}</p>
                </DetailSection>
                <DetailSection title="Directory">
                  <p className="break-all">{incoming.path}</p>
                </DetailSection>
                <DetailSection title="Description" className="record-info">
                  <div className="agreement-content">
                    <Markdown>
                      {values.description || "No description yet."}
                    </Markdown>
                  </div>
                </DetailSection>
              </ContentStack>
            )}
            <div className="actions editor-actions">
              {editing ? (
                <>
                  <Button size="sm" disabled={!dirty}>
                    Save changes
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    type="button"
                    className="quiet"
                    onClick={cancel}
                  >
                    Cancel
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  type="button"
                  onClick={() => state.setEditing(true)}
                >
                  Edit
                </Button>
              )}
            </div>
          </fieldset>
        </form>
      </div>
      <div
        role="tabpanel"
        id={`${id}-coordinator`}
        aria-labelledby={`${id}-coordinator-tab`}
        hidden={tab !== "coordinator"}
        className="content-stack"
        data-space="section"
      >
        <Disclosure
          summary="Coordinator defaults"
          onToggle={(event) => {
            if (event.currentTarget.open) setCoordinatorOpened(true);
          }}
        >
          {coordinatorOpened && (
            <AgentSettingsEditor
              coordinator
              path={`projects/${incoming.id}/coordinator-settings`}
              refresh={incoming}
              onDirty={setCoordinatorDirty}
            />
          )}
        </Disclosure>
        <ProjectGuidance
          projectId={incoming.id}
          active={tab === "coordinator"}
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-workers`}
        aria-labelledby={`${id}-workers-tab`}
        hidden={tab !== "workers"}
      >
        <WorkerSettings
          projectId={incoming.id}
          onDirty={setWorkerDirty}
          refresh={incoming}
        />
      </div>
      <div
        role="tabpanel"
        id={`${id}-integration`}
        aria-labelledby={`${id}-integration-tab`}
        hidden={tab !== "integration"}
      >
        <IntegrationSettings
          projectId={incoming.id}
          onDirty={setIntegrationDirty}
          refresh={incoming}
        />
      </div>
    </section>
  );
}
