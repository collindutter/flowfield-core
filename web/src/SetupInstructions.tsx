import { ContentStack, DetailSection } from "./DetailLayout";

export function serviceCommand(command: string) {
  const port = import.meta.env.DEV ? "8765" : window.location.port || "8765";
  return `flowfield${port === "8765" ? "" : ` --port ${port}`} ${command}`;
}

export function SetupInstructions() {
  return (
    <section className="welcome" aria-label="Project setup instructions">
      <ContentStack space="section">
        <h1>Set up your project</h1>
        <p>
          Use a terminal in your existing Git project. Keep Flowfield running.
        </p>
        <DetailSection title="1. Add the project">
          <pre className="evidence-output font-sans text-sm">
            {serviceCommand("project init")}
          </pre>
          <p>
            Registers the current directory. It does not create a project or
            initialize Git.
          </p>
        </DetailSection>
        <DetailSection title="2. Install coordinator guidance">
          <pre className="evidence-output font-sans text-sm">
            {[
              serviceCommand("project guidance preview"),
              serviceCommand("project guidance install"),
            ].join("\n")}
          </pre>
          <p>
            Review the preview before installing. Commit the guidance under your
            repository’s rules.
          </p>
        </DetailSection>
        <DetailSection title="3. Connect Codex">
          <pre className="evidence-output font-sans text-sm">
            {serviceCommand("integration connect codex")}
          </pre>
          <p>Start a fresh Codex session in the project after connecting.</p>
        </DetailSection>
        <DetailSection title="4. Configure with your coordinator">
          <p>
            Ask it to read the Flowfield board and help choose your worker
            model, delivery branch, tools, setup, check and run commands.
            Validate setup before enabling work.
          </p>
        </DetailSection>
      </ContentStack>
    </section>
  );
}
