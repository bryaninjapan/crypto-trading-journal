import type { ReactNode } from "react";

export function GlassCard({
  children,
  className = "",
  hover = false,
  noOverflow = false,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
  noOverflow?: boolean;
  onClick?: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={`glass-card ${hover ? "glass-card-hover" : ""} ${noOverflow ? "overflow-visible" : ""} p-lg ${className}`}
    >
      {children}
    </div>
  );
}
