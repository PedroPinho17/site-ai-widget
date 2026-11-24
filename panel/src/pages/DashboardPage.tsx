import { useQuery } from "@tanstack/react-query";

import { Card, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n/useI18n";
import { dashboardStatsRequest, getSchemaRequest, listDocumentsRequest, listKeysRequest } from "@/lib/api";

export function DashboardPage() {
  const { t } = useI18n();
  const documents = useQuery({ queryKey: ["documents"], queryFn: listDocumentsRequest });
  const keys = useQuery({ queryKey: ["widget-keys"], queryFn: listKeysRequest });
  const schema = useQuery({ queryKey: ["db-schema"], queryFn: getSchemaRequest });
  const stats = useQuery({ queryKey: ["dashboard-stats"], queryFn: dashboardStatsRequest });

  const show = (loading: boolean, failed: boolean, value: number | string) =>
    loading ? "…" : failed ? "—" : value;
  const docsCount = documents.data?.length ?? 0;
  const approvedTables =
    schema.data?.tables.filter((table) => table.is_present && table.status === "approved").length ?? 0;
  const activeKeys = keys.data?.filter((key) => key.is_active).length ?? 0;
  const maxDay = Math.max(1, ...(stats.data?.questions_per_day.map((row) => row.count) ?? [1]));

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">{t("dashboard.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("dashboard.subtitle")}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardTitle className="text-base">{t("dashboard.documents")}</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(documents.isLoading, documents.isError, docsCount)}</p>
        </Card>
        <Card>
          <CardTitle className="text-base">{t("dashboard.tables")}</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(schema.isLoading, schema.isError, approvedTables)}</p>
        </Card>
        <Card>
          <CardTitle className="text-base">{t("dashboard.keys")}</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(keys.isLoading, keys.isError, activeKeys)}</p>
        </Card>
      </div>
      <Card>
        <CardTitle className="text-base">{t("dashboard.perDay")}</CardTitle>
        <div className="mt-3 flex h-32 items-end gap-1">
          {(stats.data?.questions_per_day ?? []).map((row) => (
            <div
              key={row.day}
              title={`${row.day}: ${row.count}`}
              className="flex-1 rounded-t bg-primary"
              style={{ height: `${Math.max(8, (row.count / maxDay) * 100)}%` }}
            />
          ))}
        </div>
      </Card>
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardTitle className="text-base">{t("dashboard.sql")}</CardTitle>
          <p className="mt-2 text-2xl font-semibold">{stats.data?.source_share.sql ?? 0}%</p>
        </Card>
        <Card>
          <CardTitle className="text-base">{t("dashboard.docs")}</CardTitle>
          <p className="mt-2 text-2xl font-semibold">{stats.data?.source_share.docs ?? 0}%</p>
        </Card>
        <Card>
          <CardTitle className="text-base">{t("dashboard.none")}</CardTitle>
          <p className="mt-2 text-2xl font-semibold">{stats.data?.source_share.none ?? 0}%</p>
        </Card>
      </div>
      <Card>
        <CardTitle className="text-base">{t("dashboard.avgMs")}</CardTitle>
        <p className="mt-2 text-2xl font-semibold">
          {stats.data?.average_response_ms == null ? "—" : `${stats.data.average_response_ms} ${t("dashboard.ms")}`}
        </p>
      </Card>
      <Card>
        <CardTitle className="text-base">{t("dashboard.unanswered")}</CardTitle>
        {stats.data && stats.data.top_unanswered.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">{t("dashboard.noUnanswered")}</p>
        ) : (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {(stats.data?.top_unanswered ?? []).map((row) => (
              <li key={row.question}>
                {row.question} ({row.count})
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
