import { Center, Stack, Title } from "@mantine/core";
import LoginForm from "../components/LoginForm";

function LoginPage() {
  return (
    <Center style={{ minHeight: "100vh" }}>
      <Stack align="center" gap="lg">
        <Title order={1}>Tacitus</Title>
        <LoginForm />
      </Stack>
    </Center>
  );
}

export default LoginPage;
