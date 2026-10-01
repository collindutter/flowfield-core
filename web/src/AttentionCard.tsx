import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { WorkspaceLink } from "./WorkspaceLink";
import type { ReactNode } from "react";

/** Consistent attention surfaces; the label/action retain each item's own lifecycle. */
export function AttentionCard({
  href,
  kind,
  reference,
  title,
  detail,
  action,
  selected = false,
}: {
  href: string;
  kind: string;
  reference: string;
  title: string;
  detail?: string;
  action: ReactNode;
  selected?: boolean;
}) {
  return (
    <Card
      className={`gap-0 p-0 shadow-none transition-colors hover:border-foreground/40 ${selected ? "border-foreground/40" : ""}`}
    >
      <WorkspaceLink
        to={href}
        className={`attention-card border-0 rounded-xl${selected ? " selected" : ""}`}
      >
        <span className="attention-card-meta">
          <Badge variant="outline">{kind}</Badge>
          <span>{reference}</span>
        </span>
        <strong>{title}</strong>
        {detail && <span className="attention-card-detail">{detail}</span>}
        <span className="attention-card-action">
          {action}
          <span aria-hidden="true"> →</span>
        </span>
      </WorkspaceLink>
    </Card>
  );
}
