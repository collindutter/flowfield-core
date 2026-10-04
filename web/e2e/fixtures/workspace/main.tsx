import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowUp, Bell, Settings } from "lucide-react";
import { WorkspaceFrame } from "../../../src/WorkspaceFrame";
import { TaskIdentity } from "../../../src/Presentation";
import { ContentStack, DetailSection } from "../../../src/DetailLayout";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import "../../../src/style.css";
import "./preview.css";

const projects = [
  { id: "harbor", name: "Harbor", href: "#harbor" },
  { id: "field-notes", name: "Field Notes", href: "#field-notes" },
  { id: "atlas", name: "Atlas", href: "#atlas" },
];
const tasks = [
  {
    key: "HBR-12",
    title: "Show a useful error for an invalid catalog",
    task_type: "bug" as const,
    column: "Up next",
    note: "Include the input path and the first invalid row.",
  },
  {
    key: "HBR-14",
    title: "Remember the last import folder",
    task_type: "feature" as const,
    column: "Backlog",
    note: "Keep repeated imports quick.",
  },
  {
    key: "HBR-15",
    title: "Support dragging a catalog into the preview",
    task_type: "feature" as const,
    column: "Backlog",
    note: "Use the same validation as the file picker.",
  },
  {
    key: "HBR-11",
    title: "Preview changes before importing",
    task_type: "feature" as const,
    column: "In progress",
    note: "Comparing the incoming catalog with the current collection.",
  },
  {
    key: "HBR-10",
    title: "Load and validate the catalog",
    task_type: "feature" as const,
    column: "In review",
    note: "Ready for you to review the import result.",
  },
  {
    key: "HBR-8",
    title: "Define the catalog format",
    task_type: "investigation" as const,
    column: "Done",
    note: "Required fields and duplicate handling agreed.",
  },
];
const columns = ["Backlog", "Up next", "In progress", "In review", "Done"];

