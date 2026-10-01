import { Check, ChevronRight } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { components } from "./api-schema";
import { label as statusLabel } from "./workspace";

export function StageSequence({
  stages,
  label,
}: {
  stages: components["schemas"]["StagePlan"]["stages"];
  label: string;
}) {
  return (
    <div aria-label={label} role="group" className="task-stages">
      <ol>
        {stages.map((stage, index) => (
          <li key={stage.id} data-state={stage.status}>
            {index > 0 && <ChevronRight aria-hidden="true" size={14} />}
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className="task-stage"
                  tabIndex={0}
                  aria-current={stage.status === "active" ? "step" : undefined}
                >
                  {stage.status === "completed" && (
                    <Check aria-hidden="true" size={14} />
                  )}
                  {stage.title}
                  <span className="sr-only">: {statusLabel(stage.status)}</span>
                </span>
              </TooltipTrigger>
              <TooltipContent>{stage.outcome}</TooltipContent>
            </Tooltip>
          </li>
        ))}
      </ol>
    </div>
  );
}
