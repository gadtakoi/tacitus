import { Routes, Route } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import FilesPage from "./pages/FilesPage";
import StreamsPage from "./pages/StreamsPage";

function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/files" element={<FilesPage />} />
      <Route path="/streams" element={<StreamsPage />} />
    </Routes>
  );
}

export default App;
