import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { settingsApi } from "../api/client";

/** Authenticated sections, in the order they appear in the header. */
const NAV = [
  { path: "/files", label: "Файлы" },
  { path: "/streams", label: "Потоки" },
  { path: "/settings", label: "Параметры" },
];

interface AppHeaderProps {
  /** Signed-in user, or null while the session probe is still in flight. */
  username: string | null;
  onLogout: () => void;
}

/**
 * Header shared by every authenticated page: product name, the version the
 * backend is running, navigation to the other sections and logout.
 *
 * The version answers "which build am I looking at?" — images are built on the
 * deployment host, so the browser is the only place an operator can read it
 * without shelling into a container.
 */
function AppHeader({ username, onLogout }: AppHeaderProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    settingsApi
      .version()
      .then((response) => setVersion(response.version))
      .catch(() => {
        /* A missing version must not break the header: it stays hidden. */
      });
  }, []);

  return (
    <header
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "0 20px",
        borderBottom: "1px solid #ccc",
      }}
    >
      <div style={{ display: "flex", gap: "8px", alignItems: "baseline" }}>
        <h1>Tacitus</h1>
        {version && (
          <span style={{ opacity: 0.6, fontSize: "12px" }} title="Версия сервиса">
            v{version}
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
        {NAV.filter((item) => item.path !== location.pathname).map((item) => (
          <button key={item.path} onClick={() => navigate(item.path)}>
            {item.label}
          </button>
        ))}
        <button onClick={onLogout}>Logout{username ? ` (${username})` : ""}</button>
      </div>
    </header>
  );
}

export default AppHeader;
