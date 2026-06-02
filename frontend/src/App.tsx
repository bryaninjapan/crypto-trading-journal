import { Routes, Route } from "react-router-dom";
import { Nav } from "./components/Nav";
import { Dashboard } from "./pages/Dashboard";
import { Journal } from "./pages/Journal";
import { PositionDetail } from "./pages/PositionDetail";
import { Analytics } from "./pages/Analytics";
import { Profile } from "./pages/Profile";

export function App() {
  return (
    <div className="min-h-screen md:pl-20">
      <Nav />
      <main className="mx-auto w-full max-w-7xl px-margin-mobile py-lg pb-28 md:px-margin-desktop md:pb-lg">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/positions/:id" element={<PositionDetail />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/profile" element={<Profile />} />
        </Routes>
      </main>
    </div>
  );
}
