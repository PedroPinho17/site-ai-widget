import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n/useI18n";
import { documentFileBlob } from "@/lib/api";

/** Authenticated PDF preview: the file is fetched with the panel JWT in a
 *  header and shown from a blob: URL, so the token never appears in a URL or
 *  reaches the frame. Callers pass `key={id + page}` to reset the page. */
export function PdfPreview({
  documentId,
  page = 1,
  pageCount,
}: {
  documentId: string | null;
  page?: number;
  pageCount?: number | null;
}) {
  const { t } = useI18n();
  const [loaded, setLoaded] = useState<{ id: string; url: string } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [current, setCurrent] = useState(page > 0 ? page : 1);

  useEffect(() => {
    if (!documentId) {
      return;
    }
    let objectUrl = "";
    let cancelled = false;
    documentFileBlob(documentId)
      .then((blob) => {
        if (cancelled) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setLoaded({ id: documentId, url: objectUrl });
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(documentId);
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [documentId]);

  if (!documentId) {
    return <p className="text-sm text-muted-foreground">{t("docs.noPreview")}</p>;
  }

  const url = loaded && loaded.id === documentId ? loaded.url : "";
  const maxPage = pageCount && pageCount > 0 ? pageCount : Number.MAX_SAFE_INTEGER;
  const src = url ? `${url}#page=${current}` : "";

  return (
    <div className="flex h-full min-h-[24rem] flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{t("docs.preview")}</span>
        <Button type="button" variant="outline" size="sm" disabled={current <= 1} onClick={() => setCurrent((n) => n - 1)}>
          {t("docs.prev")}
        </Button>
        <span className="text-sm">
          {t("docs.page")} {current}
          {pageCount ? ` / ${pageCount}` : ""}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={current >= maxPage}
          onClick={() => setCurrent((n) => n + 1)}
        >
          {t("docs.next")}
        </Button>
      </div>
      {src ? (
        <iframe
          key={src}
          title={t("docs.preview")}
          className="min-h-[24rem] w-full flex-1 rounded-md border border-border bg-muted"
          src={src}
        />
      ) : failed === documentId ? (
        <p role="alert" className="text-sm text-destructive">
          {t("docs.previewFail")}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      )}
    </div>
  );
}
