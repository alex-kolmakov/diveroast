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

/** DAN articles behind an answer: the ones it cites, then the ones only retrieved. */
export function SourceList({ sources }: { sources?: Source[] | null }) {
  if (!sources || sources.length === 0) return null;
  const cited = sources.filter((s) => s.cited);
  const unused = sources.filter((s) => !s.cited);
  return (
    <div className="mt-2 space-y-2 border-t border-foreground/10 pt-2 text-xs not-prose">
      {cited.length > 0 && (
        <div>
          <span className="font-medium text-primary">Cited from DAN</span>
          <Links sources={cited} />
        </div>
      )}
      {unused.length > 0 && (
        <details className="text-muted-foreground/70">
          <summary className="cursor-pointer hover:text-muted-foreground">
            {unused.length} {cited.length > 0 ? "more" : ""} DAN {unused.length === 1 ? "article" : "articles"} retrieved, not
            cited
          </summary>
          <Links sources={unused} />
        </details>
      )}
    </div>
  );
}
