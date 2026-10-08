import { authorLabel } from "./workspace";
import { Disclosure } from "./DetailLayout";
import { Timestamp } from "./Timestamp";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useState } from "react";
import { Markdown } from "./Markdown";
import { request, type ActivityEntry } from "./workspace";

export function RelatedActivity({
  path,
  id,
  label,
}: {
  path: string;
  id: string;
  label: string;
}) {
  const [entry, setEntry] = useState<ActivityEntry | null>(null);
  const [error, setError] = useState("");
  return (
    <Disclosure
      summary={<>{label}</>}
      className="related-decision history-disclosure"
      onToggle={(e) => {
        if (e.currentTarget.open && !entry)
          void request<ActivityEntry>(`${path}/${encodeURIComponent(id)}`)
            .then(setEntry)
            .catch((e: Error) => setError(e.message));
      }}
    >
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {entry ? (
        <>
          <p className="detail-metadata">
            {authorLabel(entry.author)} · <Timestamp date={entry.created_at} />
          </p>
          <Markdown>{entry.body}</Markdown>
        </>
      ) : (
        <p>Loading entry…</p>
      )}
    </Disclosure>
  );
}
