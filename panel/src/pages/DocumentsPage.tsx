import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { PdfPreview } from "@/components/PdfPreview";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useI18n } from "@/i18n/useI18n";
import type { MessageKey } from "@/i18n/messages";
import {
  ApiRequestError,
  deleteDocumentRequest,
  listDocumentsRequest,
  messageForCode,
  uploadDocumentWithProgress,
  type DocumentItem,
} from "@/lib/api";
import { checkUploadFile, formatBytes, isPdfName, MAX_FILES_PER_DROP, MAX_UPLOAD_MB } from "@/lib/documents";

const PROCESSING = new Set(["pending", "processing"]);
const POLL_MS = 3000;

type UploadRow = {
  id: string;
  name: string;
  progress: number;
  status: "uploading" | "error";
  error?: string;
};

function formatDate(value: string | null): string {
  if (!value) {
    return "—";
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    return "—";
  }
  return d.toLocaleString();
}

function statusKey(status: string): MessageKey {
  if (status === "pending") {
    return "docs.status.pending";
  }
  if (status === "processing") {
    return "docs.status.processing";
  }
  if (status === "ready") {
    return "docs.status.ready";
  }
  return "docs.status.failed";
}

export function DocumentsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [selected, setSelected] = useState<DocumentItem | null>(null);
  const [uploads, setUploads] = useState<UploadRow[]>([]);

  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: listDocumentsRequest,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((doc) => PROCESSING.has(doc.status)) ? POLL_MS : false,
  });

  const remove = useMutation({
    mutationFn: deleteDocumentRequest,
    onSuccess: async () => {
      setDeleteId(null);
      setError("");
      setNotice(t("docs.deleted"));
      setSelected(null);
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : t("docs.deleteFail"));
    },
  });

  async function tryUploadMany(fileList: FileList | File[] | null) {
    if (!fileList || !isAdmin) {
      return;
    }
    const files = Array.from(fileList);
    if (files.length > MAX_FILES_PER_DROP) {
      // Refuse the whole drop rather than silently uploading only part of it.
      setNotice("");
      setError(t("docs.tooMany", { n: MAX_FILES_PER_DROP }));
      return;
    }
    for (const file of files) {
      const code = checkUploadFile(file);
      if (code) {
        setNotice("");
        setError(messageForCode(code));
        continue;
      }
      const localId = `${file.name}-${file.size}-${file.lastModified}`;
      setUploads((current) => [...current, { id: localId, name: file.name, progress: 0, status: "uploading" }]);
      try {
        await uploadDocumentWithProgress(file, (pct) => {
          setUploads((current) => current.map((row) => (row.id === localId ? { ...row, progress: pct } : row)));
        });
        setUploads((current) => current.filter((row) => row.id !== localId));
        setError("");
        setNotice(t("docs.uploaded"));
        await queryClient.invalidateQueries({ queryKey: ["documents"] });
      } catch (err) {
        const message = err instanceof ApiRequestError ? err.message : messageForCode("network_error");
        setUploads((current) =>
          current.map((row) => (row.id === localId ? { ...row, status: "error", error: message } : row)),
        );
        setNotice("");
        setError(message);
      }
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void tryUploadMany(event.dataTransfer.files);
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">{t("docs.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("docs.subtitle", { n: MAX_UPLOAD_MB })}</p>
      </div>
      {notice ? <p className="text-sm text-primary">{notice}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-[1fr_minmax(18rem,24rem)]">
        <div className="flex flex-col gap-4">
          {isAdmin ? (
            <div
              className={`rounded-lg border border-dashed p-6 text-center ${
                dragging ? "border-primary bg-muted" : "border-border bg-card"
              }`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <p className="text-sm text-muted-foreground">{t("docs.drop")}</p>
              <div className="mt-3">
                <input
                  ref={inputRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  multiple
                  className="sr-only"
                  onChange={(event) => {
                    void tryUploadMany(event.target.files);
                    event.target.value = "";
                  }}
                />
                <Button type="button" variant="outline" onClick={() => inputRef.current?.click()}>
                  {uploads.some((row) => row.status === "uploading") ? t("docs.uploading") : t("docs.choose")}
                </Button>
              </div>
            </div>
          ) : null}
          {uploads.map((row) => (
            <Card key={row.id}>
              <p className="text-sm font-medium">{row.name}</p>
              <p className="text-xs text-muted-foreground">
                {row.status === "uploading" ? `${row.progress}%` : row.error}
              </p>
            </Card>
          ))}
          {docs.isLoading ? <p className="text-sm text-muted-foreground">{t("docs.loading")}</p> : null}
          {docs.error instanceof ApiRequestError ? (
            <p role="alert" className="text-sm text-destructive">
              {docs.error.message}
            </p>
          ) : null}
          {(docs.data ?? []).map((doc) => (
            <DocumentRow
              key={doc.id}
              item={doc}
              isAdmin={isAdmin}
              selected={selected?.id === doc.id}
              statusLabel={t(statusKey(doc.status))}
              onSelect={() => setSelected(doc)}
              onDelete={() => setDeleteId(doc.id)}
            />
          ))}
          {docs.data && docs.data.length === 0 ? (
            <Card>
              <p className="text-sm text-muted-foreground">{t("docs.empty")}</p>
            </Card>
          ) : null}
        </div>
        <Card>
          <PdfPreview
            key={selected?.id ?? "none"}
            documentId={selected?.id ?? null}
            page={1}
            pageCount={selected?.page_count}
          />
        </Card>
      </div>
      <Dialog open={deleteId !== null} onOpenChange={(open) => !open && setDeleteId(null)}>
        <DialogContent>
          <DialogTitle>{t("docs.deleteTitle")}</DialogTitle>
          <DialogDescription>{t("docs.deleteBody")}</DialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setDeleteId(null)}>
              {t("docs.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={remove.isPending || !deleteId}
              onClick={() => deleteId && remove.mutate(deleteId)}
            >
              {t("docs.delete")}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DocumentRow({
  item,
  isAdmin,
  selected,
  statusLabel,
  onSelect,
  onDelete,
}: {
  item: DocumentItem;
  isAdmin: boolean;
  selected: boolean;
  statusLabel: string;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  return (
    <Card className={selected ? "ring-2 ring-primary" : undefined}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <button type="button" className="text-left" onClick={onSelect}>
          <CardTitle className="text-base">{item.original_name}</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {isPdfName(item.original_name) ? t("docs.typePdf") : t("docs.typeFile")} · {formatBytes(item.size_bytes)} · {statusLabel} ·{" "}
            {formatDate(item.created_at)}
          </p>
        </button>
        {isAdmin ? (
          <Button type="button" variant="destructive" size="sm" onClick={onDelete}>
            {t("docs.delete")}
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
