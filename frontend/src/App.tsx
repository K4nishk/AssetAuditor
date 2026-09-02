import { Button, Center, Heading, Spinner, Text, VStack } from "@chakra-ui/react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import Login from "./routes/Login";
import ManualEntryAccountBalance from "./routes/ManualEntryAccountBalance";
import ManualEntryPortfolio from "./routes/ManualEntryPortfolio";
import ParseConfirm from "./routes/ParseConfirm";

// Post-auth screens (profile/onboarding, rooms, dashboards) land per their
// own issues (mvp.md M1-M3); this is a placeholder shell until AA-7 replaces
// it with the real onboarding screen.
function AuthenticatedShell() {
  const { session, signOut } = useAuth();

  return (
    <VStack align="start" spacing={2} p={8}>
      <Heading size="md">AssetAuditor</Heading>
      <Text color="gray.500">Signed in as {session?.user.email}.</Text>
      <Button size="sm" onClick={() => void signOut()}>
        Log out
      </Button>
    </VStack>
  );
}

function AppRoutes() {
  const { session, loading } = useAuth();

  if (loading) {
    return (
      <Center h="100vh">
        <Spinner />
      </Center>
    );
  }

  if (!session) {
    return <Login />;
  }

  return (
    <Routes>
      <Route path="/staged/:jobId" element={<ParseConfirm />} />
      <Route path="/manual-entry/portfolio" element={<ManualEntryPortfolio />} />
      <Route path="/manual-entry/account-balance" element={<ManualEntryAccountBalance />} />
      <Route path="*" element={<AuthenticatedShell />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
