import { Alert, AlertIcon, Box, Button, Text, VStack } from "@chakra-ui/react";
import { useEffect, useState } from "react";

import { ApiError } from "../lib/api";
import { type DemoSeedResult, getDemoStatus, seedDemoData } from "../lib/demoApi";

// Seed-from-fixtures button for the public blog demo (KCH-69 / AA-32). Only
// renders once GET /api/demo/status confirms the signed-in account is the
// one configured demo account (DEMO_USER_ID) — a real user never sees this,
// and the backend refuses the request even if they did (app/routes/demo.py).
export default function DemoSeedButton() {
  const [visible, setVisible] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [result, setResult] = useState<DemoSeedResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDemoStatus()
      .then((status) => {
        if (!cancelled) setVisible(status.is_demo_user);
      })
      .catch(() => {
        if (!cancelled) setVisible(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!visible) {
    return null;
  }

  async function seed() {
    setSeeding(true);
    setError(null);
    try {
      setResult(await seedDemoData());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to seed demo data");
    } finally {
      setSeeding(false);
    }
  }

  return (
    <Box borderWidth="1px" borderRadius="md" p={4}>
      <VStack align="start" spacing={2}>
        <Text fontWeight="bold">Demo mode</Text>
        <Text color="gray.500" fontSize="sm">
          Resets this account to the mock-user fixtures in data/samples/ — never touches real data.
        </Text>
        <Button size="sm" colorScheme="teal" onClick={() => void seed()} isLoading={seeding}>
          Seed from fixtures
        </Button>
        {error ? (
          <Alert status="error" fontSize="sm">
            <AlertIcon />
            {error}
          </Alert>
        ) : null}
        {result ? (
          <Text fontSize="sm" color="gray.600">
            Loaded {result.fixtures_loaded.length} fixtures — net worth {result.net_worth_cad} CAD.
          </Text>
        ) : null}
      </VStack>
    </Box>
  );
}
