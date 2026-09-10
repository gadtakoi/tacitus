import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
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
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0 20px",
          borderBottom: "1px solid #ccc",
        }}
      >
        <h1>Tacitus</h1>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <button onClick={() => navigate("/files")}>Файлы</button>
          <button onClick={() => navigate("/settings")}>Параметры</button>
          <button onClick={handleLogout}>
            Logout{username ? ` (${username})` : ""}
          </button>
        </div>
      </header>
      <StreamConfigManager />
    </div>
  );
}

export default StreamsPage;
