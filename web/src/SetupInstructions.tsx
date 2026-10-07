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
    <section className="welcome" aria-label="Project setup instructions">
      <ContentStack space="section">
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
              if (!path || busy) return;
              setBusy("adding");
              setError("");
              try {
                const project = await request<Project>(
                  "projects/initialize",
                  "POST",
                  {
                    path,
                    ...(id.trim() ? { id: id.trim() } : {}),
                    ...(name.trim() ? { name: name.trim() } : {}),
                    ...(prefix.trim() ? { task_prefix: prefix.trim() } : {}),
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
                  {path && (
                    <Button type="submit">
                      {busy === "adding" ? "Adding project…" : "Add project"}
                    </Button>
                  )}
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
                          setPath(selection.path);
                          setId("");
                          setName("");
                          setPrefix("");
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
                    <p>
                      <code>AGENTS.md</code> gets a Flowfield reference;{" "}
                      <code>.agents/skills/flowfield-coordinator/SKILL.md</code>{" "}
                      contains coordinator guidance. Review and commit these
                      files so workers receive them.
                    </p>
                    {templates.error && <p role="alert">{templates.error}</p>}
                    {templates.loading && <p>Loading guidance…</p>}
                    {templates.data && (
                      <ContentStack>
                        <pre className="evidence-output">
                          {templates.data.section}
                        </pre>
                        <pre className="evidence-output">
                          {templates.data.skill}
                        </pre>
                      </ContentStack>
                    )}
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
                <Disclosure summary="Project details (optional)">
                  <ContentStack space="section">
                    <Label className="field block">
                      Name
                      <Input
                        value={name}
                        maxLength={200}
                        placeholder="Derived from the directory name"
                        onChange={(event) => setName(event.target.value)}
                      />
                    </Label>
                    <Label className="field block">
                      Project ID
                      <Input
                        value={id}
                        maxLength={64}
                        pattern={"[a-z0-9][a-z0-9_\\-]{0,63}"}
                        placeholder="Derived from the directory name"
                        onChange={(event) => setId(event.target.value)}
                      />
                    </Label>
                    <p>
                      Choose a unique ID if another project has the same
                      directory name.
                    </p>
                    <Label className="field block">
                      Task prefix
                      <Input
                        value={prefix}
                        minLength={3}
                        maxLength={3}
                        pattern="[A-Za-z]{3}"
                        placeholder="Three letters, derived from the project ID"
                        onChange={(event) =>
                          setPrefix(event.target.value.toUpperCase())
                        }
                      />
                    </Label>
                    <p>
                      Choose a unique three-letter prefix if the derived prefix
                      is already used.
                    </p>
                  </ContentStack>
                </Disclosure>
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
        <DetailSection title="Start planning">
          <p>
            Choose a model in the Coordinator. Flowfield uses this machine’s
            installed, signed-in Codex and managed ACP runtime.
          </p>
          <p>
            Worker setup and delivery settings can wait until you’re ready to
            run tasks.
          </p>
        </DetailSection>
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
