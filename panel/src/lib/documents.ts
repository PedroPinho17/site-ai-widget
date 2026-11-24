/** Client-side limits mirrored from the API (MAX_UPLOAD_MB default, PDF only). */
export const MAX_UPLOAD_MB = 25;
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

const PDF_TYPES = new Set(["application/pdf"]);

export type DocumentCheckError = "not_pdf" | "upload_too_large";

export function checkUploadFile(file: File): DocumentCheckError | null {
  const name = file.name.toLowerCase();
  const typeOk = PDF_TYPES.has(file.type) || name.endsWith(".pdf");
  if (!typeOk) {
    return "not_pdf";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "upload_too_large";
  }
  return null;
}

export function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function documentTypeLabel(name: string): string {
  return name.toLowerCase().endsWith(".pdf") ? "PDF" : "File";
}
