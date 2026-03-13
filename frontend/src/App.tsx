import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { UploadScreen } from "@/components/UploadScreen";
import { AnalyzingScreen } from "@/components/AnalyzingScreen";
import { Dashboard } from "@/components/Dashboard";
import { ChatDrawer } from "@/components/ChatDrawer";
import { useChat } from "@/hooks/useChat";
import { fetchDashboard, fetchSharedDashboard, saveRoastSummary } from "@/services/api";
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
  const roastSaved = useRef(false);

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
      "Analyze all my dives and give me a brutally honest roast. Highlight the most dangerous moments and tell me what I need to fix."
    );
  }, [phase, sessionId, sharedId, sendMessage]);

  // Save roast to snapshot once streaming finishes (enables shared-view display)
  useEffect(() => {
    if (!sessionId || sharedId || roastSaved.current || isLoading) return;
    const firstAssistant = messages.find((m) => m.role === "assistant");
    if (!firstAssistant?.content) return;
    roastSaved.current = true;
    saveRoastSummary(sessionId, firstAssistant.content);
  }, [isLoading, messages, sessionId, sharedId]);

  // Build share URL from current session (shown as a button in the dashboard header)
  const shareUrl = sessionId
    ? `${window.location.origin}/shared/${sessionId}`
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
              messages={messages}
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
