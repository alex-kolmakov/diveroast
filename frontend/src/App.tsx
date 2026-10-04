import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { UploadScreen } from "@/components/UploadScreen";
import { AnalyzingScreen } from "@/components/AnalyzingScreen";
import { Dashboard } from "@/components/Dashboard";
import { ChatDrawer } from "@/components/ChatDrawer";
import { useChat } from "@/hooks/useChat";
import { fetchDashboard, fetchSharedDashboard } from "@/services/api";
import type { AppPhase, DashboardData, UploadResponse } from "@/types";

// Detect /shared/:id in the URL at mount time (never changes for the lifetime of the page)
function getSharedId(): string | null {
  const m = window.location.pathname.match(/^\/shared\/([^/]+)/);
  return m?.[1] ?? null;
}

function App() {
  const sharedId = useMemo(getSharedId, []);

  const [phase, setPhase] = useState<AppPhase>(sharedId ? "analyzing" : "upload");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [uploadResponse, setUploadResponse] = useState<UploadResponse | null>(null);
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const roastFired = useRef(false);

  const { messages, isLoading, sendMessage } = useChat(sessionId);

  const handleUploadComplete = useCallback((response: UploadResponse) => {
    setSessionId(response.session_id);
    setUploadResponse(response);
    setPhase("analyzing");
  }, []);

  // Fetch dashboard data when entering analyzing phase
  useEffect(() => {
    if (phase !== "analyzing") return;

    // Shared view: fetch the persisted snapshot, no session needed
    if (sharedId) {
      let cancelled = false;
      fetchSharedDashboard(sharedId)
        .then((data) => {
          if (!cancelled) {
            setDashboardData(data);
            setPhase("dashboard");
          }
        })
        .catch((err) => {
          console.error("Shared dashboard fetch failed:", err);
          if (!cancelled) setPhase("upload"); // fall back to upload on bad share link
        });
      return () => { cancelled = true; };
    }

    // Normal flow: session-based dashboard
    if (!sessionId) return;
    let cancelled = false;
    fetchDashboard(sessionId)
      .then((data) => {
        if (!cancelled) {
          setDashboardData(data);
          setPhase("dashboard");
        }
      })
      .catch((err) => {
        console.error("Dashboard fetch failed:", err);
        if (!cancelled) {
          setPhase("dashboard");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [phase, sessionId, sharedId]);

  // Auto-fire roast message when dashboard mounts (only in live sessions)
  useEffect(() => {
    if (phase !== "dashboard" || !sessionId || sharedId || roastFired.current) return;
    roastFired.current = true;
    sendMessage(
      dashboardData?.mode === "single"
        ? "Analyze this dive and give me a brutally honest roast. Walk through the profile, point out the most dangerous moments and tell me what I need to fix."
        : "Analyze all my dives and give me a brutally honest roast. Highlight the most dangerous moments and tell me what I need to fix."
    );
  }, [phase, sessionId, sharedId, sendMessage, dashboardData?.mode]);

  // The server saves the roast into the shared snapshot itself.

  // Share URL uses the read-only share ID, never the private session ID
  const shareUrl =
    !sharedId && dashboardData?.share_id
      ? `${window.location.origin}/shared/${dashboardData.share_id}`
      : undefined;

  return (
    <div className="h-screen">
      {phase === "upload" && <UploadScreen onUploadComplete={handleUploadComplete} />}

      {phase === "analyzing" && (
        <AnalyzingScreen uploadResponse={uploadResponse} />
      )}

      {phase === "dashboard" && (
        <>
          {dashboardData ? (
            <Dashboard
              data={dashboardData}
              messages={messages}
              isLoading={isLoading}
              onToggleChat={sharedId ? undefined : () => setChatOpen(true)}
              shareUrl={shareUrl}
              readOnly={!!sharedId}
              donation={uploadResponse?.donation}
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-muted-foreground">
                Dashboard data unavailable. Use chat to interact with your dives.
              </p>
            </div>
          )}
          {!sharedId && (
            <ChatDrawer
              open={chatOpen}
              onOpenChange={setChatOpen}
              sessionId={sessionId!}
              // The first exchange is the auto-sent roast request and the roast,
              // which the dashboard already shows.
              messages={messages.slice(2)}
              isLoading={isLoading}
              onSendMessage={sendMessage}
            />
          )}
        </>
      )}
    </div>
  );
}

export default App;
