import { useState } from "react";
import type { components } from "./api-schema";
import { useResource } from "./useResource";
import { ProjectGuidance } from "./ProjectGuidance";
import { FolderOpen } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ContentStack, DetailSection, Disclosure } from "./DetailLayout";
import { request, type Project } from "./workspace";

export function serviceCommand(command: string) {
  const port = import.meta.env.DEV ? "8765" : window.location.port || "8765";
  return `flowfield${port === "8765" ? "" : ` --port ${port}`} ${command}`;
}

export function SetupInstructions({
  added,
}: {
  added: (project: Project) => void;
}) {
  const [preview, setPreview] = useState(false);
  const templates = useResource<{ section: string; skill: string }>(
    preview ? "guidance-template" : null,
    0,
  );
  const [installGuidance, setInstallGuidance] = useState(true);
  const [registered, setRegistered] = useState<Project | null>(null);
  const [path, setPath] = useState("");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [prefix, setPrefix] = useState("");
  const [busy, setBusy] = useState<"choosing" | "adding" | null>(null);
  const [error, setError] = useState("");
  return (
    <section className="setup-page" aria-label="Project setup instructions">
      <ContentStack space="section" className="welcome">
        <h1>Set up your project</h1>
        <p>
          Choose an existing project to plan work in the Coordinator and follow
          it on the board.
        </p>
        {registered ? (
          <ContentStack space="section">
            <p>
              {busy
                ? "Installing project guidance…"
                : "Your project was added. Guidance installation needs attention; your existing files are preserved."}
            </p>
            {!busy && <ProjectGuidance projectId={registered.id} active />}
            <Button disabled={!!busy} onClick={() => added(registered)}>
              Continue to project
            </Button>
          </ContentStack>
        ) : (
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (!path || !id.trim() || !name.trim() || !prefix.trim() || busy)
                return;
              setBusy("adding");
              setError("");
              try {
                const project = await request<Project>(
                  "projects/initialize",
                  "POST",
                  {
                    path,
                    id: id.trim(),
                    name: name.trim(),
                    task_prefix: prefix.trim(),
                  },
                );
                setRegistered(project);
                if (installGuidance) {
                  const guidancePath = `projects/${project.id}/guidance`;
                  const guidance =
                    await request<components["schemas"]["GuidanceView"]>(
                      guidancePath,
                    );
                  await request(guidancePath, "POST", {
                    action: "install",
                    expected_revision: guidance.revision,
                  });
                }
                added(project);
              } catch (failure) {
                setError((failure as Error).message);
              } finally {
                setBusy(null);
              }
            }}
          >
            <fieldset
              disabled={busy !== null}
              className="content-stack"
              data-space="section"
            >
              <ContentStack>
                {path && (
                  <Label className="field block">
                    Project directory
                    <Input
                      aria-label="Project directory"
                      value={path}
                      readOnly
                    />
                  </Label>
                )}
                <div className="actions">
                  <Button
                    type="button"
                    variant={path ? "outline" : "default"}
                    onClick={async () => {
                      setBusy("choosing");
                      setError("");
                      try {
                        const selection = await request<{
                          path: string | null;
                        }>(
                          "projects/select-directory",
                          "POST",
                          undefined,
                          undefined,
                          310000,
                        );
                        if (selection.path) {
                          const defaults = await request<
                            components["schemas"]["ProjectSetupDefaults"]
                          >("projects/setup-defaults", "POST", {
                            path: selection.path,
                          });
                          setPath(selection.path);
                          setId(defaults.id);
                          setName(defaults.name);
                          setPrefix(defaults.task_prefix);
                        }
                      } catch (failure) {
                        setError((failure as Error).message);
                      } finally {
                        setBusy(null);
                      }
                    }}
                  >
                    <FolderOpen />
                    {busy === "choosing"
                      ? "Choosing directory…"
                      : path
                        ? "Change directory"
                        : "Choose directory"}
                  </Button>
                </div>
              </ContentStack>
              {path && (
                <DetailSection title="Project details">
                  <ContentStack space="section">
                    <Label className="field block">
                      Name
                      <Input
                        required
                        pattern={".*\\S.*"}
                        value={name}
                        maxLength={200}
                        onChange={(event) => setName(event.target.value)}
                      />
                    </Label>
                    <Label className="field block">
                      Project ID
                      <Input
                        required
                        value={id}
                        maxLength={64}
                        pattern={"[a-z0-9][a-z0-9_\\-]{0,63}"}
                        onChange={(event) => setId(event.target.value)}
                      />
                    </Label>
                    <p className="detail-metadata">
                      A unique ID used in project links.
                    </p>
                    <Label className="field block">
                      Task prefix
                      <Input
                        required
                        value={prefix}
                        minLength={3}
                        maxLength={3}
                        pattern="[A-Za-z]{3}"
                        onChange={(event) =>
                          setPrefix(event.target.value.toUpperCase())
                        }
                      />
                    </Label>
                    <p className="detail-metadata">
                      Three unique letters for task IDs, such as FOL-1.
                    </p>
                  </ContentStack>
                </DetailSection>
              )}
              {path && (
                <ContentStack>
                  <Label>
                    <input
                      type="checkbox"
                      checked={installGuidance}
                      onChange={(event) =>
                        setInstallGuidance(event.target.checked)
                      }
                    />{" "}
                    Install project guidance
                  </Label>
                  <p className="detail-metadata">
                    Helps standalone agents coordinate work and prepares
                    workers. Existing instructions are preserved.
                  </p>
                  <Disclosure
                    summary="Preview project guidance"
                    onToggle={(event) => setPreview(event.currentTarget.open)}
                  >
                    <ContentStack space="section">
                      <p>
                        <code>AGENTS.md</code> gets a Flowfield reference;{" "}
                        <code>
                          .agents/skills/flowfield-coordinator/SKILL.md
                        </code>{" "}
                        contains coordinator guidance. Review and commit these
                        files so workers receive them.
                      </p>
                      {templates.error && <p role="alert">{templates.error}</p>}
                      {templates.loading && <p>Loading guidance…</p>}
                      {templates.data && (
                        <ContentStack space="section">
                          <DetailSection title="AGENTS.md section">
                            <pre className="evidence-output">
                              {templates.data.section}
                            </pre>
                          </DetailSection>
                          <DetailSection title=".agents/skills/flowfield-coordinator/SKILL.md">
                            <pre className="evidence-output">
                              {templates.data.skill}
                            </pre>
                          </DetailSection>
                        </ContentStack>
                      )}
                    </ContentStack>
                  </Disclosure>
                  {!installGuidance && (
                    <p className="detail-metadata">
                      Install later in Project settings → Coordinator. The
                      built-in Coordinator can already help you set up workers.
                    </p>
                  )}
                </ContentStack>
              )}
              {path && (
                <div className="actions">
                  <Button type="submit">
                    {busy === "adding" ? "Adding project…" : "Add project"}
                  </Button>
                </div>
              )}
            </fieldset>
          </form>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <p>
          Registration adds <code>.flowfield/config.toml</code>. Flowfield does
          not create directories or initialize Git.
        </p>
        <Disclosure summary="Add a project from the CLI">
          <p>
            Run this in your existing project directory while Flowfield is
            running:
          </p>
          <pre className="evidence-output font-sans text-sm">
            {serviceCommand("project init")}
          </pre>
          <p>The project appears in the sidebar.</p>
        </Disclosure>
        <Disclosure summary="Use a standalone coding agent">
          <p>
            To plan from a separate Codex session, preview and install project
            guidance, then connect it to Flowfield:
          </p>
          <pre className="evidence-output font-sans text-sm">
            {[
              serviceCommand("project guidance preview"),
              serviceCommand("project guidance install"),
              serviceCommand("integration connect codex"),
            ].join("\n")}
          </pre>
          <p>
            Review the added guidance and start a fresh Codex session in the
            project. The Coordinator supplies its own MCP connection.
          </p>
        </Disclosure>
      </ContentStack>
    </section>
  );
}
