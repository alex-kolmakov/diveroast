import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MessageCircle, Share2, Waves } from "lucide-react";
import type { AggregateStats } from "@/types";

interface Props {
  stats: AggregateStats;
  onToggleChat?: () => void;
  shareUrl?: string;
  readOnly?: boolean;
}

export function DashboardHeader({ stats, onToggleChat, shareUrl, readOnly }: Props) {
  const [copied, setCopied] = useState(false);

  const handleShare = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-3">
        <Waves className="h-7 w-7 text-primary" />
        <h1 className="text-2xl font-bold tracking-tight">DiveRoast</h1>
        <Badge variant="secondary" className="text-sm">
          {stats.total_dives} dives
        </Badge>
        {readOnly && (
          <Badge variant="outline" className="text-xs text-muted-foreground">
            Shared view
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-2">
        {shareUrl && (
          <Button variant="outline" size="sm" onClick={handleShare}>
            <Share2 className="mr-2 h-4 w-4" />
            {copied ? "Copied!" : "Share"}
          </Button>
        )}
        {!readOnly && onToggleChat && (
          <Button variant="outline" size="sm" onClick={onToggleChat}>
            <MessageCircle className="mr-2 h-4 w-4" />
            Chat
          </Button>
        )}
      </div>
    </div>
  );
}
