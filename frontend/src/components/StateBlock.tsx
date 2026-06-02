export function Loading() {
  return (
    <div className="flex items-center justify-center py-20 text-on-surface-variant">
      <span className="material-symbols-outlined animate-spin">progress_activity</span>
    </div>
  );
}

export function ErrorBlock({ error }: { error: string }) {
  return (
    <div className="glass-card !p-lg text-bearish">
      <div className="text-body-bold">加载失败</div>
      <div className="mt-2 font-mono text-data-mono text-on-surface-variant">{error}</div>
    </div>
  );
}

export function Empty({ text = "暂无数据" }: { text?: string }) {
  return <div className="py-16 text-center text-on-surface-variant">{text}</div>;
}
