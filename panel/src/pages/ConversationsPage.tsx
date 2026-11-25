import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { Locale, MessageKey } from "@/i18n/messages";
import { useI18n } from "@/i18n/useI18n";
import { ApiRequestError, getConversationRequest, listConversationsRequest } from "@/lib/api";
import { SafeMarkdown } from "@/lib/safe-markdown";

const SOURCE_KEYS: Record<string, MessageKey> = {
  sql: "source.sql",
  docs: "source.docs",
  none: "source.none",
  chat: "source.chat",
};

function sourceLabel(mode: string | null, t: (key: MessageKey) => string): string {
  return mode && SOURCE_KEYS[mode] ? t(SOURCE_KEYS[mode]) : "—";
}

function langLabel(lang: string | null, t: (key: MessageKey) => string): string {
  if (lang === "pt") {
    return t("conversations.lang.pt");
  }
  if (lang === "en") {
    return t("conversations.lang.en");
  }
  return "—";
}

function formatWhen(value: string | null, locale: Locale): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(locale === "pt" ? "pt-PT" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function ConversationsPage() {
  const { t, locale } = useI18n();
  const [unanswered, setUnanswered] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const badRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);
  const list = useQuery({
    enabled: !badRange,
    queryKey: ["conversations", unanswered, dateFrom, dateTo],
    queryFn: () =>
      listConversationsRequest({
        unanswered,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      }),
  });

  const detail = useQuery({
    queryKey: ["conversation", openId],
    queryFn: () => getConversationRequest(openId!),
    enabled: Boolean(openId),
  });

  if (openId) {
    return (
      <section className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <Button type="button" variant="outline" onClick={() => setOpenId(null)}>
            {t("conversations.back")}
          </Button>
          <h1 className="text-xl font-semibold">{t("conversations.title")}</h1>
        </div>
        {detail.isLoading ? <p>{t("conversations.loading")}</p> : null}
        {detail.error instanceof ApiRequestError ? (
          <p role="alert" className="text-sm text-destructive">
            {detail.error.message}
          </p>
        ) : null}
        {(detail.data?.messages ?? []).map((message, index) => (
          <Card key={`${message.created_at}-${index}`}>
            <p className="text-xs text-muted-foreground">
              {message.role === "user" ? t("conversations.role.user") : t("conversations.role.assistant")}
              {message.role === "assistant" ? ` · ${sourceLabel(message.mode, t)}` : ""}
              {message.unanswered ? ` · ${t("conversations.unanswered")}` : ""}
              {` · ${formatWhen(message.created_at, locale)}`}
            </p>
            {/* Same rendering as Test assistant: stored answers keep citation marks
                (【1】, [1]) that SafeMarkdown strips; visitor text stays verbatim. */}
            {message.role === "assistant" ? (
              <div className="mt-1">
                <SafeMarkdown text={message.text ?? ""} />
              </div>
            ) : (
              <p className="mt-1 whitespace-pre-wrap text-sm">{message.text}</p>
            )}
          </Card>
        ))}
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">{t("conversations.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("conversations.subtitle")}</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t("conversations.from")}
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("conversations.to")}
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={unanswered} onChange={(e) => setUnanswered(e.target.checked)} />
          {t("conversations.unanswered")}
        </label>
      </div>
      {badRange ? (
        <p role="alert" className="text-sm text-destructive">
          {t("conversations.badRange")}
        </p>
      ) : null}
      {list.isLoading && !badRange ? <p>{t("conversations.loading")}</p> : null}
      {list.error instanceof ApiRequestError ? (
        <p role="alert" className="text-sm text-destructive">
          {list.error.message}
        </p>
      ) : null}
      {(badRange ? [] : (list.data ?? [])).map((item) => (
        <Card key={item.id}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">{formatWhen(item.last_seen ?? item.created_at, locale)}</CardTitle>
              <p className="text-sm text-muted-foreground">
                {langLabel(item.lang, t)} · {item.message_count} {t("conversations.messages")} · {t("conversations.source")}:{" "}
                {sourceLabel(item.answer_source, t)}
                {item.unanswered ? ` · ${t("conversations.unanswered")}` : ""}
              </p>
            </div>
            <Button type="button" variant="outline" onClick={() => setOpenId(item.id)}>
              {t("conversations.open")}
            </Button>
          </div>
        </Card>
      ))}
      {!badRange && list.data && list.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("conversations.empty")}</p>
      ) : null}
    </section>
  );
}
