import type { ReactNode } from "react";

export function GlassCard({
  children,
  className = "",
  hover = false,
  noOverflow = false,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
  noOverflow?: boolean;
}) {
  return (
    <div className={`glass-card ${hover ? "glass-card-hover" : ""} ${noOverflow ? "overflow-visible" : ""} p-lg ${className}`}>
      {children}
    </div>
  );
}
