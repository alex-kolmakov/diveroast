import Markdown from "react-markdown";
import type { Source } from "@/types";

const bare = (url: string) => url.replace(/\/+$/, "");

/** An answer's markdown, with links to retrieved DAN articles marked as citations. */
export function AnswerMarkdown({ content, sources }: { content: string; sources?: Source[] | null }) {
  const danUrls = new Set((sources ?? []).map((s) => bare(s.url)));
  return (
    <Markdown
      components={{
        a: ({ href, children }) => {
          const isDan = !!href && danUrls.has(bare(href));
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className={
                isDan
                  ? "rounded bg-primary/15 px-1.5 py-0.5 text-xs font-medium text-primary no-underline hover:bg-primary/25"
                  : undefined
              }
            >
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </Markdown>
  );
}
