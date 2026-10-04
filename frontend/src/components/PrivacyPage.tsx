import { useState } from "react";
import { Waves } from "lucide-react";
import { deleteDonation } from "@/services/api";
import { CONSENT_VERSION, RETENTION_DAYS } from "@/lib/consent";

// Plain statement of what DiveRoast keeps. Changing the donation part means
// bumping CONSENT_VERSION here and in src/storage/donations.py.
export function PrivacyPage() {
  const [code, setCode] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setState("busy");
    try {
      await deleteDonation(code);
      setState("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
      setState("error");
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <article className="mx-auto max-w-2xl space-y-6 px-4 py-10 text-sm leading-relaxed text-muted-foreground">
        <a href="/" className="flex items-center gap-2 text-foreground">
          <Waves className="h-6 w-6 text-primary" />
          <span className="text-lg font-semibold">DiveRoast</span>
        </a>
        <header>
          <h1 className="text-2xl font-bold text-foreground">Privacy</h1>
          <p className="text-xs">Version {CONSENT_VERSION}</p>
        </header>

        <section className="space-y-2">
          <h2 className="text-base font-semibold text-foreground">Every upload</h2>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Your log is parsed on the DiveRoast server. The file isn't kept unless you
              donate it. The parsed dives stay in server memory until 6 hours after your
              last action, then they are dropped.
            </li>
            <li>
              To write the roast, a summary of your dives (site and trip names, depths,
              ascent rates, air use, temperatures) and your chat messages are sent to
              Google's Gemini API.
            </li>
            <li>
              The server keeps traces of those calls (the summary, your messages and the
              answers) to debug and improve the roasts. Traces are deleted after 30
              days.
            </li>
            <li>
              When something breaks, an error report goes to Sentry: the error and
              where in the code it happened. It doesn't include your log, your messages
              or your IP address.
            </li>
            <li>
              Map thumbnails on the dashboard load from OpenStreetMap, which sees the
              coordinates of the dive sites shown.
            </li>
            <li>No cookies, analytics or ads.</li>
          </ul>
        </section>

        <section className="space-y-2">
          <h2 className="text-base font-semibold text-foreground">Shared links</h2>
          <p>
            When your dashboard opens, a read-only copy is saved under a random link so
            you can share it: dive numbers, site names and positions, the metrics and the
            roast. Anyone with the link can see it; your chat is not included. Shared
            links are deleted {RETENTION_DAYS} days after they were last updated.
          </p>
        </section>

        <section className="space-y-2">
          <h2 className="text-base font-semibold text-foreground">
            Donating your log (only if you tick the box)
          </h2>
          <p>
            A donated log is used only to improve DiveRoast: reading more dive computers
            correctly and testing how good the roasts are.
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <span className="text-foreground">Removed before it is stored:</span> buddy,
              divemaster and owner names, notes, photo paths, device serial numbers, body
              data such as weight and height, and the file name.
            </li>
            <li>
              <span className="text-foreground">Kept:</span> the dive profiles, gases,
              dates and times, dive site names and GPS positions, and the roast the log
              got.
            </li>
            <li>Deleted {RETENTION_DAYS} days after you donate it.</li>
          </ul>
        </section>

        <section className="space-y-3">
          <h2 className="text-base font-semibold text-foreground">Delete a donated log</h2>
          <p>Enter the deletion code shown after your upload.</p>
          {state === "done" ? (
            <p className="text-foreground">Deleted. Nothing of that log is left on the server.</p>
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Deletion code"
                maxLength={128}
                className="min-w-0 flex-1 rounded-lg border border-input bg-secondary px-3 py-2 font-mono text-xs text-foreground"
              />
              <button
                type="submit"
                disabled={!code.trim() || state === "busy"}
                className="rounded-lg bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50"
              >
                Delete
              </button>
            </form>
          )}
          {state === "error" && <p className="text-danger">{error}</p>}
          <p>
            Lost the code? Open an issue on{" "}
            <a
              href="https://github.com/alex-kolmakov/diveroast/issues"
              className="text-primary underline underline-offset-2"
            >
              GitHub
            </a>{" "}
            with the day you uploaded and the number of dives, and it will be removed.
          </p>
        </section>
      </article>
    </div>
  );
}
