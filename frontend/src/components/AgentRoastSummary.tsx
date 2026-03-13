import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MessageSquare } from "lucide-react";
import Markdown from "react-markdown";
import type { ChatMessage } from "@/types";

interface Props {
  messages?: ChatMessage[];
  isLoading?: boolean;
  staticText?: string | null;
}

export function AgentRoastSummary({ messages = [], isLoading = false, staticText }: Props) {
  const firstAssistantMsg = messages.find((m) => m.role === "assistant");
  const content = staticText ?? firstAssistantMsg?.content;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageSquare className="h-4 w-4" />
          Agent Roast
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && !content ? (
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-primary" />
            <span className="text-sm">Generating roast...</span>
          </div>
        ) : content ? (
          <div className="prose prose-sm prose-invert max-w-none leading-relaxed">
            <Markdown>{content}</Markdown>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Waiting for the agent to roast your dives...
          </p>
        )}
      </CardContent>
    </Card>
  );
}
