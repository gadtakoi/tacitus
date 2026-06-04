import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { authApi } from "../api/client";

function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const response = await authApi.login(username, password);
      localStorage.setItem("token", response.access_token);
      navigate("/files");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ maxWidth: 320, margin: "40px auto" }}>
      <h2>Login</h2>
      {error && <div style={{ color: "red", marginBottom: 12 }}>{error}</div>}
      <div style={{ marginBottom: 12 }}>
        <label>
          Username
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            style={{ display: "block", width: "100%", padding: 8 }}
          />
        </label>
      </div>
      <div style={{ marginBottom: 12 }}>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ display: "block", width: "100%", padding: 8 }}
          />
        </label>
      </div>
      <button type="submit" style={{ padding: "8px 16px" }}>
        Sign In
      </button>
    </form>
  );
}

export default LoginForm;
