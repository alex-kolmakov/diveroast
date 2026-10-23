import type { Source } from "@/types";

function Links({ sources }: { sources: Source[] }) {
  return (
    <ul className="mt-1 space-y-0.5">
      {sources.map((s) => (
        <li key={s.url}>
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-primary"
          >
            {s.title}
          </a>
        </li>
      ))}
    </ul>
  );
}

/** DAN articles retrieved for an answer but not cited in it. The cited ones
 * are already links in the answer itself; listing them again repeats them. */
export function SourceList({ sources }: { sources?: Source[] | null }) {
  const unused = (sources ?? []).filter((s) => !s.cited);
  if (unused.length === 0) return null;
  return (
    <div className="mt-2 border-t border-foreground/10 pt-2 text-xs not-prose">
      <details className="text-muted-foreground/70">
        <summary className="cursor-pointer hover:text-muted-foreground">
          {unused.length} more DAN {unused.length === 1 ? "article" : "articles"} retrieved, not cited
        </summary>
        <Links sources={unused} />
      </details>
    </div>
  );
}
