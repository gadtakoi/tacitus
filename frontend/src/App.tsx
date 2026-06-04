import { Routes, Route } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import FilesPage from "./pages/FilesPage";

function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/files" element={<FilesPage />} />
    </Routes>
  );
}

export default App;
