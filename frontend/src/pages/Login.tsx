import { FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Loader2, LogIn } from "lucide-react";
import { useAuth } from "../auth/AuthContext";

export default function Login() {
  const { t } = useTranslation();
  const { user, ready, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from =
    (location.state as { from?: { pathname?: string } } | null)?.from
      ?.pathname || "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (ready && user) {
    return <Navigate to={from} replace />;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.loginFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-[70vh] max-w-md items-center px-4 py-16">
      <form onSubmit={handleSubmit} className="gold-card w-full p-8">
        <h1 className="text-2xl font-bold text-gold-300">{t("auth.title")}</h1>
        <p className="mt-2 text-sm text-gray-400">{t("auth.subtitle")}</p>

        <label className="mt-6 mb-2 block text-sm font-medium text-gold-300">
          {t("auth.username")}
        </label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          className="w-full border border-gold-400/20 bg-ink-400/50 px-4 py-2.5 text-sm text-gray-200 outline-none focus:border-gold-400/50"
          required
        />

        <label className="mt-4 mb-2 block text-sm font-medium text-gold-300">
          {t("auth.password")}
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          className="w-full border border-gold-400/20 bg-ink-400/50 px-4 py-2.5 text-sm text-gray-200 outline-none focus:border-gold-400/50"
          required
        />

        {error && (
          <p className="mt-4 text-sm text-red-300">{error}</p>
        )}

        <button type="submit" disabled={submitting} className="gold-btn mt-6 w-full">
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <LogIn className="h-4 w-4" />
          )}
          {t("auth.submit")}
        </button>
      </form>
    </div>
  );
}
