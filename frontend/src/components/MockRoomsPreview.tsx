import { Box, Heading, SimpleGrid, Stat, StatLabel, StatNumber, Text } from "@chakra-ui/react";

import { MOCK_ROOMS } from "../content/mockDemoData";

const CAD_FORMATTER = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

// A static "screenshot" of the real `/rooms` screen (AA-9), fed the three
// golden numbers `data/samples/README.md` and the rooms engine's own tests
// (AA-8) reproduce — $41,200 / $10,660 / $12,000 — instead of a live
// `GET /api/rooms` call.
export default function MockRoomsPreview() {
  return (
    <Box borderWidth="1px" borderRadius="lg" p={5} my={6} bg="gray.50">
      <Text fontSize="xs" textTransform="uppercase" letterSpacing="wide" color="gray.500" mb={1}>
        Illustrative — mock user "Alex Mock," not a live account
      </Text>
      <Heading size="sm" mb={4}>
        Contribution rooms preview
      </Heading>
      <SimpleGrid columns={{ base: 1, md: 3 }} spacing={4}>
        {MOCK_ROOMS.map((room) => (
          <Box key={room.accountType} borderWidth="1px" borderRadius="md" p={3} bg="white">
            <Heading size="xs" mb={2}>
              {room.accountType}
            </Heading>
            <Stat>
              <StatLabel fontSize="10px">Remaining</StatLabel>
              <StatNumber fontSize="md">{CAD_FORMATTER.format(room.roomRemaining)}</StatNumber>
            </Stat>
            <Text fontSize="xs" color="gray.500" mt={1}>
              of {CAD_FORMATTER.format(room.roomTotal)} total room
            </Text>
          </Box>
        ))}
      </SimpleGrid>
    </Box>
  );
}
