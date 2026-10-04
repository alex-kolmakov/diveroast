import * as Sentry from "@sentry/react";

// Session ids are credentials and share ids are private links: never send
// them. The dashboard API path carries the session id.
export function scrubUrl(url: string): string {
  return url
    .replace(/(\/api\/dashboard\/)[^/?#]+/g, "$1:session")
    .replace(/(\/shared\/)[^/?#]+/g, "$1:share");
}

// Errors only, when VITE_SENTRY_DSN is set at build time. No performance
// tracing, no session replay, no IPs; request bodies (logs, chat messages,
// deletion codes) are never attached by the SDK's fetch breadcrumbs.
export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeBreadcrumb(breadcrumb) {
      if (typeof breadcrumb.data?.url === "string") {
        breadcrumb.data.url = scrubUrl(breadcrumb.data.url);
      }
      return breadcrumb;
    },
    beforeSend(event) {
      if (event.request?.url) event.request.url = scrubUrl(event.request.url);
      return event;
    },
  });
}
