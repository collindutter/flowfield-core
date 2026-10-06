export function CountBadge({ count, label }: { count: number; label: string }) {
  return count > 0 ? (
    <span className="icon-count" aria-label={`${label}: ${count}`}>
      {count > 99 ? "99+" : count}
    </span>
  ) : null;
}
