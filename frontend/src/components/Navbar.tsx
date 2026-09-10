import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Boxes,
  UploadCloud,
  ListChecks,
  Home as HomeIcon,
  Globe,
  LogIn,
  LogOut,
  UserCog,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/AuthContext";

export default function Navbar() {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const { user, logout } = useAuth();

  const navItems = [
    { to: "/", label: t("nav.home"), icon: HomeIcon },
    { to: "/upload", label: t("nav.upload"), icon: UploadCloud },
    { to: "/tasks", label: t("nav.tasks"), icon: ListChecks },
    ...(user?.role === "admin"
      ? [{ to: "/admin", label: t("nav.admin"), icon: UserCog }]
      : []),
  ];

  const toggleLang = () => {
    i18n.changeLanguage(i18n.language === "zh" ? "en" : "zh");
  };

  return (
    <header className="sticky top-0 z-50 border-b border-gold-400/25 bg-ink-300/95">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
        {/* Logo */}
        <Link to="/" className="flex items-center gap-2.5 group">
          <Boxes className="h-7 w-7 text-gold-400" />
          <div className="flex flex-col leading-none">
            <span className="font-mono text-lg font-bold tracking-tight text-gold-300">
              {t("home.title")}
            </span>
          </div>
        </Link>

        {/* Desktop Nav */}
        <nav className="hidden md:flex items-center gap-0">
          {navItems.map(({ to, label, icon: Icon }) => {
            const active = location.pathname === to;
            return (
              <Link
                key={to}
                to={to}
                className={`flex items-center gap-2 border-r border-gold-400/15 px-4 py-2 font-mono text-xs uppercase tracking-wider transition-colors ${
                  active
                    ? "bg-gold-400/10 text-gold-300"
                    : "text-gray-400 hover:bg-gold-400/5 hover:text-gold-200"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Right: user + language */}
        <div className="flex items-center gap-2">
          {user ? (
            <>
              <span className="hidden max-w-[8rem] truncate font-mono text-xs text-gold-300/80 sm:inline">
                {user.username}
              </span>
              <button
                onClick={logout}
                className="flex items-center gap-1.5 border border-gold-400/40 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-gold-300 transition-colors hover:bg-gold-400/10"
              >
                <LogOut className="h-3.5 w-3.5" />
                {t("nav.logout")}
              </button>
            </>
          ) : (
            <Link
              to="/login"
              className="flex items-center gap-1.5 border border-gold-400/40 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-gold-300 transition-colors hover:bg-gold-400/10"
            >
              <LogIn className="h-3.5 w-3.5" />
              {t("nav.login")}
            </Link>
          )}
          <button
            onClick={toggleLang}
            className="flex items-center gap-1.5 border border-gold-400/40 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-gold-300 transition-colors hover:bg-gold-400/10"
          >
            <Globe className="h-3.5 w-3.5" />
            {i18n.language === "zh" ? "中" : "EN"}
          </button>

          {/* Mobile menu button */}
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="md:hidden border border-gold-400/40 p-2 text-gold-300"
          >
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={menuOpen ? "M6 18L18 6M6 6l12 12" : "M4 6h16M4 12h16M4 18h16"} />
            </svg>
          </button>
        </div>
      </div>

      {/* Mobile Nav */}
      {menuOpen && (
        <nav className="md:hidden border-t border-gold-400/15 bg-ink-200/95 px-4 py-3">
          {navItems.map(({ to, label, icon: Icon }) => {
            const active = location.pathname === to;
            return (
              <Link
                key={to}
                to={to}
                onClick={() => setMenuOpen(false)}
                className={`flex items-center gap-2 border-l-2 px-4 py-3 font-mono text-xs uppercase tracking-wider transition-colors ${
                  active ? "border-gold-400 bg-gold-400/10 text-gold-300" : "border-transparent text-gray-400"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            );
          })}
          {!user && (
            <Link
              to="/login"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-2 border-l-2 border-transparent px-4 py-3 font-mono text-xs uppercase tracking-wider text-gray-400"
            >
              <LogIn className="h-3.5 w-3.5" />
              {t("nav.login")}
            </Link>
          )}
        </nav>
      )}
    </header>
  );
}
