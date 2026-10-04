import {
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProjectBadge } from "./ProjectBadge";
import "./workspace-frame.css";

export type WorkspaceProject = { id: string; name: string; href: string };
const compactQuery = "(max-width: 1023px)";
function subscribeCompact(callback: () => void) {
  const query = window.matchMedia(compactQuery);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

function ProjectNavigation({
  projects,
  activeProjectId,
  onProjectSelect,
  footer,
}: {
  projects: WorkspaceProject[];
  activeProjectId: string;
  onProjectSelect: (project: WorkspaceProject) => void;
  footer?: ReactNode;
}) {
  const { setOpenMobile, state, isMobile } = useSidebar();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="workspace-sidebar-header">
        <span className="workspace-wordmark group-data-[collapsible=icon]:hidden">
          Flowfield
        </span>
        <SidebarTrigger
          aria-label={
            isMobile
              ? "Close projects"
              : state === "expanded"
                ? "Collapse projects"
                : "Expand projects"
          }
          aria-expanded={isMobile || state === "expanded"}
        />
      </SidebarHeader>
      <SidebarContent>
        <nav aria-label="Projects" className="workspace-projects">
          <p className="workspace-projects-label group-data-[collapsible=icon]:hidden">
            Projects
          </p>
          <SidebarMenu className="gap-2">
            {projects.map((project) => (
              <SidebarMenuItem key={project.id}>
                <SidebarMenuButton
                  asChild
                  size="lg"
                  isActive={project.id === activeProjectId}
                  tooltip={project.name}
                >
                  <a
                    href={project.href}
                    aria-label={project.name}
                    aria-current={
                      project.id === activeProjectId ? "page" : undefined
                    }
                    onClick={(event) => {
                      if (
                        event.button !== 0 ||
                        event.metaKey ||
                        event.ctrlKey ||
                        event.shiftKey ||
                        event.altKey
                      )
                        return;
                      event.preventDefault();
                      onProjectSelect(project);
                      setOpenMobile(false);
                    }}
                  >
                    <ProjectBadge id={project.id} name={project.name} />
                    <span className="group-data-[collapsible=icon]:hidden">
                      {project.name}
                    </span>
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </nav>
      </SidebarContent>
      {footer && <SidebarFooter>{footer}</SidebarFooter>}
    </Sidebar>
  );
}

// Owns layout only. Conversation state, navigation and work remain with their callers.
export function WorkspaceFrame({
  projects,
  activeProjectId,
  onProjectSelect,
  coordinator,
  work,
  footer,
}: {
  projects: WorkspaceProject[];
  activeProjectId: string;
  onProjectSelect: (project: WorkspaceProject) => void;
  coordinator: ReactNode;
  work: ReactNode;
  footer?: ReactNode;
}) {
  const compact = useSyncExternalStore(
    subscribeCompact,
    () => window.matchMedia(compactQuery).matches,
    () => false,
  );
  const [surface, setSurface] = useState("coordinator");
  const [sidebarOpen, setSidebarOpen] = useState(
    () =>
      !document.cookie
        .split(";")
        .some((part) => part.trim() === "sidebar_state=false"),
  );
  return (
    <SidebarProvider
      open={sidebarOpen}
      onOpenChange={setSidebarOpen}
      className="workspace-frame"
      style={
        {
          "--sidebar-width": "14rem",
          "--sidebar-width-icon": "3.5rem",
        } as CSSProperties
      }
    >
      <ProjectNavigation
        projects={projects}
        activeProjectId={activeProjectId}
        onProjectSelect={onProjectSelect}
        footer={footer}
      />
      <main className="workspace-main">
        <Tabs
          value={surface}
          onValueChange={setSurface}
          className="workspace-surfaces"
        >
          {compact && (
            <div className="workspace-surface-switch">
              <SidebarTrigger
                className="md:hidden"
                aria-label="Open projects"
              />
              <TabsList aria-label="Workspace surface">
                <TabsTrigger value="coordinator">Coordinator</TabsTrigger>
                <TabsTrigger value="work">Work</TabsTrigger>
              </TabsList>
            </div>
          )}
          <ResizablePanelGroup
            orientation="horizontal"
            disabled={compact}
            className="workspace-panels"
          >
            <ResizablePanel
              id="coordinator"
              defaultSize="440px"
              minSize={
                compact ? (surface === "coordinator" ? "100%" : "0%") : "360px"
              }
              maxSize={
                compact
                  ? surface === "coordinator"
                    ? "100%"
                    : "0%"
                  : undefined
              }
            >
              <TabsContent
                forceMount
                value="coordinator"
                aria-label="Coordinator"
                className="workspace-pane"
                inert={compact && surface !== "coordinator"}
                aria-hidden={compact && surface !== "coordinator"}
              >
                {coordinator}
              </TabsContent>
            </ResizablePanel>
            <ResizableHandle
              aria-label="Resize coordinator and work"
              className={compact ? "hidden" : "workspace-divider"}
            />
            <ResizablePanel
              id="work"
              minSize={compact ? (surface === "work" ? "100%" : "0%") : "320px"}
              maxSize={
                compact ? (surface === "work" ? "100%" : "0%") : undefined
              }
            >
              <TabsContent
                forceMount
                value="work"
                aria-label="Project work"
                className="workspace-pane"
                inert={compact && surface !== "work"}
                aria-hidden={compact && surface !== "work"}
              >
                {work}
              </TabsContent>
            </ResizablePanel>
          </ResizablePanelGroup>
        </Tabs>
      </main>
    </SidebarProvider>
  );
}
