import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import {
  ApiRequestError,
  deleteDocumentRequest,
  listDocumentsRequest,
  messageForCode,
  uploadDocumentRequest,
  type DocumentItem,
} from "@/lib/api";
import { checkUploadFile, documentTypeLabel, formatBytes, MAX_UPLOAD_MB } from "@/lib/documents";

const PROCESSING = new Set(["pending", "processing"]);
const POLL_MS = 3000;

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

export function DocumentsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: listDocumentsRequest,
    // Keep polling while a file is still being processed so its status updates.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((doc) => PROCESSING.has(doc.status)) ? POLL_MS : false,
  });

  const upload = useMutation({
    mutationFn: uploadDocumentRequest,
    onSuccess: async () => {
      setError("");
      setNotice("Upload started.");
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : messageForCode("network_error"));
    },
  });

  const remove = useMutation({
    mutationFn: deleteDocumentRequest,
    onSuccess: async () => {
      setDeleteId(null);
      setError("");
      setNotice("Document deleted.");
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : "Could not delete the document.");
    },
  });

  function tryUpload(file: File | undefined) {
    if (!file || !isAdmin) {
      return;
    }
    const code = checkUploadFile(file);
    if (code) {
      setNotice("");
      setError(messageForCode(code));
      return;
    }
    setError("");
    upload.mutate(file);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    tryUpload(event.dataTransfer.files[0]);
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Documents</h1>
        <p className="text-sm text-muted-foreground">
          PDFs the widget can search. Maximum {MAX_UPLOAD_MB} MB per file.
        </p>
      </div>
      {notice ? <p className="text-sm text-primary">{notice}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
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
          <p className="text-sm text-muted-foreground">Drag a PDF here, or choose a file.</p>
          <div className="mt-3">
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              onChange={(event) => {
                tryUpload(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={upload.isPending}
              onClick={() => inputRef.current?.click()}
            >
              {upload.isPending ? "Uploading…" : "Choose PDF"}
            </Button>
          </div>
        </div>
      ) : null}
      {docs.isLoading ? <p className="text-sm text-muted-foreground">Loading documents…</p> : null}
      {docs.error instanceof ApiRequestError ? (
        <p role="alert" className="text-sm text-destructive">
          {docs.error.message}
        </p>
      ) : null}
      {(docs.data ?? []).map((doc) => (
        <DocumentRow key={doc.id} item={doc} isAdmin={isAdmin} onDelete={() => setDeleteId(doc.id)} />
      ))}
      {docs.data && docs.data.length === 0 ? (
        <Card>
          <p className="text-sm text-muted-foreground">No documents yet.</p>
        </Card>
      ) : null}
      <Dialog open={deleteId !== null} onOpenChange={(open) => !open && setDeleteId(null)}>
        <DialogContent>
          <DialogTitle>Delete this document?</DialogTitle>
          <DialogDescription>
            The file and its chunks will be removed. This cannot be undone.
          </DialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setDeleteId(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={remove.isPending || !deleteId}
              onClick={() => deleteId && remove.mutate(deleteId)}
            >
              Delete
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
  onDelete,
}: {
  item: DocumentItem;
  isAdmin: boolean;
  onDelete: () => void;
}) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle className="text-base">{item.original_name}</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {documentTypeLabel(item.original_name)} · {formatBytes(item.size_bytes)} · {item.status} ·{" "}
            {formatDate(item.created_at)}
          </p>
        </div>
        {isAdmin ? (
          <Button type="button" variant="destructive" size="sm" onClick={onDelete}>
            Delete
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
