import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { chatStream, extractTravelState, classifyIntent, parseItinerary, fetchAmapKey } from "./api";
const hasItineraryDays = (content) => /第\s*[一二三四五六七八九十\d]+\s*[天日]|Day\s*\d+/i.test(content);
export default function App() {
    const [messages, setMessages] = useState(() => {
        try {
            const saved = localStorage.getItem("trip_messages");
            return saved ? JSON.parse(saved) : [];
        }
        catch {
            return [];
        }
    });
    const [input, setInput] = useState("");
    const [loading, setLoading] = useState(false);
    const [mapLoadingIdx, setMapLoadingIdx] = useState(null);
    const [sessionId, setSessionId] = useState(() => localStorage.getItem("trip_session_id") || "");
    const bottomRef = useRef(null);
    const chatRef = useRef(null);
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
        if (!el)
            return;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        userScrolledUp.current = !atBottom;
    };
    // ── 核心流式对话（首次传 travelState，后续传 null 由 checkpointer 恢复）──
    const streamChat = async (message, travelState) => {
        let content = "";
        const toolCalls = [];
        let sid = sessionId;
        const updateAssistant = () => {
            setMessages((prev) => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last?.role === "assistant") {
                    copy[copy.length - 1] = { role: "assistant", content, toolCalls: [...toolCalls] };
                }
                else {
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
                        toolCalls.push({ tool: event.tool, input: event.input || {} });
                        updateAssistant();
                        break;
                    case "tool_result": {
                        const match = toolCalls.findLast((tc) => tc.tool === event.tool && tc.output === undefined);
                        if (match)
                            match.output = event.output;
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
        }
        catch {
            content += "\n\n⚠️ 请求失败，请检查后端服务是否启动";
            updateAssistant();
        }
        setLoading(false);
    };
    const send = async () => {
        const text = input.trim();
        if (!text || loading)
            return;
        setInput("");
        setLoading(true);
        userScrolledUp.current = false;
        // 意图识别：planning → 提取信息+表单；booking/other → 直接对话
        let intent = "other";
        try {
            intent = await classifyIntent(text);
        }
        catch {
            // 识别失败时降级为直接对话
        }
        setMessages((prev) => [...prev, { role: "user", content: text }]);
        if (intent === "planning") {
            // 行程规划：提取旅行信息 → 弹出表单
            let extractResult = null;
            try {
                extractResult = await extractTravelState(text);
            }
            catch {
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
                    formData: { initial: extractResult.state, options: extractResult.options },
                    formSubmitted: false,
                },
            ]);
            setLoading(false);
        }
        else {
            // booking / other：直接对话，travel_state 由 LangGraph checkpointer 恢复
            await streamChat(text, null);
        }
    };
    // 用户提交表单后触发规划
    const startPlanning = async (state, formMsgIndex) => {
        setMessages((prev) => prev.map((m, i) => (i === formMsgIndex ? { ...m, formSubmitted: true } : m)));
        userScrolledUp.current = false;
        setLoading(true);
        const interests = state.interests?.join("、") || "综合游览";
        const planMessage = `请为我规划旅行方案：\n` +
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
    return (_jsxs("div", { className: "app", children: [_jsxs("header", { children: [_jsx("h1", { children: "\uD83D\uDDFA\uFE0F \u667A\u6167\u65C5\u6E38 Agent" }), _jsx("button", { onClick: newChat, className: "new-chat", children: "\u65B0\u5BF9\u8BDD" })] }), _jsxs("main", { className: "messages", ref: chatRef, onScroll: handleScroll, children: [messages.length === 0 && (_jsxs("div", { className: "welcome", children: [_jsx("p", { children: "\u4F60\u597D\uFF01\u6211\u662F\u667A\u6167\u65C5\u6E38\u52A9\u624B\uFF0C\u53EF\u4EE5\u5E2E\u4F60\uFF1A" }), _jsxs("ul", { children: [_jsx("li", { children: "\uD83C\uDF24\uFE0F \u67E5\u8BE2\u76EE\u7684\u5730\u5929\u6C14" }), _jsx("li", { children: "\uD83D\uDCCD \u641C\u7D22\u666F\u70B9\u3001\u7F8E\u98DF\u3001\u9152\u5E97" }), _jsx("li", { children: "\uD83D\uDE97 \u89C4\u5212\u4EA4\u901A\u8DEF\u7EBF" }), _jsx("li", { children: "\uD83D\uDCDA \u4E86\u89E3\u5F53\u5730\u6587\u5316\u5386\u53F2" })] }), _jsx("p", { children: "\u8BD5\u8BD5\u8BF4\uFF1A\u300C\u5E2E\u6211\u89C4\u5212\u4E00\u4E2A\u676D\u5DDE\u4E09\u65E5\u6E38\u300D" }), _jsx("p", { children: "\u63CF\u8FF0\u66F4\u6E05\u695A\u4F1A\u5F97\u5230\u66F4\u52A0\u4E2A\u6027\u5316\u7684\u7B54\u6848\u54E6\uFF08\u76EE\u7684\u5730\u3001\u65C5\u884C\u5929\u6570\u3001\u9884\u7B97\u7B49\u7EA7\u3001\u540C\u884C\u4EBA\u3001\u5174\u8DA3\u504F\u597D\uFF09" })] })), messages.map((msg, i) => {
                        if (msg.role === "form" && msg.formData) {
                            return (_jsx(TravelForm, { initial: msg.formData.initial, options: msg.formData.options, submitted: !!msg.formSubmitted, onSubmit: (state) => startPlanning(state, i) }, i));
                        }
                        return (_jsxs("div", { className: `msg ${msg.role}`, children: [_jsx("div", { className: "msg-label", children: msg.role === "user" ? "你" : "Agent" }), msg.toolCalls && msg.toolCalls.length > 0 && (_jsx("div", { className: "tool-calls", children: msg.toolCalls.map((tc, j) => (_jsxs("details", { className: "tool-card", children: [_jsxs("summary", { children: ["\uD83D\uDD27 \u8C03\u7528\u5DE5\u5177: ", _jsx("strong", { children: tc.tool }), tc.output ? " ✅" : " ⏳"] }), _jsxs("div", { className: "tool-detail", children: [_jsxs("div", { className: "tool-section", children: [_jsx("span", { children: "\u8F93\u5165:" }), _jsx("pre", { children: JSON.stringify(tc.input, null, 2) })] }), tc.output && (_jsxs("div", { className: "tool-section", children: [_jsx("span", { children: "\u7ED3\u679C:" }), _jsx("pre", { children: tc.output })] }))] })] }, j))) })), msg.content && (_jsxs("div", { className: "msg-content", children: [_jsx(ReactMarkdown, { components: {
                                                a: ({ href, children }) => {
                                                    const isBooking = href?.includes("ctrip.com") ||
                                                        href?.includes("fliggy.com") ||
                                                        href?.includes("qunar.com");
                                                    if (isBooking) {
                                                        return (_jsxs("button", { className: "booking-btn", onClick: () => {
                                                                if (window.confirm(`即将跳转到第三方平台完成预订，确认继续？\n${href}`)) {
                                                                    window.open(href, "_blank", "noopener,noreferrer");
                                                                }
                                                            }, children: [children, " \u2708\uFE0F"] }));
                                                    }
                                                    return (_jsx("a", { href: href, target: "_blank", rel: "noopener noreferrer", children: children }));
                                                },
                                            }, children: msg.content }), msg.role === "assistant" && hasItineraryDays(msg.content) && (_jsx("button", { className: "map-btn", disabled: mapLoadingIdx === i, onClick: async () => {
                                                setMapLoadingIdx(i);
                                                try {
                                                    const [days, key] = await Promise.all([
                                                        parseItinerary(msg.content),
                                                        fetchAmapKey(),
                                                    ]);
                                                    window.__openTripMap?.(days, key);
                                                }
                                                catch (e) {
                                                    console.error("parse_itinerary failed", e);
                                                    alert("行程解析失败，请确认后端服务是否已启动");
                                                }
                                                finally {
                                                    setMapLoadingIdx(null);
                                                }
                                            }, children: mapLoadingIdx === i ? "⏳ 解析中..." : "🗺️ 查看地图规划" }))] }))] }, i));
                    }), loading && _jsx("div", { className: "loading", children: "\u601D\u8003\u4E2D..." }), _jsx("div", { ref: bottomRef })] }), _jsxs("footer", { children: [_jsx("input", { value: input, onChange: (e) => setInput(e.target.value), onKeyDown: (e) => e.key === "Enter" && !e.shiftKey && send(), placeholder: "\u8F93\u5165\u4F60\u7684\u65C5\u884C\u9700\u6C42...", disabled: loading }), _jsx("button", { onClick: send, disabled: loading, children: "\u53D1\u9001" })] })] }));
}
function TravelForm({ initial, options, submitted, onSubmit }) {
    const [destination, setDestination] = useState(initial.destination || "");
    const [days, setDays] = useState(initial.days != null ? String(initial.days) : "");
    const [budget, setBudget] = useState(initial.budget || "");
    const [travelGroup, setTravelGroup] = useState(initial.travel_group || "");
    const [interests, setInterests] = useState(initial.interests || []);
    const [errors, setErrors] = useState({});
    const toggleInterest = (item) => {
        setInterests((prev) => prev.includes(item) ? prev.filter((i) => i !== item) : [...prev, item]);
    };
    const validate = () => {
        const e = {};
        if (!destination.trim())
            e.destination = "请填写目的地";
        const d = Number(days);
        if (!days || isNaN(d) || d < 1 || d > 30)
            e.days = "请填写 1-30 的天数";
        if (!budget)
            e.budget = "请选择预算";
        if (!travelGroup)
            e.travelGroup = "请选择同行人";
        if (interests.length === 0)
            e.interests = "请至少选择一项兴趣";
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
        return (_jsxs("div", { className: "msg assistant", children: [_jsx("div", { className: "msg-label", children: "Agent" }), _jsxs("div", { className: "travel-state-summary", children: [_jsx("span", { children: "\uD83D\uDCCB \u5DF2\u786E\u8BA4\u65C5\u884C\u4FE1\u606F" }), _jsx("table", { children: _jsxs("tbody", { children: [_jsxs("tr", { children: [_jsx("td", { children: "\u76EE\u7684\u5730" }), _jsx("td", { children: destination })] }), _jsxs("tr", { children: [_jsx("td", { children: "\u5929\u6570" }), _jsxs("td", { children: [days, " \u5929"] })] }), _jsxs("tr", { children: [_jsx("td", { children: "\u9884\u7B97" }), _jsx("td", { children: budget })] }), _jsxs("tr", { children: [_jsx("td", { children: "\u540C\u884C\u4EBA" }), _jsx("td", { children: travelGroup })] }), _jsxs("tr", { children: [_jsx("td", { children: "\u5174\u8DA3\u504F\u597D" }), _jsx("td", { children: interests.join("、") })] })] }) })] })] }));
    }
    return (_jsxs("div", { className: "msg assistant", children: [_jsx("div", { className: "msg-label", children: "Agent" }), _jsxs("div", { className: "travel-form-card", children: [_jsx("p", { className: "form-title", children: "\uD83D\uDCCB \u8BF7\u786E\u8BA4\u6216\u8865\u5145\u65C5\u884C\u4FE1\u606F" }), _jsx("p", { className: "form-hint", children: "\u5DF2\u81EA\u52A8\u586B\u5165\u8BC6\u522B\u5230\u7684\u5185\u5BB9\uFF0C\u7A7A\u767D\u9879\u8BF7\u624B\u52A8\u586B\u5199\u540E\u70B9\u51FB\u300C\u5F00\u59CB\u89C4\u5212\u300D" }), _jsx("table", { className: "form-table", children: _jsxs("tbody", { children: [_jsxs("tr", { children: [_jsx("td", { children: _jsxs("label", { children: ["\u76EE\u7684\u5730 ", _jsx("span", { className: "req", children: "*" })] }) }), _jsxs("td", { children: [_jsx("input", { className: errors.destination ? "err" : "", value: destination, onChange: (e) => { setDestination(e.target.value); setErrors((p) => ({ ...p, destination: "" })); }, placeholder: "\u5982\uFF1A\u676D\u5DDE\u3001\u6210\u90FD\u3001\u65E5\u672C\u4EAC\u90FD" }), errors.destination && _jsx("span", { className: "err-msg", children: errors.destination })] })] }), _jsxs("tr", { children: [_jsx("td", { children: _jsxs("label", { children: ["\u65C5\u884C\u5929\u6570 ", _jsx("span", { className: "req", children: "*" })] }) }), _jsxs("td", { children: [_jsx("input", { type: "number", min: 1, max: 30, className: errors.days ? "err" : "", value: days, onChange: (e) => { setDays(e.target.value); setErrors((p) => ({ ...p, days: "" })); }, placeholder: "1 - 30" }), errors.days && _jsx("span", { className: "err-msg", children: errors.days })] })] }), _jsxs("tr", { children: [_jsx("td", { children: _jsxs("label", { children: ["\u9884\u7B97 ", _jsx("span", { className: "req", children: "*" })] }) }), _jsxs("td", { children: [_jsx("div", { className: "radio-group", children: options.budget.map((b) => (_jsxs("label", { className: `radio-btn ${budget === b ? "active" : ""}`, children: [_jsx("input", { type: "radio", value: b, checked: budget === b, onChange: () => { setBudget(b); setErrors((p) => ({ ...p, budget: "" })); } }), b] }, b))) }), errors.budget && _jsx("span", { className: "err-msg", children: errors.budget })] })] }), _jsxs("tr", { children: [_jsx("td", { children: _jsxs("label", { children: ["\u540C\u884C\u4EBA ", _jsx("span", { className: "req", children: "*" })] }) }), _jsxs("td", { children: [_jsx("div", { className: "radio-group", children: options.travel_group.map((g) => (_jsxs("label", { className: `radio-btn ${travelGroup === g ? "active" : ""}`, children: [_jsx("input", { type: "radio", value: g, checked: travelGroup === g, onChange: () => { setTravelGroup(g); setErrors((p) => ({ ...p, travelGroup: "" })); } }), g] }, g))) }), errors.travelGroup && _jsx("span", { className: "err-msg", children: errors.travelGroup })] })] }), _jsxs("tr", { children: [_jsx("td", { children: _jsxs("label", { children: ["\u5174\u8DA3\u504F\u597D ", _jsx("span", { className: "req", children: "*" })] }) }), _jsxs("td", { children: [_jsx("div", { className: "checkbox-group", children: options.interests.map((item) => (_jsxs("label", { className: `checkbox-btn ${interests.includes(item) ? "active" : ""}`, children: [_jsx("input", { type: "checkbox", checked: interests.includes(item), onChange: () => { toggleInterest(item); setErrors((p) => ({ ...p, interests: "" })); } }), item] }, item))) }), errors.interests && _jsx("span", { className: "err-msg", children: errors.interests })] })] })] }) }), _jsx("button", { className: "submit-plan-btn", onClick: handleSubmit, children: "\uD83D\uDE80 \u5F00\u59CB\u89C4\u5212" })] })] }));
}
