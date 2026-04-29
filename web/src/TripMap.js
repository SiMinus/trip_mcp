import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * 旅游行程可视化页面
 * 左侧：高德地图（markers + polyline）
 * 右侧：竖向时间轴
 * 顶部：第X天切换按钮
 */
import { useEffect, useRef, useState } from "react";
import "./TripMap.css";
function loadAmapScript(key) {
    if (window._amapLoaded)
        return Promise.resolve();
    return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = `https://webapi.amap.com/maps?v=2.0&key=${key}`;
        script.onload = () => {
            window._amapLoaded = true;
            resolve();
        };
        script.onerror = reject;
        document.head.appendChild(script);
    });
}
const DAY_COLORS = ["#FF6B35", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD"];
export default function TripMap({ days, amapKey, onBack }) {
    const mapRef = useRef(null);
    const mapInstance = useRef(null);
    const markersRef = useRef([]);
    const polylinesRef = useRef([]);
    const [currentDay, setCurrentDay] = useState(0);
    const [activeSpot, setActiveSpot] = useState(null);
    // 初始化地图
    useEffect(() => {
        if (!amapKey)
            return;
        loadAmapScript(amapKey).then(() => {
            if (mapInstance.current || !mapRef.current)
                return;
            mapInstance.current = new window.AMap.Map(mapRef.current, {
                zoom: 13,
                mapStyle: "amap://styles/normal",
            });
        });
        return () => {
            if (mapInstance.current) {
                mapInstance.current.destroy();
                mapInstance.current = null;
            }
        };
    }, [amapKey]);
    // 切换天/高亮时重绘
    useEffect(() => {
        const map = mapInstance.current;
        if (!map || !window.AMap)
            return;
        // 清除旧 markers & polylines
        markersRef.current.forEach((m) => map.remove(m));
        polylinesRef.current.forEach((p) => map.remove(p));
        markersRef.current = [];
        polylinesRef.current = [];
        const dayData = days[currentDay];
        if (!dayData)
            return;
        const color = DAY_COLORS[currentDay % DAY_COLORS.length];
        const validSpots = dayData.spots.filter((s) => s.lng && s.lat);
        const lngLats = [];
        validSpots.forEach((spot, idx) => {
            const isActive = activeSpot === idx;
            const lngLat = new window.AMap.LngLat(spot.lng, spot.lat);
            lngLats.push(lngLat);
            // 自定义 marker 内容
            const content = document.createElement("div");
            content.className = `amap-marker-custom${isActive ? " active" : ""}`;
            content.style.cssText = `
        background:${isActive ? "#FF3B30" : color};
        color:#fff;font-size:12px;font-weight:bold;
        width:28px;height:28px;border-radius:50%;
        display:flex;align-items:center;justify-content:center;
        border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35);
        cursor:pointer;transition:transform .2s;
        transform:scale(${isActive ? 1.4 : 1});
      `;
            content.textContent = String(idx + 1);
            const marker = new window.AMap.Marker({
                position: lngLat,
                content,
                title: spot.name,
                offset: new window.AMap.Pixel(-14, -14),
            });
            marker.on("click", () => {
                setActiveSpot(idx);
            });
            // InfoWindow
            const info = new window.AMap.InfoWindow({
                content: `<div style="padding:6px 10px;font-size:13px"><b>${spot.name}</b><br/>${spot.time} · ${spot.duration}</div>`,
                offset: new window.AMap.Pixel(0, -30),
            });
            if (isActive) {
                info.open(map, lngLat);
            }
            marker.on("click", () => info.open(map, lngLat));
            map.add(marker);
            markersRef.current.push(marker);
        });
        // polyline 连线
        if (lngLats.length > 1) {
            const polyline = new window.AMap.Polyline({
                path: lngLats,
                strokeColor: color,
                strokeWeight: 4,
                strokeOpacity: 0.8,
                strokeStyle: "dashed",
                lineJoin: "round",
            });
            map.add(polyline);
            polylinesRef.current.push(polyline);
        }
        // 自适应视野
        if (lngLats.length > 0) {
            map.setFitView(markersRef.current, false, [40, 40, 40, 40]);
        }
    }, [currentDay, activeSpot, days]);
    // 点击时间轴节点 → 地图移动
    const handleSpotClick = (idx) => {
        setActiveSpot(idx);
        const spot = days[currentDay]?.spots[idx];
        if (spot?.lng && spot?.lat && mapInstance.current) {
            mapInstance.current.setCenter([spot.lng, spot.lat]);
            mapInstance.current.setZoom(15);
        }
    };
    const dayData = days[currentDay];
    return (_jsxs("div", { className: "tripmap-root", children: [_jsxs("header", { className: "tripmap-header", children: [_jsx("button", { className: "back-btn", onClick: onBack, children: "\u2190 \u8FD4\u56DE\u5BF9\u8BDD" }), _jsx("div", { className: "day-tabs", children: days.map((d, i) => (_jsxs("button", { className: `day-tab${currentDay === i ? " active" : ""}`, style: currentDay === i ? { background: DAY_COLORS[i % DAY_COLORS.length] } : {}, onClick: () => { setCurrentDay(i); setActiveSpot(null); }, children: ["\u7B2C", d.day, "\u5929"] }, i))) })] }), _jsxs("div", { className: "tripmap-body", children: [_jsxs("div", { className: "map-pane", children: [!amapKey && (_jsxs("div", { className: "map-placeholder", children: [_jsx("p", { children: "\u26A0\uFE0F \u672A\u914D\u7F6E\u9AD8\u5FB7\u5730\u56FE Key" }), _jsxs("p", { children: ["\u8BF7\u5728 ", _jsx("code", { children: ".env" }), " \u4E2D\u8BBE\u7F6E ", _jsx("code", { children: "AMAP_API_KEY" })] })] })), _jsx("div", { ref: mapRef, className: "amap-container" })] }), _jsxs("aside", { className: "timeline-pane", children: [_jsxs("h3", { className: "timeline-title", children: ["\u7B2C", dayData?.day, "\u5929\u884C\u7A0B"] }), _jsx("ul", { className: "timeline", children: dayData?.spots.map((spot, idx) => (_jsxs("li", { className: `tl-node${activeSpot === idx ? " active" : ""}`, onClick: () => handleSpotClick(idx), children: [_jsx("div", { className: "tl-dot", style: { background: DAY_COLORS[currentDay % DAY_COLORS.length] }, children: idx + 1 }), _jsxs("div", { className: "tl-content", children: [_jsx("span", { className: "tl-time", children: spot.time }), _jsx("span", { className: "tl-name", children: spot.name }), _jsx("span", { className: "tl-duration", children: spot.duration })] })] }, idx))) })] })] })] }));
}
