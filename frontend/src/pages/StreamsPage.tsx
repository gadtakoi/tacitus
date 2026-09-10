import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppHeader from "../components/AppHeader";
import StreamConfigManager from "../components/StreamConfigManager";
import { authApi, clearTokens, hasTokens, setSessionExpiredHandler } from "../api/client";

function StreamsPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState<string | null>(null);

  // Redirect to login when the session is definitively dead (refresh failed).
  useEffect(() => {
    const handler = () => navigate("/", { replace: true });
    setSessionExpiredHandler(handler);
    return () => setSessionExpiredHandler(null);
  }, [navigate]);

  // Guard the route: no tokens — no streams page.
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
      <StreamConfigManager />
    </div>
  );
}

export default StreamsPage;
