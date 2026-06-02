export function Pagination({
  limit,
  setLimit,
  offset,
  setOffset,
  total,
}: {
  limit: number;
  setLimit: (v: number) => void;
  offset: number;
  setOffset: (v: number) => void;
  total: number;
}) {
  const pageNum = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);

  return (
    <div className="flex items-center justify-between border-t border-white/[0.06] px-lg py-md">
      {/* Row count selector */}
      <div className="flex items-center gap-2">
        <span className="text-data-mono text-sm text-on-surface-variant">Rows:</span>
        {[10, 20, 50].map((n) => (
          <button
            key={n}
            onClick={() => {
              setLimit(n);
              setOffset(0);
            }}
            className={`rounded px-2 py-1 text-data-mono text-xs transition-colors ${
              limit === n
                ? "bg-primary/20 text-primary"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            {n}
          </button>
        ))}
      </div>

      {/* Page info */}
      <div className="text-data-mono text-sm text-on-surface-variant">
        Page {pageNum} of {totalPages} ({total} total)
      </div>

      {/* Navigation buttons */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => setOffset(Math.max(0, offset - limit))}
          disabled={offset === 0}
          className="rounded px-2 py-1 text-data-mono text-xs disabled:opacity-40 hover:bg-surface-container"
        >
          ← Prev
        </button>
        <button
          onClick={() => setOffset(offset + limit)}
          disabled={offset + limit >= total}
          className="rounded px-2 py-1 text-data-mono text-xs disabled:opacity-40 hover:bg-surface-container"
        >
          Next →
        </button>
      </div>
    </div>
  );
}
