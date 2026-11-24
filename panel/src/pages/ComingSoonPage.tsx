import { Card, CardTitle } from "@/components/ui/card";

export function ComingSoonPage({ title }: { title: string }) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <p className="mt-2 text-sm text-muted-foreground">Coming soon.</p>
    </Card>
  );
}

export function DashboardPage() {
  return (
    <Card>
      <CardTitle>Dashboard</CardTitle>
      <p className="mt-2 text-sm text-muted-foreground">
        Use the sidebar to manage widget keys and allowed origins. Other sections are coming soon.
      </p>
    </Card>
  );
}
