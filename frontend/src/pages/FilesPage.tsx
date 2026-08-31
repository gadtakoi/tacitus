import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import FileManager from "../components/FileManager";
import { authApi, clearTokens, hasTokens, setSessionExpiredHandler } from "../api/client";

function FilesPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState<string | null>(null);

  // Redirect to login when the session is definitively dead (refresh failed).
  useEffect(() => {
    const handler = () => navigate("/", { replace: true });
    setSessionExpiredHandler(handler);
    return () => setSessionExpiredHandler(null);
  }, [navigate]);

  // Guard the route: no tokens — no files page.
  useEffect(() => {
    if (!hasTokens()) {
      navigate("/", { replace: true });
      return;
    }
    // Probe the session: validates the access token (or silently refreshes it).
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
      // Server-side revocation of access + refresh tokens. Must run while the
      // tokens are still stored: logout() reads the refresh token from localStorage.
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
        <button onClick={handleLogout}>
          Logout{username ? ` (${username})` : ""}
        </button>
      </header>
      <FileManager />
    </div>
  );
}

export default FilesPage;
