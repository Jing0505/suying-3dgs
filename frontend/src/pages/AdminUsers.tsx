import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, UserPlus } from "lucide-react";
import {
  createUser,
  listUsers,
  patchUser,
} from "../api/client";
import type { AuthUser } from "../types";

export default function AdminUsers() {
  const { t } = useTranslation();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setUsers(await listUsers());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await createUser(username.trim(), password);
      setUsername("");
      setPassword("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setSubmitting(false);
    }
  };

  const toggleDisabled = async (user: AuthUser) => {
    try {
      await patchUser(user.id, !user.disabled);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <h1 className="text-3xl font-bold text-gold-300">{t("admin.title")}</h1>
      <p className="mt-2 text-sm text-gray-400">{t("admin.subtitle")}</p>

      <form onSubmit={handleCreate} className="gold-card mt-8 grid gap-4 p-6 sm:grid-cols-3">
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder={t("auth.username")}
          className="border border-gold-400/20 bg-ink-400/50 px-3 py-2 text-sm text-gray-200 outline-none focus:border-gold-400/50"
          required
          minLength={2}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={t("auth.password")}
          className="border border-gold-400/20 bg-ink-400/50 px-3 py-2 text-sm text-gray-200 outline-none focus:border-gold-400/50"
          required
          minLength={6}
        />
        <button type="submit" disabled={submitting} className="gold-btn">
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <UserPlus className="h-4 w-4" />
          )}
          {t("admin.create")}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-red-300">{error}</p>}

      <div className="gold-card mt-8 overflow-x-auto">
        {loading ? (
          <div className="flex justify-center p-10">
            <Loader2 className="h-6 w-6 animate-spin text-gold-400" />
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gold-400/15 text-xs uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">{t("auth.username")}</th>
                <th className="px-4 py-3">{t("admin.role")}</th>
                <th className="px-4 py-3">{t("admin.status")}</th>
                <th className="px-4 py-3">{t("admin.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-gold-400/10">
                  <td className="px-4 py-3 text-gold-200">{u.username}</td>
                  <td className="px-4 py-3 text-gray-400">{u.role}</td>
                  <td className="px-4 py-3">
                    {u.disabled ? t("admin.disabled") : t("admin.active")}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => toggleDisabled(u)}
                      className="text-xs text-gold-400 hover:text-gold-200"
                    >
                      {u.disabled ? t("admin.enable") : t("admin.disable")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
