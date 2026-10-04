import type { CSSProperties } from "react";

// Identity comes from the project ID, so renaming preserves its color.
export function ProjectBadge({ id, name }: { id: string; name: string }) {
  let hash = 2166136261;
  for (const character of id) {
    hash = Math.imul(hash ^ character.codePointAt(0)!, 16777619) >>> 0;
  }
  const hue = hash % 360;
  const style: CSSProperties = {
    background: `linear-gradient(140deg, hsl(${hue} 52% 42%), hsl(${(hue + 48) % 360} 62% 26%))`,
  };
  return (
    <span className="project-badge" style={style} aria-hidden="true">
      {Array.from(name.trim())[0]?.toLocaleUpperCase() || "·"}
    </span>
  );
}
