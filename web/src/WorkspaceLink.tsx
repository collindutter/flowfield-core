import { Link, useLocation, type LinkProps } from "react-router";
import { entityNavigationState } from "./navigation";

/** Native link semantics with the same overlay return path as button navigation. */
export function WorkspaceLink({
  to,
  ...props
}: Omit<LinkProps, "to" | "state"> & { to: string }) {
  const location = useLocation();
  return (
    <Link
      {...props}
      to={to}
      state={entityNavigationState(location.pathname, location.state, to)}
    />
  );
}
