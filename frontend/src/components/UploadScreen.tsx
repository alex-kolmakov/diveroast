import { useState, useCallback } from "react";
import { Waves, Upload } from "lucide-react";
import { uploadDiveLog } from "@/services/api";
import type { UploadResponse } from "@/types";

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
          accept=".ssrf,.xml"
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
            : "Drop your .ssrf or .xml dive log here, or click to browse"}
        </p>
      </div>

      <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
        <input
          type="checkbox"
          checked={donate}
          onChange={(e) => setDonate(e.target.checked)}
          className="h-4 w-4 rounded border-muted accent-primary"
        />
        Donate my dive log to help improve DiveRoast
      </label>

      {error && (
        <p className="text-sm text-danger">{error}</p>
      )}
    </div>
  );
}
