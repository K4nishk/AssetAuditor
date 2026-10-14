import {
  Box,
  HStack,
  Heading,
  SimpleGrid,
  Stat,
  StatGroup,
  StatLabel,
  StatNumber,
  Text,
} from "@chakra-ui/react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { MOCK_NET_WORTH, MOCK_SNAPSHOT_DATE, MOCK_TERM_BUCKETS } from "../content/mockDemoData";

// Same categorical order `routes/Dashboard.tsx` assigns its term-bucket
// slices (short/medium/long from `TERM_BUCKET_COLORS`) — reused here rather
// than re-derived so the "screenshot" reads as the same product, not a
// reskinned lookalike.
const PALETTE = ["#2a78d6", "#1baf7a", "#4a3aa7"];

const CAD_FORMATTER = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

// A static "screenshot" of the real `/dashboard` screen (AA-22/AA-23), fed
// the exact reference totals `data/samples/README.md` publishes instead of
// a live `GET /api/dashboard` call — the blog page never touches a real
// account (mvp.md AA-31: "screenshots from mock-user data only").
export default function MockDashboardPreview() {
  return (
    <Box borderWidth="1px" borderRadius="lg" p={5} my={6} bg="gray.50">
      <Text fontSize="xs" textTransform="uppercase" letterSpacing="wide" color="gray.500" mb={1}>
        Illustrative — mock user "Alex Mock," not a live account
      </Text>
      <Heading size="sm" mb={4}>
        Dashboard preview · as of {MOCK_SNAPSHOT_DATE}
      </Heading>
      <SimpleGrid columns={{ base: 1, md: 2 }} spacing={6} alignItems="center">
        <StatGroup>
          <Stat>
            <StatLabel>Total assets</StatLabel>
            <StatNumber fontSize="xl">{CAD_FORMATTER.format(MOCK_NET_WORTH.totalAssets)}</StatNumber>
          </Stat>
          <Stat>
            <StatLabel>Total liabilities</StatLabel>
            <StatNumber fontSize="xl">
              {CAD_FORMATTER.format(MOCK_NET_WORTH.totalLiabilities)}
            </StatNumber>
          </Stat>
          <Stat>
            <StatLabel>Net worth</StatLabel>
            <StatNumber fontSize="xl">{CAD_FORMATTER.format(MOCK_NET_WORTH.netWorth)}</StatNumber>
          </Stat>
        </StatGroup>
        <Box>
          <Box height="180px">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={MOCK_TERM_BUCKETS}
                  dataKey="amount"
                  nameKey="label"
                  innerRadius={45}
                  outerRadius={75}
                  paddingAngle={2}
                >
                  {MOCK_TERM_BUCKETS.map((bucket, index) => (
                    <Cell key={bucket.key} fill={PALETTE[index] ?? "#898781"} />
                  ))}
                </Pie>
                <Tooltip formatter={(value: number) => CAD_FORMATTER.format(value)} />
              </PieChart>
            </ResponsiveContainer>
          </Box>
          <HStack spacing={4} mt={2} flexWrap="wrap" justify="center">
            {MOCK_TERM_BUCKETS.map((bucket, index) => (
              <HStack key={bucket.key} spacing={2}>
                <Box w="10px" h="10px" borderRadius="full" bg={PALETTE[index] ?? "#898781"} />
                <Text fontSize="xs" color="gray.600">
                  {bucket.label}
                </Text>
              </HStack>
            ))}
          </HStack>
        </Box>
      </SimpleGrid>
    </Box>
  );
}
