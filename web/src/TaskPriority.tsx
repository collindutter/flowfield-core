import { Button } from "@/components/ui/button";
import { isUpcoming, type TaskCard, type WorkStatus } from "./workspace";

/** Keyboard/touch access to the same board priority operation as dragging. */
export function TaskPriority({
  task,
  busy,
  prioritize,
}: {
  task: TaskCard;
  busy: boolean;
  prioritize: (
    task: TaskCard,
    status: WorkStatus,
    before?: string | null,
  ) => Promise<void>;
}) {
  if (task.archived || !isUpcoming(task.status)) return null;
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() =>
          void prioritize(
            task,
            task.status === "backlog" ? "up_next" : "backlog",
          )
        }
      >
        {task.status === "backlog" ? "Choose for Up next" : "Defer to Backlog"}
      </Button>
    </>
  );
}
