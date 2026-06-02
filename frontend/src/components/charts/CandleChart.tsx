import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  type IChartApi,
  type CandlestickData,
  type UTCTimestamp,
} from "lightweight-charts";
import type { KlinesResponse } from "../../api/types";

// TradingView lightweight-charts —— 蜡烛详情（DESIGN.md：终端级暗色蜡烛）。
export function CandleChart({ data, height = 360 }: { data: KlinesResponse; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart: IChartApi = createChart(ref.current, {
      height,
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

    const series = chart.addCandlestickSeries({
      upColor: "#10B981",
      downColor: "#EF4444",
      wickUpColor: "#10B981",
      wickDownColor: "#EF4444",
      borderVisible: false,
    });

    const candles: CandlestickData[] = data.candles.map((k) => ({
      time: (k.t / 1000) as UTCTimestamp,
      open: k.o,
      high: k.h,
      low: k.l,
      close: k.c,
    }));
    series.setData(candles);

    // 开/平仓价格线
    const { entry, exit } = data.markers;
    if (entry?.price) {
      series.createPriceLine({
        price: entry.price,
        color: "#7bd0ff",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Open",
      });
    }
    if (exit?.price) {
      series.createPriceLine({
        price: exit.price,
        color: "#c0c1ff",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Close",
      });
    }
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    });
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [data, height]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
