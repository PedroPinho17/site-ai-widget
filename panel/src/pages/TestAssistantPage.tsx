import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  ApiRequestError,
  type AssistantTestResponse,
  sourceChips,
  testAssistantRequest,
} from "@/lib/api";
import { SafeMarkdown } from "@/lib/safe-markdown";

const EXAMPLES = ["Qual o preço do azeite?", "Qual o horário de abertura?", "Olá"];

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  text: string;
  chips: string[];
  elapsed_ms?: number | null;
};

function answerText(data: AssistantTestResponse): string {
  if (typeof data.answer === "string" && data.answer) {
    return data.answer;
  }
  if (typeof data.message === "string" && data.message) {
    return data.message;
  }
  return "Não consegui responder a isso.";
}

export function TestAssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  useEffect(() => {
    const node = logRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [messages, busy]);

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) {
      return;
    }
    const userId = nextId.current++;
    setMessages((current) => [...current, { id: userId, role: "user", text: trimmed, chips: [] }]);
    setText("");
    setBusy(true);
    setError("");
    try {
      const data = await testAssistantRequest(trimmed, "pt");
      setMessages((current) => [
        ...current,
        {
          id: nextId.current++,
          role: "assistant",
          text: answerText(data),
          chips: sourceChips(data),
          elapsed_ms: data.elapsed_ms,
        },
      ]);
    } catch (err) {
      // Keep what the admin typed and drop the unsent bubble, so a retry
      // (for example after a 429) is one click and does not duplicate it.
      setMessages((current) => current.filter((message) => message.id !== userId));
      setText(trimmed);
      setError(err instanceof ApiRequestError ? err.message : "Could not send.");
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(text);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(text);
    }
  }

  return (
    <section className="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Test assistant</h1>
        <Button type="button" variant="outline" onClick={() => setMessages([])}>
          Clear chat
        </Button>
      </div>
      <div ref={logRef} className="flex flex-1 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-muted/40 p-4">
        {messages.length === 0 ? (
          <div className="m-auto flex max-w-md flex-col items-center gap-3 text-center">
            <p className="text-sm text-muted-foreground">Try a question about the shop.</p>
            {EXAMPLES.map((example) => (
              <Button key={example} type="button" variant="outline" onClick={() => void send(example)}>
                {example}
              </Button>
            ))}
          </div>
        ) : (
          messages.map((message) => (
            <div
              key={message.id}
              className={
                message.role === "user"
                  ? "max-w-[80%] self-end rounded-2xl bg-primary px-3 py-2 text-primary-foreground"
                  : "max-w-[80%] self-start rounded-2xl border border-border bg-card px-3 py-2"
              }
            >
              {message.role === "assistant" ? (
                <SafeMarkdown text={message.text} />
              ) : (
                <p className="whitespace-pre-wrap text-sm">{message.text}</p>
              )}
              {message.chips.length ? (
                <div className="mt-2 flex flex-wrap gap-1">
                  {message.chips.map((chip, index) => (
                    <span key={`${index}-${chip}`} className="rounded-full bg-muted px-2 py-0.5 text-xs">
                      {chip}
                    </span>
                  ))}
                </div>
              ) : null}
              {message.role === "assistant" && typeof message.elapsed_ms === "number" ? (
                <p className="mt-1 text-xs text-muted-foreground">{message.elapsed_ms} ms</p>
              ) : null}
            </div>
          ))
        )}
        {busy ? (
          <p className="text-sm text-muted-foreground" role="status">
            A escrever…
          </p>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}
      <form className="flex items-end gap-2" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="test-question">
          Question
        </label>
        <textarea
          id="test-question"
          className="min-h-12 flex-1 rounded-md border border-border bg-card px-3 py-2 text-sm"
          value={text}
          placeholder="Escreva uma pergunta"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <Button type="submit" disabled={busy || text.trim().length === 0}>
          Send
        </Button>
      </form>
    </section>
  );
}
