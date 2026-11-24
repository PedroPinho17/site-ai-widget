import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiRequestError,
  createKeyRequest,
  listKeysRequest,
  revokeKeyRequest,
  updateKeyOriginsRequest,
  type WidgetKey,
} from "@/lib/api";
import { API_URL } from "@/lib/config";
import { originError, parseAllowedOrigin } from "@/lib/origin";
import { embedSnippet, keyPrefix } from "@/lib/snippet";

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function KeysPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const queryClient = useQueryClient();
  const keys = useQuery({ queryKey: ["widget-keys"], queryFn: listKeysRequest });
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [revokeId, setRevokeId] = useState<string | null>(null);

  const createKey = useMutation({
    mutationFn: createKeyRequest,
    onSuccess: async () => {
      setError("");
      setNotice("Key created.");
      await queryClient.invalidateQueries({ queryKey: ["widget-keys"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : "Could not create the key.");
    },
  });

  const revokeKey = useMutation({
    mutationFn: revokeKeyRequest,
    onSuccess: async () => {
      setRevokeId(null);
      setError("");
      setNotice("Key deactivated.");
      await queryClient.invalidateQueries({ queryKey: ["widget-keys"] });
    },
    onError: (err) => {
      setNotice("");
      setError(err instanceof ApiRequestError ? err.message : "Could not deactivate the key.");
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Keys &amp; Origins</h1>
          <p className="text-sm text-muted-foreground">
            Public widget keys and the sites allowed to embed them.
          </p>
        </div>
        {isAdmin ? (
          <Button type="button" onClick={() => createKey.mutate()} disabled={createKey.isPending}>
            {createKey.isPending ? "Creating…" : "Create key"}
          </Button>
        ) : null}
      </div>
      {notice ? <p className="text-sm text-primary">{notice}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {keys.isLoading ? <p className="text-sm text-muted-foreground">Loading keys…</p> : null}
      {keys.error instanceof ApiRequestError ? (
        <p role="alert" className="text-sm text-destructive">
          {keys.error.message}
        </p>
      ) : null}
      {(keys.data ?? []).map((key) => (
        <KeyCard
          key={key.id}
          item={key}
          isAdmin={isAdmin}
          onRevoke={() => setRevokeId(key.id)}
          onCopied={(label) => {
            setError("");
            setNotice(label);
          }}
          onOrigins={(origins) =>
            updateKeyOriginsRequest(key.id, origins).then(async () => {
              setError("");
              await queryClient.invalidateQueries({ queryKey: ["widget-keys"] });
            })
          }
          onOriginError={(message) => {
            setNotice("");
            setError(message);
          }}
        />
      ))}
      {keys.data && keys.data.length === 0 ? (
        <Card>
          <p className="text-sm text-muted-foreground">No widget keys yet.</p>
        </Card>
      ) : null}
      <Dialog open={revokeId !== null} onOpenChange={(open) => !open && setRevokeId(null)}>
        <DialogContent>
          <DialogTitle>Deactivate this key?</DialogTitle>
          <DialogDescription>
            Sites using it will stop receiving answers. You can create a new key afterwards.
          </DialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setRevokeId(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={revokeKey.isPending || !revokeId}
              onClick={() => revokeId && revokeKey.mutate(revokeId)}
            >
              Deactivate
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KeyCard({
  item,
  isAdmin,
  onRevoke,
  onCopied,
  onOrigins,
  onOriginError,
}: {
  item: WidgetKey;
  isAdmin: boolean;
  onRevoke: () => void;
  onCopied: (label: string) => void;
  onOrigins: (origins: string[]) => Promise<void>;
  onOriginError: (message: string) => void;
}) {
  const [draft, setDraft] = useState("");

  async function addOrigin() {
    const parsed = parseAllowedOrigin(draft);
    const message = originError(draft);
    if (!parsed || message) {
      onOriginError(message ?? "That origin is not valid.");
      return;
    }
    if (item.allowed_origins.includes(parsed)) {
      onOriginError("That origin is already on the list.");
      return;
    }
    try {
      await onOrigins([...item.allowed_origins, parsed]);
      setDraft("");
      onCopied("Origin added.");
    } catch (err) {
      onOriginError(err instanceof ApiRequestError ? err.message : "Could not save the origin.");
    }
  }

  async function removeOrigin(origin: string) {
    try {
      await onOrigins(item.allowed_origins.filter((itemOrigin) => itemOrigin !== origin));
      onCopied("Origin removed.");
    } catch (err) {
      onOriginError(err instanceof ApiRequestError ? err.message : "Could not save the origin.");
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle className="font-mono text-base">{keyPrefix(item.public_key)}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">{item.is_active ? "Active" : "Inactive"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={async () => {
              const ok = await copyText(item.public_key);
              onCopied(ok ? "Public key copied." : "Could not copy.");
            }}
          >
            Copy key
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={async () => {
              const ok = await copyText(embedSnippet(API_URL, item.public_key));
              onCopied(ok ? "Embed snippet copied." : "Could not copy.");
            }}
          >
            Copy embed snippet
          </Button>
          {isAdmin && item.is_active ? (
            <Button type="button" variant="destructive" size="sm" onClick={onRevoke}>
              Deactivate
            </Button>
          ) : null}
        </div>
      </div>
      <div className="mt-4">
        <p className="text-sm font-medium">Allowed origins</p>
        {item.allowed_origins.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">None. The widget will refuse every site.</p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            {item.allowed_origins.map((origin) => (
              <li key={origin} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted px-3 py-2">
                <code className="text-sm">{origin}</code>
                {isAdmin ? (
                  <Button type="button" variant="ghost" size="sm" onClick={() => void removeOrigin(origin)}>
                    Remove
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {isAdmin ? (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="flex min-w-56 flex-1 flex-col gap-1.5">
              <Label htmlFor={`origin-${item.id}`}>Add origin</Label>
              <Input
                id={`origin-${item.id}`}
                value={draft}
                placeholder="https://shop.example"
                onChange={(event) => setDraft(event.target.value)}
              />
            </div>
            <Button type="button" variant="outline" onClick={() => void addOrigin()}>
              Add
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
