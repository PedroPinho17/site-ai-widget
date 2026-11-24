import { useQuery } from "@tanstack/react-query";

import { Card, CardTitle } from "@/components/ui/card";
import { getSchemaRequest, listDocumentsRequest, listKeysRequest } from "@/lib/api";

export function DashboardPage() {
  const documents = useQuery({ queryKey: ["documents"], queryFn: listDocumentsRequest });
  const keys = useQuery({ queryKey: ["widget-keys"], queryFn: listKeysRequest });
  const schema = useQuery({ queryKey: ["db-schema"], queryFn: getSchemaRequest });

  // A failed load shows "—", never a misleading 0.
  const show = (loading: boolean, failed: boolean, value: number) =>
    loading ? "…" : failed ? "—" : value;
  const docsCount = documents.data?.length ?? 0;
  const approvedTables =
    schema.data?.tables.filter((table) => table.is_present && table.status === "approved").length ?? 0;
  const activeKeys = keys.data?.filter((key) => key.is_active).length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview of documents, schema and widget keys.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardTitle className="text-base">Documents</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(documents.isLoading, documents.isError, docsCount)}</p>
        </Card>
        <Card>
          <CardTitle className="text-base">Approved tables</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(schema.isLoading, schema.isError, approvedTables)}</p>
        </Card>
        <Card>
          <CardTitle className="text-base">Active keys</CardTitle>
          <p className="mt-2 text-3xl font-semibold">{show(keys.isLoading, keys.isError, activeKeys)}</p>
        </Card>
      </div>
    </div>
  );
}
