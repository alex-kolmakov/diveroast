import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Flame } from "lucide-react";
import { AnswerMarkdown } from "@/components/AnswerMarkdown";
import { SourceList } from "@/components/SourceList";
import type { ChatMessage, Source } from "@/types";

interface Props {
  messages?: ChatMessage[];
  isLoading?: boolean;
  staticText?: string | null;
  staticSources?: Source[] | null;
}

export function AgentRoastSummary({ messages = [], isLoading = false, staticText, staticSources }: Props) {
  const firstAssistantMsg = messages.find((m) => m.role === "assistant");
  const content = staticText ?? firstAssistantMsg?.content;
  const sources = staticText != null ? staticSources : firstAssistantMsg?.sources;

  return (
    <Card className="border-primary/30">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Flame className="h-4 w-4 text-primary" aria-hidden />
          The Roast
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && !content ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-primary" />
            <span className="text-sm">Generating roast...</span>
          </div>
        ) : content ? (
          <div className="prose prose-sm prose-invert max-w-none leading-relaxed [&>*:first-child]:mt-0">
            <AnswerMarkdown content={content} sources={sources} />
            <SourceList sources={sources} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Waiting for the agent&apos;s roast...
          </p>
        )}
      </CardContent>
    </Card>
  );
}
