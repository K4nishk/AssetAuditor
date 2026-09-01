import { Heading, Text, VStack } from "@chakra-ui/react";

// Routes, components, and charts land per their own issues (see mvp.md M1-M3).
export default function App() {
  return (
    <VStack align="start" spacing={2} p={8}>
      <Heading size="md">AssetAuditor</Heading>
      <Text color="gray.500">Scaffold shell — screens land per mvp.md.</Text>
    </VStack>
  );
}
