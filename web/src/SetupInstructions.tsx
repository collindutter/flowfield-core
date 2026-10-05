import { useState } from "react";
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
          Choose an existing project to plan work in Coordinator Chat and follow
          it on the board.
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!path || busy) return;
            setBusy("adding");
            setError("");
            try {
              added(
                await request<Project>("projects/initialize", "POST", {
                  path,
                  ...(id.trim() ? { id: id.trim() } : {}),
                  ...(name.trim() ? { name: name.trim() } : {}),
                  ...(prefix.trim() ? { task_prefix: prefix.trim() } : {}),
                }),
              );
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
                  <Input aria-label="Project directory" value={path} readOnly />
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
                      const selection = await request<{ path: string | null }>(
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
                {path && (
                  <Button type="submit">
                    {busy === "adding" ? "Adding project…" : "Add project"}
                  </Button>
                )}
              </div>
            </ContentStack>
            {path && (
              <Disclosure summary="Project details (optional)">
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
                  Choose a unique ID if another project has the same directory
                  name.
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
                  Choose a unique three-letter prefix if the derived prefix is
                  already used.
                </p>
              </Disclosure>
            )}
          </fieldset>
        </form>
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
            Enable <strong>Use Local host</strong> in Integration settings, then
            choose your model in Coordinator settings. Install and sign in to
            Codex, and install its managed ACP runtime on the machine running
            Flowfield.
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
            project. Built-in Coordinator Chat supplies its own MCP connection.
          </p>
        </Disclosure>
      </ContentStack>
    </section>
  );
}
