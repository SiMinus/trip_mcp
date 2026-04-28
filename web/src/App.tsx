import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { chatStream, extractTravelState, classifyIntent, TravelState, ExtractResponse, SSEEvent } from "./api";

interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output?: string;
}

interface Message {
  role: "user" | "assistant" | "form";
  content: string;
  toolCalls?: ToolCall[];
  formData?: {
    initial: Partial<TravelState>;
    options: ExtractResponse["options"];
  };
  formSubmitted?: boolean;
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>(() => {
    try {
      const saved = localStorage.getItem("trip_messages");
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(
    () => localStorage.getItem("trip_session_id") || ""
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // 持久化消息到 localStorage
  useEffect(() => {
    localStorage.setItem("trip_messages", JSON.stringify(messages));
  }, [messages]);

  // 持久化 sessionId
  useEffect(() => {
    localStorage.setItem("trip_session_id", sessionId);
  }, [sessionId]);

  // 仅在用户没有手动上翻时才自动滚到底部
  useEffect(() => {
    if (!userScrolledUp.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  const handleScroll = () => {
    const el = chatRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    userScrolledUp.current = !atBottom;
  };

  // ── 核心流式对话（首次传 travelState，后续传 null 由 checkpointer 恢复）──
  const streamChat = async (message: string, travelState: TravelState | null) => {
    let content = "";
    const toolCalls: ToolCall[] = [];
    let sid = sessionId;

    const updateAssistant = () => {
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") {
          copy[copy.length - 1] = { role: "assistant", content, toolCalls: [...toolCalls] };
        } else {
          copy.push({ role: "assistant", content, toolCalls: [...toolCalls] });
        }
        return copy;
      });
    };

    try {
      for await (const event of chatStream(message, sid, travelState)) {
        switch (event.type) {
          case "token":
            content += event.content || "";
            updateAssistant();
            break;
          case "tool_call":
            toolCalls.push({ tool: event.tool!, input: event.input || {} });
            updateAssistant();
            break;
          case "tool_result": {
            const match = toolCalls.findLast(
              (tc) => tc.tool === event.tool && tc.output === undefined
            );
            if (match) match.output = event.output;
            updateAssistant();
            break;
          }
          case "done":
            if (event.session_id) {
              sid = event.session_id;
              setSessionId(sid);
            }
            break;
        }
      }
    } catch {
      content += "\n\n⚠️ 请求失败，请检查后端服务是否启动";
      updateAssistant();
    }
    setLoading(false);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setLoading(true);
    userScrolledUp.current = false;

    // 意图识别：planning → 提取信息+表单；booking/other → 直接对话
    let intent = "other";
    try {
      intent = await classifyIntent(text);
    } catch {
      // 识别失败时降级为直接对话
    }

    setMessages((prev) => [...prev, { role: "user", content: text }]);

    if (intent === "planning") {
      // 行程规划：提取旅行信息 → 弹出表单
      let extractResult: ExtractResponse | null = null;
      try {
        extractResult = await extractTravelState(text);
      } catch {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "⚠️ 信息提取失败，请检查后端服务是否启动" },
        ]);
        setLoading(false);
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          role: "form",
          content: "",
          formData: { initial: extractResult!.state, options: extractResult!.options },
          formSubmitted: false,
        },
      ]);
      setLoading(false);
    } else {
      // booking / other：直接对话，travel_state 由 LangGraph checkpointer 恢复
      await streamChat(text, null);
    }
  };

  // 用户提交表单后触发规划
  const startPlanning = async (state: TravelState, formMsgIndex: number) => {
    setMessages((prev) =>
      prev.map((m, i) => (i === formMsgIndex ? { ...m, formSubmitted: true } : m))
    );
    userScrolledUp.current = false;
    setLoading(true);

    const interests = state.interests?.join("、") || "综合游览";
    const planMessage =
      `请为我规划旅行方案：\n` +
      `- 目的地：${state.destination}\n` +
      `- 旅行天数：${state.days} 天\n` +
      `- 预算：${state.budget}\n` +
      `- 同行人：${state.travel_group}\n` +
      `- 兴趣偏好：${interests}\n\n` +
      `请调用工具查询天气、搜索景点、检索旅游攻略，生成详细的每日行程安排。`;

    await streamChat(planMessage, state);
  };

  const newChat = () => {
    setMessages([]);
    setSessionId("");
    localStorage.removeItem("trip_messages");
    localStorage.removeItem("trip_session_id");
  };

  return (
    <div className="app">
      <header>
        <h1>🗺️ 智慧旅游 Agent</h1>
        <button onClick={newChat} className="new-chat">
          新对话
        </button>
      </header>

      <main className="messages" ref={chatRef} onScroll={handleScroll}>
        {messages.length === 0 && (
          <div className="welcome">
            <p>你好！我是智慧旅游助手，可以帮你：</p>
            <ul>
              <li>🌤️ 查询目的地天气</li>
              <li>📍 搜索景点、美食、酒店</li>
              <li>🚗 规划交通路线</li>
              <li>📚 了解当地文化历史</li>
            </ul>
            <p>试试说：「帮我规划一个杭州三日游」</p>
            <p>描述更清楚会得到更加个性化的答案哦（目的地、旅行天数、预算等级、同行人、兴趣偏好）</p>
          </div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === "form" && msg.formData) {
            return (
              <TravelForm
                key={i}
                initial={msg.formData.initial}
                options={msg.formData.options}
                submitted={!!msg.formSubmitted}
                onSubmit={(state) => startPlanning(state, i)}
              />
            );
          }
          return (
            <div key={i} className={`msg ${msg.role}`}>
              <div className="msg-label">{msg.role === "user" ? "你" : "Agent"}</div>
              {msg.toolCalls && msg.toolCalls.length > 0 && (
                <div className="tool-calls">
                  {msg.toolCalls.map((tc, j) => (
                    <details key={j} className="tool-card">
                      <summary>
                        🔧 调用工具: <strong>{tc.tool}</strong>
                        {tc.output ? " ✅" : " ⏳"}
                      </summary>
                      <div className="tool-detail">
                        <div className="tool-section">
                          <span>输入:</span>
                          <pre>{JSON.stringify(tc.input, null, 2)}</pre>
                        </div>
                        {tc.output && (
                          <div className="tool-section">
                            <span>结果:</span>
                            <pre>{tc.output}</pre>
                          </div>
                        )}
                      </div>
                    </details>
                  ))}
                </div>
              )}
              {msg.content && (
                <div className="msg-content">
                  <ReactMarkdown
                    components={{
                      a: ({ href, children }) => {
                        const isBooking =
                          href?.includes("ctrip.com") ||
                          href?.includes("fliggy.com") ||
                          href?.includes("qunar.com");
                        if (isBooking) {
                          return (
                            <button
                              className="booking-btn"
                              onClick={() => {
                                if (
                                  window.confirm(
                                    `即将跳转到第三方平台完成预订，确认继续？\n${href}`
                                  )
                                ) {
                                  window.open(href, "_blank", "noopener,noreferrer");
                                }
                              }}
                            >
                              {children} ✈️
                            </button>
                          );
                        }
                        return (
                          <a href={href} target="_blank" rel="noopener noreferrer">
                            {children}
                          </a>
                        );
                      },
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          );
        })}

        {loading && <div className="loading">思考中...</div>}
        <div ref={bottomRef} />
      </main>

      <footer>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder="输入你的旅行需求..."
          disabled={loading}
        />
        <button onClick={send} disabled={loading}>
          发送
        </button>
      </footer>
    </div>
  );
}

// ─── TravelForm 组件 ───────────────────────────────────────────────
interface TravelFormProps {
  initial: Partial<TravelState>;
  options: ExtractResponse["options"];
  submitted: boolean;
  onSubmit: (state: TravelState) => void;
}

function TravelForm({ initial, options, submitted, onSubmit }: TravelFormProps) {
  const [destination, setDestination] = useState(initial.destination || "");
  const [days, setDays] = useState<string>(initial.days != null ? String(initial.days) : "");
  const [budget, setBudget] = useState(initial.budget || "");
  const [travelGroup, setTravelGroup] = useState(initial.travel_group || "");
  const [interests, setInterests] = useState<string[]>(initial.interests || []);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const toggleInterest = (item: string) => {
    setInterests((prev) =>
      prev.includes(item) ? prev.filter((i) => i !== item) : [...prev, item]
    );
  };

  const validate = () => {
    const e: Record<string, string> = {};
    if (!destination.trim()) e.destination = "请填写目的地";
    const d = Number(days);
    if (!days || isNaN(d) || d < 1 || d > 30) e.days = "请填写 1-30 的天数";
    if (!budget) e.budget = "请选择预算";
    if (!travelGroup) e.travelGroup = "请选择同行人";
    if (interests.length === 0) e.interests = "请至少选择一项兴趣";
    return e;
  };

  const handleSubmit = () => {
    const e = validate();
    if (Object.keys(e).length > 0) {
      setErrors(e);
      return;
    }
    onSubmit({
      destination: destination.trim(),
      days: Number(days),
      budget,
      travel_group: travelGroup,
      interests,
    });
  };

  // 已提交后显示摘要卡片
  if (submitted) {
    return (
      <div className="msg assistant">
        <div className="msg-label">Agent</div>
        <div className="travel-state-summary">
          <span>📋 已确认旅行信息</span>
          <table>
            <tbody>
              <tr><td>目的地</td><td>{destination}</td></tr>
              <tr><td>天数</td><td>{days} 天</td></tr>
              <tr><td>预算</td><td>{budget}</td></tr>
              <tr><td>同行人</td><td>{travelGroup}</td></tr>
              <tr><td>兴趣偏好</td><td>{interests.join("、")}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="msg assistant">
      <div className="msg-label">Agent</div>
      <div className="travel-form-card">
        <p className="form-title">📋 请确认或补充旅行信息</p>
        <p className="form-hint">已自动填入识别到的内容，空白项请手动填写后点击「开始规划」</p>
        <table className="form-table">
          <tbody>
            <tr>
              <td><label>目的地 <span className="req">*</span></label></td>
              <td>
                <input
                  className={errors.destination ? "err" : ""}
                  value={destination}
                  onChange={(e) => { setDestination(e.target.value); setErrors((p) => ({ ...p, destination: "" })); }}
                  placeholder="如：杭州、成都、日本京都"
                />
                {errors.destination && <span className="err-msg">{errors.destination}</span>}
              </td>
            </tr>
            <tr>
              <td><label>旅行天数 <span className="req">*</span></label></td>
              <td>
                <input
                  type="number"
                  min={1}
                  max={30}
                  className={errors.days ? "err" : ""}
                  value={days}
                  onChange={(e) => { setDays(e.target.value); setErrors((p) => ({ ...p, days: "" })); }}
                  placeholder="1 - 30"
                />
                {errors.days && <span className="err-msg">{errors.days}</span>}
              </td>
            </tr>
            <tr>
              <td><label>预算 <span className="req">*</span></label></td>
              <td>
                <div className="radio-group">
                  {options.budget.map((b) => (
                    <label key={b} className={`radio-btn ${budget === b ? "active" : ""}`}>
                      <input type="radio" value={b} checked={budget === b}
                        onChange={() => { setBudget(b); setErrors((p) => ({ ...p, budget: "" })); }} />
                      {b}
                    </label>
                  ))}
                </div>
                {errors.budget && <span className="err-msg">{errors.budget}</span>}
              </td>
            </tr>
            <tr>
              <td><label>同行人 <span className="req">*</span></label></td>
              <td>
                <div className="radio-group">
                  {options.travel_group.map((g) => (
                    <label key={g} className={`radio-btn ${travelGroup === g ? "active" : ""}`}>
                      <input type="radio" value={g} checked={travelGroup === g}
                        onChange={() => { setTravelGroup(g); setErrors((p) => ({ ...p, travelGroup: "" })); }} />
                      {g}
                    </label>
                  ))}
                </div>
                {errors.travelGroup && <span className="err-msg">{errors.travelGroup}</span>}
              </td>
            </tr>
            <tr>
              <td><label>兴趣偏好 <span className="req">*</span></label></td>
              <td>
                <div className="checkbox-group">
                  {options.interests.map((item) => (
                    <label key={item} className={`checkbox-btn ${interests.includes(item) ? "active" : ""}`}>
                      <input type="checkbox" checked={interests.includes(item)}
                        onChange={() => { toggleInterest(item); setErrors((p) => ({ ...p, interests: "" })); }} />
                      {item}
                    </label>
                  ))}
                </div>
                {errors.interests && <span className="err-msg">{errors.interests}</span>}
              </td>
            </tr>
          </tbody>
        </table>
        <button className="submit-plan-btn" onClick={handleSubmit}>
          🚀 开始规划
        </button>
      </div>
    </div>
  );
}
