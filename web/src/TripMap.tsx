/**
 * 旅游行程可视化页面
 * 左侧：高德地图（markers + polyline）
 * 右侧：竖向时间轴
 * 顶部：第X天切换按钮
 */
import { useEffect, useRef, useState } from "react";
import "./TripMap.css";

export interface Spot {
  name: string;
  time: string;
  duration: string;
  lng: number | null;
  lat: number | null;
}

export interface DayPlan {
  day: number;
  spots: Spot[];
}

interface TripMapProps {
  days: DayPlan[];
  amapKey: string;
  onBack: () => void;
}

declare global {
  interface Window {
    AMap: any;
    _amapLoaded?: boolean;
  }
}

function loadAmapScript(key: string): Promise<void> {
  if (window._amapLoaded) return Promise.resolve();
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

export default function TripMap({ days, amapKey, onBack }: TripMapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstance = useRef<any>(null);
  const markersRef = useRef<any[]>([]);
  const polylinesRef = useRef<any[]>([]);

  const [currentDay, setCurrentDay] = useState(0);
  const [activeSpot, setActiveSpot] = useState<number | null>(null);

  // 初始化地图
  useEffect(() => {
    if (!amapKey) return;
    loadAmapScript(amapKey).then(() => {
      if (mapInstance.current || !mapRef.current) return;
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
    if (!map || !window.AMap) return;

    // 清除旧 markers & polylines
    markersRef.current.forEach((m) => map.remove(m));
    polylinesRef.current.forEach((p) => map.remove(p));
    markersRef.current = [];
    polylinesRef.current = [];

    const dayData = days[currentDay];
    if (!dayData) return;

    const color = DAY_COLORS[currentDay % DAY_COLORS.length];
    const validSpots = dayData.spots.filter((s) => s.lng && s.lat);

    const lngLats: any[] = [];

    validSpots.forEach((spot, idx) => {
      const isActive = activeSpot === idx;
      const lngLat = new window.AMap.LngLat(spot.lng!, spot.lat!);
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
  const handleSpotClick = (idx: number) => {
    setActiveSpot(idx);
    const spot = days[currentDay]?.spots[idx];
    if (spot?.lng && spot?.lat && mapInstance.current) {
      mapInstance.current.setCenter([spot.lng, spot.lat]);
      mapInstance.current.setZoom(15);
    }
  };

  const dayData = days[currentDay];

  return (
    <div className="tripmap-root">
      {/* 顶部工具栏 */}
      <header className="tripmap-header">
        <button className="back-btn" onClick={onBack}>← 返回对话</button>
        <div className="day-tabs">
          {days.map((d, i) => (
            <button
              key={i}
              className={`day-tab${currentDay === i ? " active" : ""}`}
              style={currentDay === i ? { background: DAY_COLORS[i % DAY_COLORS.length] } : {}}
              onClick={() => { setCurrentDay(i); setActiveSpot(null); }}
            >
              第{d.day}天
            </button>
          ))}
        </div>
      </header>

      <div className="tripmap-body">
        {/* 左侧地图 */}
        <div className="map-pane">
          {!amapKey && (
            <div className="map-placeholder">
              <p>⚠️ 未配置高德地图 Key</p>
              <p>请在 <code>.env</code> 中设置 <code>AMAP_API_KEY</code></p>
            </div>
          )}
          <div ref={mapRef} className="amap-container" />
        </div>

        {/* 右侧时间轴 */}
        <aside className="timeline-pane">
          <h3 className="timeline-title">
            第{dayData?.day}天行程
          </h3>
          <ul className="timeline">
            {dayData?.spots.map((spot, idx) => (
              <li
                key={idx}
                className={`tl-node${activeSpot === idx ? " active" : ""}`}
                onClick={() => handleSpotClick(idx)}
              >
                <div
                  className="tl-dot"
                  style={{ background: DAY_COLORS[currentDay % DAY_COLORS.length] }}
                >
                  {idx + 1}
                </div>
                <div className="tl-content">
                  <span className="tl-time">{spot.time}</span>
                  <span className="tl-name">{spot.name}</span>
                  <span className="tl-duration">{spot.duration}</span>
                </div>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}
