import {
  Alert,
  AlertIcon,
  Button,
  FormControl,
  FormLabel,
  Heading,
  Input,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  VStack,
} from "@chakra-ui/react";
import { useState } from "react";
import type { FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";

type Mode = "login" | "signup";

function AuthForm({ mode }: { mode: Mode }) {
  const { signIn, signUp } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setInfo(null);
    setSubmitting(true);

    const { error: authError } =
      mode === "login" ? await signIn(email, password) : await signUp(email, password);

    if (authError) {
      setError(authError);
    } else if (mode === "signup") {
      setInfo("Check your email to confirm your account.");
    }
    setSubmitting(false);
  }

  return (
    <form onSubmit={handleSubmit}>
      <VStack align="stretch" spacing={4}>
        {error && (
          <Alert status="error">
            <AlertIcon />
            {error}
          </Alert>
        )}
        {info && (
          <Alert status="success">
            <AlertIcon />
            {info}
          </Alert>
        )}
        <FormControl isRequired>
          <FormLabel>Email</FormLabel>
          <Input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
          />
        </FormControl>
        <FormControl isRequired>
          <FormLabel>Password</FormLabel>
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </FormControl>
        <Button type="submit" colorScheme="teal" isLoading={submitting}>
          {mode === "login" ? "Log in" : "Sign up"}
        </Button>
      </VStack>
    </form>
  );
}

export default function Login() {
  return (
    <VStack align="stretch" spacing={6} p={8} maxW="sm" mx="auto">
      <Heading size="md">AssetAuditor</Heading>
      <Tabs isFitted colorScheme="teal">
        <TabList>
          <Tab>Log in</Tab>
          <Tab>Sign up</Tab>
        </TabList>
        <TabPanels>
          <TabPanel px={0}>
            <AuthForm mode="login" />
          </TabPanel>
          <TabPanel px={0}>
            <AuthForm mode="signup" />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </VStack>
  );
}
