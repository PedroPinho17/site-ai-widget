import { Card, CardTitle } from "@/components/ui/card";

export function ComingSoonPage({ title }: { title: string }) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <p className="mt-2 text-sm text-muted-foreground">Coming soon.</p>
    </Card>
  );
}
