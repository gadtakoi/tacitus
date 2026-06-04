import { useState, useEffect, useCallback } from "react";
import { filesApi, type FileListResponse } from "../api/client";

function FileManager() {
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data: FileListResponse = await filesApi.list();
      setFiles(data.files);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await filesApi.upload(file);
      await loadFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleDownload = async (filename: string) => {
    try {
      const data = await filesApi.getUrl(filename);
      window.open(data.url, "_blank");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  };

  const handleDelete = async (filename: string) => {
    if (!window.confirm(`Delete "${filename}"?`)) return;
    setError("");
    try {
      await filesApi.delete(filename);
      await loadFiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <div style={{ maxWidth: 800, margin: "40px auto" }}>
      <h2>Files</h2>
      {error && <div style={{ color: "red", marginBottom: 12 }}>{error}</div>}
      <div style={{ marginBottom: 16 }}>
        <label>
          Upload file:{" "}
          <input type="file" onChange={handleUpload} disabled={uploading} />
        </label>
        {uploading && <span> Uploading...</span>}
      </div>
      {loading ? (
        <p>Loading...</p>
      ) : files.length === 0 ? (
        <p>No files found.</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>Name</th>
              <th style={{ textAlign: "right", borderBottom: "1px solid #ccc" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {files.map((filename) => (
              <tr key={filename}>
                <td style={{ padding: "8px 0", borderBottom: "1px solid #eee" }}>{filename}</td>
                <td style={{ textAlign: "right", borderBottom: "1px solid #eee" }}>
                  <button onClick={() => handleDownload(filename)} style={{ marginRight: 8 }}>
                    Download
                  </button>
                  <button onClick={() => handleDelete(filename)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default FileManager;
