import { useState, useEffect, useCallback } from "react";
import { filesApi, type FileListResponse, type StatsResponse } from "../api/client";

function FileManager() {
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [stats, setStats] = useState<StatsResponse | null>(null);

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

  const loadStats = useCallback(async () => {
    try {
      const data: StatsResponse = await filesApi.stats();
      setStats(data);
    } catch (err) {
      // Silently ignore stats errors to avoid cluttering UI
      console.error("Failed to load stats", err);
    }
  }, []);

  useEffect(() => {
    loadFiles();
    loadStats();
  }, [loadFiles, loadStats]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await filesApi.upload(file);
      await loadFiles();
      await loadStats();
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
      await loadStats();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <div style={{ maxWidth: 800, margin: "40px auto" }}>
      <h2>Files</h2>
      {error && <div style={{ color: "red", marginBottom: 12 }}>{error}</div>}
      {stats && (
        <div
          style={{
            marginBottom: 16,
            padding: 12,
            border: "1px solid #ddd",
            borderRadius: 6,
            background: "#fafafa",
          }}
        >
          <strong>Bucket Stats</strong>
          <div style={{ marginTop: 6, fontSize: 14 }}>
            Total files: <strong>{stats.total_files}</strong> &middot; Total size:{" "}
            <strong>{stats.total_size_human}</strong> &middot; Average:{" "}
            <strong>{stats.average_size_bytes.toFixed(0)} bytes</strong>
            {stats.largest_file && (
              <span>
                {" "}
                &middot; Largest: <strong>{stats.largest_file.filename}</strong> ({" "}
                {stats.largest_file.size_human || `${stats.largest_file.size_bytes} bytes`})
              </span>
            )}
          </div>
        </div>
      )}
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
