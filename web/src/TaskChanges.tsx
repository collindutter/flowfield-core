import { Disclosure, DetailSection } from "./DetailLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useResource } from "./useResource";
import { useState } from "react";
import { Markdown } from "./Markdown";
import {
  label,
  type Milestone,
  type TaskReference,
  type TaskRevision,
} from "./workspace";

type Field = "body" | "title" | "task_type" | "milestone_id" | "dependencies";
const fields: [Field, string][] = [
  ["body", "Description"],
  ["title", "Title"],
  ["task_type", "Type"],
  ["milestone_id", "Milestone"],
  ["dependencies", "Prerequisites"],
];
export function TaskChanges({
  revision,
  path,
  milestones,
  tasks,
}: {
  revision: number;
  path: string;
  milestones: Milestone[];
  tasks: TaskReference[];
}) {
  const [open, setOpen] = useState(false);
  const [retry, setRetry] = useState(0);
  const enabled = open && revision > 1;
  const beforeRead = useResource<TaskRevision>(
    enabled ? `${path}/revisions/${revision - 1}` : null,
    retry,
  );
  const afterRead = useResource<TaskRevision>(
    enabled ? `${path}/revisions/${revision}` : null,
    retry,
  );
  const before = beforeRead.data,
    after = afterRead.data;
  if (revision <= 1) return null;
  const changed =
    before && after
      ? fields.filter(
          ([key]) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
        )
      : [];
  function value(record: TaskRevision, field: Field): string {
    if (field === "task_type") return label(record.task_type);
    if (field === "milestone_id")
      return (
        milestones.find((e) => e.id === record.milestone_id)?.title ??
        "No milestone"
      );
    if (field === "dependencies")
      return (
        record.dependencies
          .map((id) => tasks.find((t) => t.id === id)?.key ?? id)
          .join(", ") || "None"
      );
    return record[field];
  }
  return (
    <Disclosure
      summary={<>View definition changes</>}
      className="task-changes history-disclosure"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      {open && (beforeRead.error || afterRead.error) && (
        <Alert variant="destructive">
          <AlertDescription>
            {beforeRead.error || afterRead.error}{" "}
            <Button size="sm" onClick={() => setRetry((v) => v + 1)}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {open && (!before || !after) && !beforeRead.error && !afterRead.error && (
        <p>Loading changes…</p>
      )}
      {open &&
        before &&
        after &&
        (changed.length ? (
          changed.map(([key, name]) => (
            <DetailSection key={key} title={name}>
              <div className="change-before content-stack" data-space="tight">
                <strong>Before</strong>
                <Markdown>{value(before, key) || "Empty"}</Markdown>
              </div>
              <div className="change-after content-stack" data-space="tight">
                <strong>After</strong>
                <Markdown>{value(after, key) || "Empty"}</Markdown>
              </div>
            </DetailSection>
          ))
        ) : (
          <p>No net change across these edits.</p>
        ))}
    </Disclosure>
  );
}
