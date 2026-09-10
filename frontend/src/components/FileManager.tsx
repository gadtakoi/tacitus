import { useState, useEffect, useCallback } from "react";
import {
  Paper,
  Title,
  Text,
  Group,
  Button,
  Table,
  Loader,
  Alert,
  ThemeIcon,
  Divider,
  FileButton,
} from "@mantine/core";
import {
  IconBucket,
  IconFile,
  IconUpload,
  IconTrash,
  IconDownload,
  IconChartBar,
  IconAlertCircle,
  IconVideo,
} from "@tabler/icons-react";
import { filesApi, checkUploadSize, formatBytes, type StatsResponse } from "../api/client";

function FileManager() {
  const [files, setFiles] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [stats, setStats] = useState<StatsResponse | null>(null);

  /**
   * Load one page. Without a cursor this replaces the list (first page or a
   * reload after upload/delete); with one it appends the next page.
   */
  const loadFiles = useCallback(async (from: string | null = null) => {
    if (from) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    setError("");
    try {
      const data = await filesApi.list(from);
      if (data && Array.isArray(data.files)) {
        setFiles((previous) => (from ? [...previous, ...data.files] : data.files));
        setCursor(data.next_cursor ?? null);
      } else {
        console.error("Invalid files response", data);
        setFiles([]);
        setCursor(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const data = await filesApi.stats();
      if (
        data &&
        typeof data === "object" &&
        typeof data.total_files === "number" &&
        typeof data.average_size_bytes === "number"
      ) {
        setStats(data as StatsResponse);
      } else {
        console.error("Invalid stats response", data);
        setStats(null);
      }
    } catch (err) {
      console.error("Failed to load stats", err);
      setStats(null);
    }
  }, []);

  useEffect(() => {
    loadFiles();
    loadStats();
  }, [loadFiles, loadStats]);

  const handleUpload = async (file: File | null) => {
    if (!file) return;
    const sizeError = checkUploadSize(file);
    if (sizeError) {
      setError(sizeError);
      return;
    }
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
      <Title order={2} mb="md">
        Files
      </Title>

      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" mb="md">
          {error}
        </Alert>
      )}

      {stats && (
        <Paper withBorder shadow="sm" p="md" radius="md" mb="md">
          <Group mb="xs">
            <ThemeIcon variant="light" color="blue" size="lg">
              <IconBucket size={20} />
            </ThemeIcon>
            <Title order={4}>Bucket Stats</Title>
          </Group>
          <Divider mb="sm" />
          <Group gap="xs" wrap="wrap">
            <Group gap={4}>
              <IconFile size={16} opacity={0.7} />
              <Text size="sm">
                Total files: <strong>{stats.total_files ?? 0}</strong>
              </Text>
            </Group>
            <Group gap={4}>
              <IconChartBar size={16} opacity={0.7} />
              <Text size="sm">
                Total size: <strong>{stats.total_size_human ?? "0 B"}</strong>
              </Text>
            </Group>
            <Group gap={4}>
              <IconChartBar size={16} opacity={0.7} />
              <Text size="sm">
                Average:{" "}
                <strong>
                  {stats.average_size_bytes != null
                    ? formatBytes(stats.average_size_bytes)
                    : "N/A"}
                </strong>
              </Text>
            </Group>
            {stats.largest_file && (
              <Group gap={4}>
                <IconFile size={16} opacity={0.7} />
                <Text size="sm">
                  Largest: <strong>{stats.largest_file.filename}</strong>{" "}
                  ({stats.largest_file.size_human ??
                    `${stats.largest_file.size_bytes ?? 0} bytes`})
                </Text>
              </Group>
            )}
            {/* Archived recordings share the bucket but not this page: they
                are counted apart so the numbers above describe the list. */}
            <Group gap={4}>
              <IconVideo size={16} opacity={0.7} />
              <Text size="sm">
                Recordings archived: <strong>{stats.archive_files ?? 0}</strong>{" "}
                ({stats.archive_size_human ?? "0 B"})
              </Text>
            </Group>
          </Group>
        </Paper>
      )}

      <Group mb="md">
        <FileButton onChange={handleUpload} accept="*/*">
          {(props) => (
            <Button
              leftSection={<IconUpload size={16} />}
              loading={uploading}
              {...props}
            >
              Upload file
            </Button>
          )}
        </FileButton>
        {uploading && <Loader size="sm" />}
      </Group>

      {loading ? (
        <Loader />
      ) : files.length === 0 ? (
        <Text c="dimmed">No files found.</Text>
      ) : (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th style={{ textAlign: "right" }}>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {files.map((filename) => (
              <Table.Tr key={filename}>
                <Table.Td>{filename}</Table.Td>
                <Table.Td style={{ textAlign: "right" }}>
                  <Button
                    variant="light"
                    size="xs"
                    leftSection={<IconDownload size={14} />}
                    onClick={() => handleDownload(filename)}
                    mr="xs"
                  >
                    Download
                  </Button>
                  <Button
                    variant="light"
                    color="red"
                    size="xs"
                    leftSection={<IconTrash size={14} />}
                    onClick={() => handleDelete(filename)}
                  >
                    Delete
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {/* The cursor, not the page size, decides whether more files exist: a
          short page can still be followed by another one. */}
      {cursor && (
        <Group justify="center" mt="md">
          <Button
            variant="default"
            loading={loadingMore}
            onClick={() => loadFiles(cursor)}
          >
            Load more
          </Button>
        </Group>
      )}
    </div>
  );
}

export default FileManager;
