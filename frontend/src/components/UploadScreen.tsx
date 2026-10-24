import { useState, useCallback } from "react";
import { Waves, Upload } from "lucide-react";
import { uploadDiveLog } from "@/services/api";
import { RETENTION_DAYS } from "@/lib/consent";
import type { UploadResponse } from "@/types";

const EXTENSIONS = [".ssrf", ".xml", ".uddf", ".fit", ".zip"];
// Phone pickers filter by MIME type, and .ssrf, .uddf and .fit have none, so
// an `accept` list greys those files out. Phones get no filter; the
// extension is checked once a file is picked.
const PICKER_ACCEPT = window.matchMedia("(pointer: coarse)").matches ? undefined : EXTENSIONS.join(",");

interface Props {
  onUploadComplete: (response: UploadResponse) => void;
}

export function UploadScreen({ onUploadComplete }: Props) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [donate, setDonate] = useState(false);

  const handleFile = useCallback(
    async (file: File) => {
      if (!EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
        setError(`${file.name} isn't a dive log DiveRoast can read. Pick a Subsurface .ssrf, UDDF, Garmin .fit or .zip file.`);
        return;
      }
      setIsUploading(true);
      setError(null);
      try {
        const result = await uploadDiveLog(file, undefined, donate);
        onUploadComplete(result);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setIsUploading(false);
      }
    },
    [onUploadComplete, donate]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      // Cleared so picking the same file again after an error fires again
      e.target.value = "";
      if (file) handleFile(file);
    },
    [handleFile]
  );

  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 px-4">
      <div className="text-center">
        <div className="mb-4 flex items-center justify-center gap-3">
          <Waves className="h-10 w-10 text-primary" />
          <h1 className="text-4xl font-bold tracking-tight">DiveRoast</h1>
        </div>
        <p className="text-muted-foreground">
          Upload your dive log and prepare to get roasted
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => document.getElementById("file-input")?.click()}
        className={`flex w-full max-w-md cursor-pointer flex-col items-center gap-4 rounded-xl border-2 border-dashed p-12 transition-all ${
          isDragging
            ? "border-primary bg-primary/10"
            : "border-muted-foreground/30 hover:border-primary/50"
        }`}
      >
        <input
          id="file-input"
          type="file"
          accept={PICKER_ACCEPT}
          onChange={handleChange}
          className="hidden"
        />
        {isUploading ? (
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-muted border-t-primary" />
        ) : (
          <Upload className="h-10 w-10 text-muted-foreground" />
        )}
        <p className="text-center text-muted-foreground">
          {isUploading
            ? "Parsing dive log..."
            : "Drop a dive log here (Subsurface .ssrf, UDDF, a Garmin .fit dive, or a .zip of them), or click to browse"}
        </p>
      </div>

      {/* Same width as the drop zone, so the fine print doesn't run across the screen */}
      <label className="flex w-full max-w-md cursor-pointer items-start gap-3 text-sm text-muted-foreground">
        <input
          type="checkbox"
          checked={donate}
          onChange={(e) => setDonate(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-muted accent-primary"
        />
        <span className="space-y-1">
          <span className="block text-foreground/80">
            Donate my dive log to help improve DiveRoast
          </span>
          <span className="block text-xs leading-relaxed">
            Names and notes are removed first; kept {RETENTION_DAYS} days.{" "}
            <a
              href="/privacy"
              onClick={(e) => e.stopPropagation()}
              className="text-primary underline underline-offset-2"
            >
              What's kept
            </a>
          </span>
        </span>
      </label>

      {error && (
        <p className="text-sm text-danger">{error}</p>
      )}
    </div>
  );
}
