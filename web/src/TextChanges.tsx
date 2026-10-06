import { useMemo } from "react";
import { diffWordsWithSpace } from "diff";

export function TextChanges({
  before,
  after,
}: {
  before: string;
  after: string;
}) {
  const changes = useMemo(
    () =>
      diffWordsWithSpace(before, after, { timeout: 50, maxEditLength: 4000 }),
    [before, after],
  );
  return (
    <div className="text-changes" aria-label="Text changes">
      <span className="sr-only">
        Deletions are struck through; additions are underlined.
      </span>
      {(
        changes ?? [
          { value: before, removed: true },
          { value: after, added: true },
        ]
      ).map((change, index) =>
        change.removed ? (
          <del key={index}>{change.value}</del>
        ) : change.added ? (
          <ins key={index}>{change.value}</ins>
        ) : (
          <span key={index}>{change.value}</span>
        ),
      )}
    </div>
  );
}