function Fixture() {
  const [project, setProject] = useState(projects[0]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [task, setTask] = useState<(typeof tasks)[number] | null>(null);
  const [view, setView] = useState("board");
  const [utility, setUtility] = useState<string | null>(null);
  const openTask = (key: string) =>
    setTask(tasks.find((item) => item.key === key) ?? null);
  const taskLink = (key: string) => (
    <a
      href={`#${key}`}
      onClick={(event) => {
        event.preventDefault();
        openTask(key);
      }}
    >
      {key}
    </a>
  );
  return (
    <>
      <WorkspaceFrame
        projects={projects}
        activeProjectId={project.id}
        onProjectSelect={(next) => {
          setProject(next);
          window.history.replaceState(null, "", next.href);
        }}
        footer={
          <Button
            variant="ghost"
            className="justify-start group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:p-0"
            aria-label="Notifications"
            onClick={() => setUtility("Notifications")}
          >
            <Bell />
            <span className="group-data-[collapsible=icon]:hidden">
              Notifications
            </span>
          </Button>
        }
        coordinator={
          <>
            <header className="workspace-pane-header">
              <h2>Coordinator</h2>
              <span className="fixture-agent-name">Codex</span>
            </header>
            <div
              className="fixture-conversation"
              aria-label="Coordinator conversation"
              tabIndex={0}
            >
              <div className="fixture-message">
                <span className="fixture-author">You</span>
                <p>
                  Let’s make {project.name}’s first import feel reliable. What
                  should we tackle next?
                </p>
              </div>
              <div className="fixture-message">
                <span className="fixture-author">Coordinator</span>
                <p>
                  The catalog format is agreed, and validation is ready for
                  review. I’d finish the import path before adding more ways to
                  bring files in.
                </p>
                <p>Here’s where the work stands:</p>
                <ul>
                  <li>{taskLink("HBR-10")} is ready for your review.</li>
                  <li>
                    {taskLink("HBR-11")} is building the preview of incoming
                    changes.
                  </li>
                  <li>
                    {taskLink("HBR-12")} will make validation errors easier to
                    fix.
                  </li>
                </ul>
                <p>
                  The drag-and-drop idea can stay in Backlog until that path
                  feels good.
                </p>
              </div>
              <div className="fixture-message">
                <span className="fixture-author">You</span>
                <p>
                  That order makes sense. Keep invalid-catalog errors next, and
                  leave the folder shortcut for later.
                </p>
              </div>
              <div className="fixture-message">
                <span className="fixture-author">Coordinator</span>
                <p>
                  {taskLink("HBR-12")} is Up next. The folder shortcut and
                  drag-and-drop work remain in Backlog.
                </p>
                <p>
                  You can review the catalog result while the preview work
                  continues.
                </p>
              </div>
            </div>
            <div className="fixture-composer">
              <Label htmlFor="coordinator-message">Message coordinator</Label>
              <Textarea
                id="coordinator-message"
                value={drafts[project.id] ?? ""}
                onChange={(event) =>
                  setDrafts({ ...drafts, [project.id]: event.target.value })
                }
                placeholder="Discuss the next step…"
              />
              <div className="fixture-composer-actions">
                <span>{project.name}</span>
                <Button size="icon-sm" aria-label="Send message" disabled>
                  <ArrowUp />
                </Button>
              </div>
            </div>
          </>
        }
        work={
          <>
            <header className="workspace-pane-header">
              <h1>{project.name}</h1>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label="Project settings"
                onClick={() => setUtility("Project settings")}
              >
                <Settings />
              </Button>
            </header>
            <div className="fixture-work-navigation">
              <Tabs value={view} onValueChange={setView}>
                <TabsList variant="line" aria-label="Project work">
                  <TabsTrigger value="board">Board</TabsTrigger>
                  <TabsTrigger value="needs-you">
                    Needs you <span className="fixture-count">1</span>
                  </TabsTrigger>
                  <TabsTrigger value="milestones">Milestones</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
            {view === "board" ? (
              <div
                className="workspace-board-scroll"
                aria-label="Task board"
                tabIndex={0}
              >
                <div className="workspace-board-columns">
                  {columns.map((column) => (
                    <section key={column} className="fixture-column">
                      <header>
                        <h2>{column}</h2>
                        <span>
                          {
                            tasks.filter((item) => item.column === column)
                              .length
                          }
                        </span>
                      </header>
                      {column === "Up next" && (
                        <div className="fixture-queue">
                          <Button size="sm" variant="outline" disabled>
                            Run queue
                          </Button>
                          <span>Paused</span>
                        </div>
                      )}
                      {tasks
                        .filter((item) => item.column === column)
                        .map((item) => (
                          <a
                            key={item.key}
                            href={`#${item.key}`}
                            onClick={(event) => {
                              event.preventDefault();
                              setTask(item);
                            }}
                            className="fixture-task"
                          >
                            <Card className="gap-3 rounded-lg p-4 shadow-none">
                              <TaskIdentity task={item} />
                              <p>{item.note}</p>
                            </Card>
                          </a>
                        ))}
                    </section>
                  ))}
                </div>
              </div>
            ) : (
              <div className="fixture-work-list">
                {view === "needs-you" ? (
                  <>
                    <h2>Ready for review</h2>
                    <a
                      href="#HBR-10"
                      onClick={(event) => {
                        event.preventDefault();
                        openTask("HBR-10");
                      }}
                    >
                      Load and validate the catalog
                    </a>
                  </>
                ) : (
                  <>
                    <h2>First reliable import</h2>
                    <p>
                      Validate a catalog, preview its changes and bring it into
                      the collection.
                    </p>
                    <span className="muted">1 of 4 tasks complete</span>
                  </>
                )}
              </div>
            )}
          </>
        }
      />
      <Dialog
        open={task !== null}
        onOpenChange={(open) => {
          if (!open) setTask(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{task?.title}</DialogTitle>
            <DialogDescription>
              {task?.key} · {task?.column}
            </DialogDescription>
          </DialogHeader>
          <ContentStack space="section">
            <DetailSection title="Definition">
              <p>{task?.note}</p>
            </DetailSection>
            <DetailSection title="Activity">
              <p>The task is part of the first reliable import milestone.</p>
            </DetailSection>
          </ContentStack>
        </DialogContent>
      </Dialog>
      <Dialog
        open={utility !== null}
        onOpenChange={(open) => {
          if (!open) setUtility(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{utility}</DialogTitle>
            <DialogDescription>
              {utility === "Notifications"
                ? "You’re all caught up."
                : project.name}
            </DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider delayDuration={0}>
      <Fixture />
    </TooltipProvider>
  </StrictMode>,
);
