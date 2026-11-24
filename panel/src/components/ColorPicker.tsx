import { COLOR_PRESETS, contrastRatio, onColor, parseHex } from "@/lib/contrast";
import { useI18n } from "@/i18n/useI18n";

/** Native colour input, hex field and presets, all bound to one value.
 *  The hex field may hold a half-typed value; the swatch and the native input
 *  keep the last valid colour until it is complete. The API re-validates. */
export function ColorPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const { t } = useI18n();
  const parsed = parseHex(value);
  const hex = parsed ?? "#2563eb";
  const ink = onColor(hex);
  const ratio = contrastRatio(ink, hex).toFixed(1);
  const invalid = value.trim() !== "" && !parsed;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label={t("widget.color")}
          type="color"
          value={hex}
          onChange={(event) => onChange(event.target.value.toLowerCase())}
          className="h-10 w-12 cursor-pointer rounded border border-border bg-card p-0"
        />
        <input
          aria-label={t("widget.colorHex")}
          aria-invalid={invalid}
          aria-describedby={invalid ? "color-hex-error" : undefined}
          maxLength={7}
          spellCheck={false}
          className="w-28 rounded-md border border-border bg-card px-2 py-1.5 font-mono text-sm aria-[invalid=true]:border-red-700"
          value={value}
          onChange={(event) => onChange(event.target.value.trim())}
        />
        <span className="rounded-md px-2 py-1 text-xs" style={{ background: hex, color: ink }}>
          {t("widget.contrastHint")} · {ratio}:1
        </span>
      </div>
      {invalid ? (
        <p id="color-hex-error" className="text-xs text-red-700">
          {t("widget.colorInvalid")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("widget.presets")}>
        {COLOR_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            aria-label={preset}
            aria-pressed={parsed === preset}
            className="h-7 w-7 rounded-full border border-border aria-pressed:ring-2 aria-pressed:ring-primary aria-pressed:ring-offset-2"
            style={{ background: preset }}
            onClick={() => onChange(preset)}
          />
        ))}
      </div>
    </div>
  );
}
