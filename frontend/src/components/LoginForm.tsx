import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  TextInput,
  PasswordInput,
  Button,
  Paper,
  Title,
  Alert,
  Stack,
} from "@mantine/core";
import { useForm, isNotEmpty } from "@mantine/form";
import { authApi } from "../api/client";

function LoginForm() {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const form = useForm({
    mode: "controlled",
    initialValues: {
      username: "",
      password: "",
    },
    validate: {
      username: isNotEmpty("Username is required"),
      password: isNotEmpty("Password is required"),
    },
  });

  const handleSubmit = async (values: { username: string; password: string }) => {
    setError("");
    setLoading(true);
    try {
      const response = await authApi.login(values.username, values.password);
      localStorage.setItem("token", response.access_token);
      navigate("/files");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Paper withBorder shadow="md" p={30} radius="md" style={{ maxWidth: 360 }}>
      <Title order={3} ta="center" mb="md">
        Login
      </Title>

      {error && (
        <Alert color="red" mb="md" variant="light">
          {error}
        </Alert>
      )}

      <form onSubmit={form.onSubmit(handleSubmit)}>
        <Stack>
          <TextInput
            label="Username"
            placeholder="Enter your username"
            {...form.getInputProps("username")}
          />
          <PasswordInput
            label="Password"
            placeholder="Enter your password"
            {...form.getInputProps("password")}
          />
          <Button type="submit" loading={loading} fullWidth>
            Sign In
          </Button>
        </Stack>
      </form>
    </Paper>
  );
}

export default LoginForm;
