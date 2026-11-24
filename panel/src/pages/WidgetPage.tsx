import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiRequestError, updateWidgetSettingsRequest, widgetSettingsRequest } from "@/lib/api";

const OPTIONS = [
  { value: "", label: "None" },
  { value: "EUR", label: "EUR" },
  { value: "USD", label: "USD" },
  { value: "GBP", label: "GBP" },
  { value: "BRL", label: "BRL" },
];

export function WidgetPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["widget-settings"], queryFn: widgetSettingsRequest });
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const value = draft ?? settings.data?.currency ?? "";

  const save = useMutation({
    mutationFn: () => updateWidgetSettingsRequest({ currency: value || null }),
    onSuccess: async () => {
      setDraft(null);
      setError("");
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["widget-settings"] });
    },
    onError: (err: unknown) => {
      setSaved(false);
      setError(err instanceof ApiRequestError ? err.message : "Could not save.");
    },
  });

  return (
    <section className="mx-auto flex max-w-lg flex-col gap-4">
      <h1 className="text-xl font-semibold">Widget</h1>
      <label className="flex flex-col gap-1 text-sm" htmlFor="currency">
        Currency
        <select
          id="currency"
          className="rounded-md border border-border bg-card px-3 py-2"
          value={value}
          onChange={(event) => {
            setDraft(event.target.value);
            setSaved(false);
          }}
        >
          {OPTIONS.map((option) => (
            <option key={option.label} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {saved ? (
        <p role="status" className="text-sm text-green-800">
          Saved ✓
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
      <Button type="button" onClick={() => save.mutate()} disabled={save.isPending || settings.isLoading}>
        Save
      </Button>
    </section>
  );
}
