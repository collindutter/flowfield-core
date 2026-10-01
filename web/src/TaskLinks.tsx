import { TooltipLink } from "./TooltipLink";
import { followLink, taskHref } from "./navigation";
import type { TaskReference } from "./workspace";
export function TaskLink({
  projectId,
  task,
  open,
}: {
  projectId: string;
  task: TaskReference;
  open: (id: string) => void;
}) {
  return (
    <TooltipLink
      className="task-key"
      href={taskHref(projectId, task)}
      tooltip={task.title}
      onClick={(event) => followLink(event, () => open(task.id))}
    >
      {task.key}
    </TooltipLink>
  );
}

export function BlockedBy({
  projectId,
  tasks,
  open,
  prefix = "Blocked by",
}: {
  projectId: string;
  tasks: TaskReference[];
  open: (id: string) => void;
  prefix?: string;
}) {
  return (
    <span>
      {prefix}{" "}
      {tasks.map((task, index) => (
        <span key={task.id}>
          {index > 0 && ", "}
          <TaskLink projectId={projectId} task={task} open={open} />
        </span>
      ))}
    </span>
  );
}
