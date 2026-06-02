// plotly.js-dist-min 不带类型声明，这里给一个最小声明（图表数据用宽松类型）。
declare module "plotly.js-dist-min" {
  const Plotly: {
    react: (
      el: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ) => Promise<void>;
    newPlot: (
      el: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ) => Promise<void>;
    purge: (el: HTMLElement) => void;
  };
  export default Plotly;
}
