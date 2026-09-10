import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppHeader from "../components/AppHeader";
import SettingsViewer from "../components/SettingsViewer";
import { authApi, clearTokens, hasTokens, setSessionExpiredHandler } from "../api/client";

function SettingsPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState<string | null>(null);

  // Redirect to login when the session is definitively dead (refresh failed).
  useEffect(() => {
    const handler = () => navigate("/", { replace: true });
    setSessionExpiredHandler(handler);
    return () => setSessionExpiredHandler(null);
  }, [navigate]);

  // Guard the route: no tokens — no settings page.
  useEffect(() => {
    if (!hasTokens()) {
      navigate("/", { replace: true });
      return;
    }
    authApi
      .me()
      .then((me) => setUsername(me.username))
      .catch(() => {
        /* 401 already handled by the session-expired handler */
      });
  }, [navigate]);

  const handleLogout = async () => {
    navigate("/", { replace: true });
    try {
      await authApi.logout();
    } catch {
      /* network failure is fine — tokens are dropped below regardless */
    } finally {
      clearTokens();
    }
  };

  return (
    <div>
      <AppHeader username={username} onLogout={handleLogout} />
      <SettingsViewer />
    </div>
  );
}

export default SettingsPage;
