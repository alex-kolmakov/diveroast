import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MessageCircle, Share2, Waves } from "lucide-react";

interface Props {
  /** What the page is about: "144 dives" or "Dive #529 · Elphinstone". */
  subject: string;
  onToggleChat?: () => void;
  shareUrl?: string;
  readOnly?: boolean;
}

export function DashboardHeader({ subject, onToggleChat, shareUrl, readOnly }: Props) {
  const [copied, setCopied] = useState(false);

  const handleShare = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3">
        <Waves className="h-7 w-7 shrink-0 text-primary" />
        <h1 className="text-2xl font-bold tracking-tight">DiveRoast</h1>
        <Badge variant="secondary" className="min-w-0 text-sm">
          <span className="truncate">{subject}</span>
        </Badge>
        {readOnly && (
          <Badge variant="outline" className="text-xs text-muted-foreground">
            Shared view
          </Badge>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
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
