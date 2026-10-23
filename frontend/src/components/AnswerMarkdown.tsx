import { useId } from "react";
import Markdown from "react-markdown";
import type { Source } from "@/types";

const bare = (url: string) => url.replace(/\/+$/, "");

/** DAN articles the answer links to, numbered by where each is first cited. */
function citations(content: string, sources: Source[]): Source[] {
  const byUrl = new Map(sources.map((s) => [bare(s.url), s]));
  const cited = new Map<string, Source>();
  for (const [, href] of content.matchAll(/\]\(\s*<?([^)\s>]+)>?[^)]*\)/g)) {
    const source = byUrl.get(bare(href));
    if (source && !cited.has(bare(href))) cited.set(bare(href), source);
  }
  return [...cited.values()];
}

/** An answer's markdown with its DAN links numbered like a bibliography: each
 * link keeps its place in the text and gets a [n] pointing to the reference
 * list under the answer, where the full title and address are spelled out. */
export function AnswerMarkdown({ content, sources }: { content: string; sources?: Source[] | null }) {
  const id = useId();
  const refs = citations(content, sources ?? []);
  const number = new Map(refs.map((s, i) => [bare(s.url), i + 1]));
  const anchor = (n: number) => `${id}-ref-${n}`;
  return (
    <>
      <Markdown
        components={{
          a: ({ href, children }) => {
            const n = href ? number.get(bare(href)) : undefined;
            return (
              <>
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={
                    n
                      ? "rounded bg-primary/15 px-1.5 py-0.5 text-xs font-medium text-primary no-underline hover:bg-primary/25"
                      : undefined
                  }
                >
                  {children}
                </a>
                {n && (
                  <sup className="ml-0.5">
                    <a href={`#${anchor(n)}`} className="text-primary no-underline hover:underline">
                      [{n}]
                    </a>
                  </sup>
                )}
              </>
            );
          },
        }}
      >
        {content}
      </Markdown>
      {refs.length > 0 && (
        <div className="mt-3 border-t border-foreground/10 pt-2 text-xs not-prose">
          <span className="font-medium text-primary">References</span>
          <ol className="mt-1 space-y-1">
            {refs.map((s, i) => (
              <li key={s.url} id={anchor(i + 1)} className="flex gap-1.5">
                <span className="text-muted-foreground">[{i + 1}]</span>
                <span>
                  Divers Alert Network{s.year ? ` (${s.year})` : ""}. “{s.title}.”{" "}
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all underline underline-offset-2 hover:text-primary"
                  >
                    {s.url}
                  </a>
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}
