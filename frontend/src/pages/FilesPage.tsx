import { useNavigate } from "react-router-dom";
import FileManager from "../components/FileManager";

function FilesPage() {
  const navigate = useNavigate();

  const handleLogout = () => {
    localStorage.removeItem("token");
    navigate("/");
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
        <button onClick={handleLogout}>Logout</button>
      </header>
      <FileManager />
    </div>
  );
}

export default FilesPage;
