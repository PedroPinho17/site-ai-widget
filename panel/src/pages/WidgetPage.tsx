import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useI18n } from "@/i18n/useI18n";
import { ApiRequestError, listKeysRequest, updateWidgetSettingsRequest, widgetSettingsRequest } from "@/lib/api";
import { API_URL } from "@/lib/config";
import { buildPreviewHtml, HEX_COLOR as HEX, PREVIEW_SANDBOX, splitQuestions } from "@/lib/widget-preview";

export function WidgetPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["widget-settings"], queryFn: widgetSettingsRequest });
  const keys = useQuery({ queryKey: ["widget-keys"], queryFn: listKeysRequest });
  const data = settings.data;
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const [primary_color, setColor] = useState("");
  const [position, setPosition] = useState("");
  const [assistant_name, setName] = useState("");
  const [greeting, setGreeting] = useState("");
  const [locale, setLocale] = useState("");
  const [currency, setCurrency] = useState<string | null>(null);
  const [show_suggestions, setShowSuggestions] = useState(true);
  const [show_fullscreen, setShowFullscreen] = useState(true);
  const [allow_visitor_documents, setAllowDocs] = useState(false);
  const [conversation_retention_days, setRetention] = useState(30);
  const [suggested_questions, setQuestions] = useState("");
  const [hydrated, setHydrated] = useState(false);

  // Fill the form once from the first settings response, during render
  // rather than in an effect (no extra render pass).
  if (data && !hydrated) {
    setHydrated(true);
    setColor(data.primary_color);
    setPosition(data.position);
    setName(data.assistant_name);
    setGreeting(data.greeting);
    setLocale(data.locale);
    setCurrency(data.currency);
    setShowSuggestions(data.show_suggestions ?? true);
    setShowFullscreen(data.show_fullscreen ?? true);
    setAllowDocs(data.allow_visitor_documents ?? false);
    setRetention(data.conversation_retention_days ?? 30);
    setQuestions((data.suggested_questions ?? []).join("\n"));
  }

  const previewHtml = useMemo(
    () =>
      buildPreviewHtml({
        apiUrl: API_URL,
        locale,
        primary_color,
        position,
        assistant_name,
        greeting,
        questions: splitQuestions(suggested_questions),
        show_fullscreen,
        show_suggestions,
      }),
    [
      assistant_name,
      greeting,
      locale,
      position,
      primary_color,
      show_fullscreen,
      show_suggestions,
      suggested_questions,
    ],
  );

  const save = useMutation({
    mutationFn: () => {
      if (!HEX.test(primary_color)) {
        throw new ApiRequestError(422, "validation_error", t("widget.colorInvalid"));
      }
      const questions = splitQuestions(suggested_questions);
      if (questions.some((line) => line.length > 80)) {
        throw new ApiRequestError(422, "validation_error", t("widget.questionTooLong"));
      }
      if (!Number.isInteger(conversation_retention_days) || conversation_retention_days < 1 || conversation_retention_days > 365) {
        throw new ApiRequestError(422, "validation_error", t("widget.retentionInvalid"));
      }
      return updateWidgetSettingsRequest({
        primary_color,
        position: position === "bottom-left" ? "bottom-left" : "bottom-right",
        assistant_name,
        greeting,
        locale: locale === "en" || locale === "auto" ? locale : "pt",
        currency: currency || null,
        show_suggestions,
        show_fullscreen,
        allow_visitor_documents,
        conversation_retention_days,
        suggested_questions: questions,
      });
    },
    onSuccess: async () => {
      setError("");
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["widget-settings"] });
    },
    onError: (err: unknown) => {
      setSaved(false);
      setError(err instanceof ApiRequestError ? err.message : t("widget.saveFail"));
    },
  });

  return (
    <section className="grid gap-6 lg:grid-cols-[minmax(0,28rem)_1fr]">
      <div className="flex flex-col gap-4">
        <h1 className="text-xl font-semibold">{t("widget.title")}</h1>
        <label className="flex flex-col gap-1 text-sm">
          {t("widget.color")}
          <Input value={primary_color} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor="position">
          {t("widget.position")}
          <select
            id="position"
            className="rounded-md border border-border bg-card px-3 py-2"
            value={position}
            onChange={(e) => setPosition(e.target.value)}
          >
            <option value="bottom-right">{t("widget.right")}</option>
            <option value="bottom-left">{t("widget.left")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("widget.name")}
          <Input value={assistant_name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("widget.greeting")}
          <Input value={greeting} maxLength={500} onChange={(e) => setGreeting(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor="locale">
          {t("widget.locale")}
          <select
            id="locale"
            className="rounded-md border border-border bg-card px-3 py-2"
            value={locale}
            onChange={(e) => setLocale(e.target.value)}
          >
            <option value="pt">{t("widget.localePt")}</option>
            <option value="en">{t("widget.localeEn")}</option>
            <option value="auto">{t("widget.localeAuto")}</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={show_suggestions} onChange={(e) => setShowSuggestions(e.target.checked)} />
          {t("widget.suggestionsOn")}
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("widget.suggestions")}
          <textarea
            className="min-h-24 rounded-md border border-border bg-card px-3 py-2 text-sm"
            value={suggested_questions}
            onChange={(e) => setQuestions(e.target.value)}
          />
          <span className="text-xs text-muted-foreground">{t("widget.suggestionsHint")}</span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={show_fullscreen} onChange={(e) => setShowFullscreen(e.target.checked)} />
          {t("widget.fullscreen")}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={allow_visitor_documents}
            onChange={(e) => setAllowDocs(e.target.checked)}
          />
          {t("widget.allowDocs")}
        </label>
        <label className="flex flex-col gap-1 text-sm" htmlFor="currency">
          {t("widget.currency")}
          <select
            id="currency"
            className="rounded-md border border-border bg-card px-3 py-2"
            value={currency ?? ""}
            onChange={(e) => setCurrency(e.target.value || null)}
          >
            <option value="">{t("widget.currencyNone")}</option>
            <option value="EUR">EUR</option>
            <option value="USD">USD</option>
            <option value="GBP">GBP</option>
            <option value="BRL">BRL</option>
          </select>
        </label>
        <Label htmlFor="retention">{t("widget.retention")}</Label>
        <Input
          id="retention"
          type="number"
          min={1}
          max={365}
          value={conversation_retention_days}
          onChange={(e) => setRetention(Number(e.target.value))}
        />
        {saved ? (
          <p role="status" className="text-sm text-green-800">
            {t("widget.saved")}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        ) : null}
        <Button type="button" onClick={() => save.mutate()} disabled={save.isPending || settings.isLoading}>
          {t("widget.save")}
        </Button>
        {keys.data?.length ? (
          <p className="text-xs text-muted-foreground">{keys.data[0].public_key.slice(0, 10)}…</p>
        ) : null}
      </div>
      <div>
        <h2 className="mb-2 text-sm font-medium">{t("widget.preview")}</h2>
        {/* Opaque-origin sandbox: the preview can never read the panel JWT. */}
        <iframe
          title={t("widget.preview")}
          className="h-[32rem] w-full rounded-lg border border-border bg-slate-50"
          sandbox={PREVIEW_SANDBOX}
          referrerPolicy="no-referrer"
          srcDoc={previewHtml}
        />
      </div>
    </section>
  );
}
