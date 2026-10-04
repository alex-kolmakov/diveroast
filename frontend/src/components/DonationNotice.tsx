import { useState } from "react";
import { Check, Copy, X } from "lucide-react";
import type { DonationReceipt } from "@/types";

// Shown once after a donated upload. The deletion code is never shown again.
export function DonationNotice({ donation }: { donation: DonationReceipt }) {
  const [hidden, setHidden] = useState(false);
  const [copied, setCopied] = useState(false);
  if (hidden) return null;

  const copy = async () => {
    if (!donation.deletion_code) return;
    try {
      await navigator.clipboard.writeText(donation.deletion_code);
      setCopied(true);
    } catch {
      // clipboard blocked: the code is still on screen to copy by hand
    }
  };

  return (
    <div className="relative rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 pr-10 text-sm text-muted-foreground">
      <button
        type="button"
        onClick={() => setHidden(true)}
        aria-label="Dismiss"
        className="absolute right-2 top-2 rounded p-1 hover:bg-primary/10"
      >
        <X className="h-4 w-4" />
      </button>
      {donation.deletion_code ? (
        <>
          <p>
            Thanks for donating your log. To delete it later, keep this code and
            use it on the <a href="/privacy" className="text-primary underline underline-offset-2">privacy page</a>.
            It is shown only once.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 break-all rounded bg-background px-2 py-1 font-mono text-xs text-foreground">
              {donation.deletion_code}
            </code>
            <button
              type="button"
              onClick={copy}
              className="flex shrink-0 items-center gap-1 rounded border border-border px-2 py-1 text-xs hover:bg-primary/10"
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </>
      ) : (
        <p>
          This log was already donated, so it is stored only once. Thanks!
        </p>
      )}
    </div>
  );
}
