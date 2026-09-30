import Markdown from "react-markdown";
import type { ChatMessage } from "@/types";

interface Props {
  message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`mb-3 flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[70%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-secondary text-secondary-foreground"
        }`}
      >
        {isUser ? (
          <span className="whitespace-pre-wrap">{message.content || "..."}</span>
        ) : (
          <div className="prose prose-sm prose-invert max-w-none">
            <Markdown>{message.content || "..."}</Markdown>
            {message.sources && message.sources.length > 0 && (
              <div className="mt-2 border-t border-foreground/10 pt-2 text-xs not-prose">
                <span className="text-muted-foreground">DAN sources:</span>
                <ul className="mt-1 space-y-0.5">
                  {message.sources.map((s) => (
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
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
