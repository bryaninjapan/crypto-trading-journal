import type { ReactNode } from "react";

export function GlassCard({
  children,
  className = "",
  hover = false,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}) {
  return (
    <div className={`glass-card ${hover ? "glass-card-hover" : ""} p-lg ${className}`}>
      {children}
    </div>
  );
}
