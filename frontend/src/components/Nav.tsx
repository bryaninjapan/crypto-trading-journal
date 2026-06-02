import { NavLink } from "react-router-dom";

const ITEMS = [
  { to: "/", icon: "dashboard", label: "Dashboard", end: true },
  { to: "/journal", icon: "menu_book", label: "Journal", end: false },
  { to: "/analytics", icon: "monitoring", label: "Analytics", end: false },
  { to: "/profile", icon: "person", label: "Profile", end: false },
];

export function Nav() {
  return (
    <>
      {/* 桌面侧栏 */}
      <aside className="hidden md:flex fixed left-0 top-0 h-full w-20 flex-col items-center gap-md border-r border-white/[0.08] bg-surface/40 py-lg backdrop-blur-xl">
        <span className="material-symbols-outlined text-primary text-3xl">candlestick_chart</span>
        <nav className="mt-md flex flex-col gap-sm">
          {ITEMS.map((it) => (
            <NavLink
              key={it.to}
              to={it.to}
              end={it.end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-1 rounded-md px-3 py-2 transition-colors ${
                  isActive
                    ? "bg-primary/15 text-primary"
                    : "text-on-surface-variant hover:text-on-surface"
                }`
              }
            >
              <span className="material-symbols-outlined">{it.icon}</span>
              <span className="text-[10px]">{it.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* 移动端浮岛 */}
      <nav className="md:hidden fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 gap-1 rounded-full border border-white/[0.08] bg-surface/70 px-2 py-2 backdrop-blur-xl shadow-2xl">
        {ITEMS.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.end}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 rounded-full px-4 py-1 transition-colors ${
                isActive ? "bg-primary/15 text-primary" : "text-on-surface-variant"
              }`
            }
          >
            <span className="material-symbols-outlined text-[22px]">{it.icon}</span>
            <span className="text-[10px]">{it.label}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
}
