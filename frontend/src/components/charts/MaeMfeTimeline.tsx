import { useEffect, useRef } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";
import type { MaeMfeTimeline } from "../../api/types";

export function MaeMfeTimeline({ data }: { data: MaeMfeTimeline }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !data.timeline.length) return;

    const chart: IChartApi = createChart(ref.current, {
      height: 240,
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0,0,0,0)" },
        textColor: "#c7c4d7",
        fontFamily: "JetBrains Mono, monospace",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)", timeVisible: true },
      crosshair: { mode: 0 },
    });

    const maeSeries = chart.addLineSeries({
      color: "#EF4444",
      lineWidth: 2,
      title: "MAE %",
    });

    const mfeSeries = chart.addLineSeries({
      color: "#10B981",
      lineWidth: 2,
      title: "MFE %",
    });

    const maeData = data.timeline.map((p) => ({
      time: (p.t / 1000) as any,
      value: p.mae,
    }));

    const mfeData = data.timeline.map((p) => ({
      time: (p.t / 1000) as any,
      value: p.mfe,
    }));

    maeSeries.setData(maeData);
    mfeSeries.setData(mfeData);

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [data]);

  return <div ref={ref} style={{ width: "100%", height: 240 }} />;
}
