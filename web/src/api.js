export async function classifyIntent(message) {
    const resp = await fetch("/api/intent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
    });
    const data = await resp.json();
    return data.intent ?? "other";
}
export async function extractTravelState(message) {
    const resp = await fetch("/api/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
    });
    return resp.json();
}
export async function parseItinerary(text) {
    const resp = await fetch("/api/parse_itinerary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    });
    const data = await resp.json();
    return data.days;
}
export async function fetchAmapKey() {
    try {
        const resp = await fetch("/api/config/amap_key");
        const data = await resp.json();
        return data.key ?? "";
    }
    catch {
        return "";
    }
}
export async function* chatStream(message, sessionId, travelState) {
    const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            message,
            session_id: sessionId,
            travel_state: travelState ?? null,
        }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done)
            break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith("data:")) {
                const json = trimmed.slice(5).trim();
                if (json) {
                    try {
                        yield JSON.parse(json);
                    }
                    catch {
                        // skip malformed
                    }
                }
            }
        }
    }
}
