export interface SSEEvent {
  type: "token" | "tool_call" | "tool_result" | "done";
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  output?: string;
  session_id?: string;
}

export interface TravelState {
  destination: string | null;
  days: number | null;
  budget: string | null;
  travel_group: string | null;
  interests: string[];
}

export interface ExtractResponse {
  state: TravelState;
  options: {
    budget: string[];
    travel_group: string[];
    interests: string[];
  };
}

export async function extractTravelState(message: string): Promise<ExtractResponse> {
  const resp = await fetch("/api/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return resp.json();
}

export async function* chatStream(
  message: string,
  sessionId: string,
  travelState?: TravelState | null,
): AsyncGenerator<SSEEvent> {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      travel_state: travelState ?? null,
    }),
  });

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("data:")) {
        const json = trimmed.slice(5).trim();
        if (json) {
          try {
            yield JSON.parse(json) as SSEEvent;
          } catch {
            // skip malformed
          }
        }
      }
    }
  }
}
