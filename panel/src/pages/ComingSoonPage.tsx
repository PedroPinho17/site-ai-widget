import { Card, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n/useI18n";

export function ComingSoonPage({ title }: { title: string }) {
  const { t } = useI18n();
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <p className="mt-2 text-sm text-muted-foreground">{t("common.comingSoon")}</p>
    </Card>
  );
}
