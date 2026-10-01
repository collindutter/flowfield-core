import { useEffect, useRef, useState } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { WorkspaceLink } from "./WorkspaceLink";

/** Retain native link semantics; disclose the name only when its text is clipped. */
export function ProjectNameLink({ name, to }: { name: string; to: string }) {
  const text = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  useEffect(() => {
    const element = text.current;
    if (!element) return;
    const measure = () =>
      setTruncated(element.scrollWidth > element.clientWidth);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [name]);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <WorkspaceLink to={to}>
          <span ref={text} className="block truncate">
            {name}
          </span>
        </WorkspaceLink>
      </TooltipTrigger>
      {truncated && (
        <TooltipContent className="max-w-xs">{name}</TooltipContent>
      )}
    </Tooltip>
  );
}
