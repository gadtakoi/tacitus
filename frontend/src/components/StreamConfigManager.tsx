import { useState, useEffect, useCallback } from "react";
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
  Modal,
  Switch,
  NumberInput,
  ThemeIcon,
  Divider,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { IconVideo, IconCrop, IconAlertCircle, IconSettings } from "@tabler/icons-react";
import {
  streamConfigsApi,
  MAX_CROP_PERCENT,
  MAX_CROP_TOTAL_PERCENT,
  type StreamConfig,
} from "../api/client";

interface CropFormValues {
  crop_enabled: boolean;
  crop_top_percent: number;
  crop_bottom_percent: number;
}

/** Percentage of the frame height left after a crop. */
function remainingPercent(top: number, bottom: number): number {
  return 100 - top - bottom;
}

function describeCrop(config: StreamConfig): string {
  if (!config.crop_enabled) {
    return "выключена";
  }
  return `сверху ${config.crop_top_percent}%, снизу ${config.crop_bottom_percent}%`;
}

function StreamConfigManager() {
  const [configs, setConfigs] = useState<StreamConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<StreamConfig | null>(null);
  const [saving, setSaving] = useState(false);

  const form = useForm<CropFormValues>({
    initialValues: {
      crop_enabled: false,
      crop_top_percent: 0,
      crop_bottom_percent: 0,
    },
    // Mirrors the pydantic schema and the CHECK constraints on the streams
    // table. Validating here only saves a round trip — the server rejects the
    // same values regardless.
    validate: {
      crop_top_percent: (value, values) => {
        if (!values.crop_enabled) return null;
        if (!Number.isInteger(value) || value < 0 || value > MAX_CROP_PERCENT) {
          return `От 0 до ${MAX_CROP_PERCENT}`;
        }
        return value + values.crop_bottom_percent > MAX_CROP_TOTAL_PERCENT
          ? `Сверху и снизу вместе — не более ${MAX_CROP_TOTAL_PERCENT}%`
          : null;
      },
      crop_bottom_percent: (value, values) => {
        if (!values.crop_enabled) return null;
        if (!Number.isInteger(value) || value < 0 || value > MAX_CROP_PERCENT) {
          return `От 0 до ${MAX_CROP_PERCENT}`;
        }
        return value + values.crop_top_percent > MAX_CROP_TOTAL_PERCENT
          ? `Сверху и снизу вместе — не более ${MAX_CROP_TOTAL_PERCENT}%`
          : null;
      },
    },
  });

  const loadConfigs = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await streamConfigsApi.list();
      setConfigs(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить конфигурации потоков");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfigs();
  }, [loadConfigs]);

  const openEditor = (config: StreamConfig) => {
    form.setValues({
      crop_enabled: config.crop_enabled,
      crop_top_percent: config.crop_top_percent,
      crop_bottom_percent: config.crop_bottom_percent,
    });
    form.resetDirty();
    setEditing(config);
  };

  const handleSubmit = async (values: CropFormValues) => {
    if (!editing) return;
    setSaving(true);
    setError("");
    try {
      const updated = await streamConfigsApi.update(editing.name, values);
      setConfigs((current) =>
        current.map((item) => (item.name === updated.name ? updated : item)),
      );
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  };

  const remaining = remainingPercent(
    form.values.crop_enabled ? form.values.crop_top_percent : 0,
    form.values.crop_enabled ? form.values.crop_bottom_percent : 0,
  );

  return (
    <Paper p="lg" m="md" withBorder>
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <ThemeIcon variant="light">
            <IconVideo size={18} />
          </ThemeIcon>
          <Title order={3}>Конфигурации потоков</Title>
        </Group>
        <Button variant="light" onClick={() => void loadConfigs()} loading={loading}>
          Обновить
        </Button>
      </Group>

      <Text size="sm" c="dimmed" mb="md">
        Обрезка применяется к записанным сегментам после того, как MediaMTX их закрыл: заданная
        доля кадра срезается сверху и снизу, а результат занимает место оригинала. Живой поток
        остаётся полным. Срезанные пиксели остаются в файле, поэтому обрезка убирает лишние зоны
        кадра, но не скрывает приватные.
      </Text>

      {error && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" mb="md">
          {error}
        </Alert>
      )}

      <Divider mb="md" />

      {loading && configs.length === 0 ? (
        <Group justify="center" p="lg">
          <Loader />
        </Group>
      ) : configs.length === 0 ? (
        <Text c="dimmed">
          Конфигураций потоков нет. Запись существует, пока путь объявлен в конфигурации MediaMTX;
          строка здесь задаёт только то, что Tacitus делает с готовыми сегментами.
        </Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Поток</Table.Th>
              <Table.Th>Источник</Table.Th>
              <Table.Th>Запись</Table.Th>
              <Table.Th>Обрезка</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {configs.map((config) => (
              <Table.Tr key={config.name}>
                <Table.Td>{config.name}</Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {config.source}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Badge color={config.record_enabled ? "green" : "gray"} variant="light">
                    {config.record_enabled ? "включена" : "выключена"}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    {config.crop_enabled && <IconCrop size={16} />}
                    <Text size="sm">{describeCrop(config)}</Text>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconSettings size={14} />}
                    onClick={() => openEditor(config)}
                  >
                    Настроить
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal
        opened={editing !== null}
        onClose={() => setEditing(null)}
        title={editing ? `Обрезка кадра: ${editing.name}` : ""}
      >
        <form onSubmit={form.onSubmit((values) => void handleSubmit(values))}>
          <Stack>
            <Switch
              label="Обрезать записанные сегменты"
              {...form.getInputProps("crop_enabled", { type: "checkbox" })}
            />
            <NumberInput
              label="Срезать сверху, %"
              min={0}
              max={MAX_CROP_PERCENT}
              allowDecimal={false}
              disabled={!form.values.crop_enabled}
              {...form.getInputProps("crop_top_percent")}
            />
            <NumberInput
              label="Срезать снизу, %"
              min={0}
              max={MAX_CROP_PERCENT}
              allowDecimal={false}
              disabled={!form.values.crop_enabled}
              {...form.getInputProps("crop_bottom_percent")}
            />
            <Text size="sm" c={remaining < 100 ? "yellow" : "dimmed"}>
              Остаётся {remaining}% высоты кадра.
            </Text>
            <Text size="xs" c="dimmed">
              Новые настройки применяются к сегментам при следующем обходе. Уже обрезанные
              сегменты пересчитываются от исходной геометрии, поэтому значения можно менять и
              возвращать к нулю.
            </Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setEditing(null)} disabled={saving}>
                Отмена
              </Button>
              <Button type="submit" loading={saving}>
                Сохранить
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Paper>
  );
}

export default StreamConfigManager;
