import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Paper,
  Title,
  Text,
  Group,
  Stack,
  Button,
  Table,
  Loader,
  Alert,
  Badge,
  TextInput,
  ThemeIcon,
  Divider,
} from "@mantine/core";
import {
  IconAdjustments,
  IconAlertCircle,
  IconLock,
  IconSearch,
} from "@tabler/icons-react";
import { settingsApi, type SettingItem } from "../api/client";

/** Settings in backend order, split into the groups the backend assigned. */
function groupItems(items: SettingItem[]): [string, SettingItem[]][] {
  const groups = new Map<string, SettingItem[]>();
  for (const item of items) {
    const group = groups.get(item.group);
    if (group) {
      group.push(item);
    } else {
      groups.set(item.group, [item]);
    }
  }
  return [...groups.entries()];
}

/**
 * Value cell. A secret never arrives from the backend, so there is nothing to
 * hide behind a "show" toggle here: the badge reports only whether the
 * variable is configured.
 */
function SettingValue({ item }: { item: SettingItem }) {
  if (item.sensitive) {
    return (
      <Badge
        color={item.is_set ? "gray" : "yellow"}
        variant="light"
        leftSection={<IconLock size={12} />}
      >
        {item.is_set ? "задано, скрыто" : "не задано"}
      </Badge>
    );
  }
  if (item.value === "") {
    return (
      <Text size="sm" c="dimmed">
        (пусто)
      </Text>
    );
  }
  return (
    <Text size="sm" ff="monospace" style={{ wordBreak: "break-all" }}>
      {item.value}
    </Text>
  );
}

/**
 * Read-only view of the environment variables the backend process is running
 * with. Values come from the running configuration, so a variable left unset
 * shows the default the service actually applies, not an empty cell.
 */
function SettingsViewer() {
  const [items, setItems] = useState<SettingItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await settingsApi.list();
      setItems(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить параметры");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? items.filter(
          (item) =>
            item.name.toLowerCase().includes(needle) ||
            item.group.toLowerCase().includes(needle) ||
            // Secrets have no value to match against, and that is intended:
            // filtering must not become a way to probe a hidden value.
            (item.value ?? "").toLowerCase().includes(needle),
        )
      : items;
    return groupItems(filtered);
  }, [items, query]);

  return (
    <Paper p="lg" m="md" withBorder>
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <ThemeIcon variant="light">
            <IconAdjustments size={18} />
          </ThemeIcon>
          <Title order={3}>Параметры окружения</Title>
        </Group>
        <Button variant="light" onClick={() => void loadSettings()} loading={loading}>
          Обновить
        </Button>
      </Group>

      <Text size="sm" c="dimmed" mb="md">
        Действующая конфигурация backend-процесса: переменные окружения <code>TACITUS_*</code> со
        значениями, которые применяются сейчас. Переменная, не заданная в окружении, показана со
        значением по умолчанию. Параметры доступны только для чтения — изменение требует правки
        окружения и перезапуска сервиса. Значения паролей, ключей и токенов не передаются в
        браузер: для них показывается только факт наличия, а пароль внутри строк подключения
        заменён на <code>***</code>.
      </Text>

      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" mb="md">
          {error}
        </Alert>
      )}

      <TextInput
        placeholder="Поиск по имени или значению"
        leftSection={<IconSearch size={16} />}
        value={query}
        onChange={(event) => setQuery(event.currentTarget.value)}
        mb="md"
      />

      <Divider mb="md" />

      {loading && items.length === 0 ? (
        <Group justify="center" p="lg">
          <Loader />
        </Group>
      ) : groups.length === 0 ? (
        <Text c="dimmed">
          {items.length === 0 ? "Параметры недоступны." : "Ничего не найдено."}
        </Text>
      ) : (
        <Stack gap="xl">
          {groups.map(([group, groupItemsList]) => (
            <div key={group}>
              <Title order={5} mb="xs">
                {group}
              </Title>
              <Table striped highlightOnHover layout="fixed">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w="40%">Переменная</Table.Th>
                    <Table.Th>Значение</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {groupItemsList.map((item) => (
                    <Table.Tr key={item.name}>
                      <Table.Td>
                        <Text size="sm" ff="monospace" style={{ wordBreak: "break-all" }}>
                          {item.name}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <SettingValue item={item} />
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          ))}
        </Stack>
      )}
    </Paper>
  );
}

export default SettingsViewer;
