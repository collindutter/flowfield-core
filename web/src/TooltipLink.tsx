import type { AnchorHTMLAttributes } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function TooltipLink({
  tooltip,
  children,
  className = "",
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { tooltip: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <a {...props} className={`inline-flex w-fit ${className}`}>
          {children}
        </a>
      </TooltipTrigger>
      <TooltipContent collisionPadding={8} className="max-w-xs">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}
