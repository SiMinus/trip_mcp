import { jsx as _jsx } from "react/jsx-runtime";
import React, { useState, useEffect } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import TripMap from "./TripMap";
import "./App.css";
function Root() {
    const [route, setRoute] = useState(window.location.hash);
    const [tripDays, setTripDays] = useState([]);
    const [amapKey, setAmapKey] = useState("");
    useEffect(() => {
        const onHash = () => setRoute(window.location.hash);
        window.addEventListener("hashchange", onHash);
        return () => window.removeEventListener("hashchange", onHash);
    }, []);
    // 全局挂载：App 组件通过 window 传数据给地图页
    useEffect(() => {
        window.__openTripMap = (days, key) => {
            setTripDays(days);
            setAmapKey(key);
            window.location.hash = "/map";
        };
    }, []);
    if (route === "#/map") {
        return (_jsx(TripMap, { days: tripDays, amapKey: amapKey, onBack: () => { window.location.hash = ""; } }));
    }
    return _jsx(App, {});
}
ReactDOM.createRoot(document.getElementById("root")).render(_jsx(React.StrictMode, { children: _jsx(Root, {}) }));
