import { useEffect, useRef } from "react";

// Use global Plotly from CDN (loaded in index.html)
const Plotly = (window as any).Plotly;

type Trace = Record<string, unknown>;
type Layout = Record<string, unknown>;

// 薄封装：透明背景 + 暗色主题（DESIGN.md：图表 100% 透明，trendline glow）。
const DARK_LAYOUT: Layout = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#c7c4d7", family: "JetBrains Mono, monospace", size: 11 },
  margin: { l: 48, r: 16, t: 8, b: 32 },
  xaxis: { gridcolor: "rgba(255,255,255,0.06)", zeroline: false },
  yaxis: { gridcolor: "rgba(255,255,255,0.06)", zeroline: false },
  legend: { orientation: "h", y: -0.2, font: { color: "#c7c4d7" } },
  showlegend: false,
  hovermode: "x unified",
  hoverlabel: {
    bgcolor: "#1c1f29",
    bordercolor: "#464554",
    font: { color: "#e0e2ef", family: "JetBrains Mono, monospace", size: 11 },
  },
};

export function PlotlyChart({
  data,
  layout,
  height = 280,
}: {
  data: Trace[];
  layout?: Layout;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !Plotly) return;
    try {
      Plotly.react(
        ref.current,
        data,
        { ...DARK_LAYOUT, ...layout, height },
        { displayModeBar: false, responsive: true },
      );
    } catch (e) {
      console.error("Plotly rendering error:", e);
    }
  }, [data, layout, height]);

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(() => {
      if (ref.current) (Plotly as any).relayout(ref.current, { autosize: true });
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  return <div ref={ref} style={{ width: "100%", height, overflow: "visible" }} />;
}
