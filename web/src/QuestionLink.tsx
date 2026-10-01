import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { TooltipLink } from "./TooltipLink";
import {
  followLink,
  questionOverlayHref,
  withoutQuestionOverlay,
} from "./navigation";

/** Related input opens over the current page; the inbox itself remains a full view. */
export function QuestionLink({
  id,
  children,
  tooltip,
  className = "compact-control",
}: {
  id: string;
  children: ReactNode;
  tooltip?: string;
  className?: string;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const href = questionOverlayHref(location.pathname, id);
  const state = {
    ...location.state,
    overlayFrom: withoutQuestionOverlay(location.pathname),
  };
  return tooltip ? (
    <TooltipLink
      className={className}
      href={href}
      tooltip={tooltip}
      onClick={(event) =>
        followLink(event, () => {
          void navigate(href, { state });
        })
      }
    >
      {children}
    </TooltipLink>
  ) : (
    <Link className={className} to={href} state={state}>
      {children}
    </Link>
  );
}
