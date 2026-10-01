import type { components } from "./api-schema";

export function UsageSummary({
  usage,
  active = false,
}: {
  usage?: components["schemas"]["Usage"];
  active?: boolean;
}) {
  return (
    <p className="detail-metadata" aria-label="Reported token usage">
      {usage?.total_tokens == null ? (
        "Token usage not reported"
      ) : (
        <>
          {usage.total_tokens.toLocaleString()} tokens reported
          {!usage.complete && (active ? " so far" : " · incomplete")}
          {usage.cached_input_tokens != null && (
            <> · {usage.cached_input_tokens.toLocaleString()} cached input</>
          )}
        </>
      )}
    </p>
  );
}
