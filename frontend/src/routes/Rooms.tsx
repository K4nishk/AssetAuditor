import {
  Accordion,
  AccordionButton,
  AccordionIcon,
  AccordionItem,
  AccordionPanel,
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Center,
  FormControl,
  FormLabel,
  Heading,
  HStack,
  Input,
  Select,
  Spinner,
  Stat,
  StatGroup,
  StatLabel,
  StatNumber,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import { PolarAngleAxis, RadialBar, RadialBarChart } from "recharts";

import { ApiError } from "../lib/api";
import {
  type AccountType,
  type RoomBreakdown,
  type RoomsOut,
  getRooms,
  submitCraOverride,
} from "../lib/roomsApi";

// Rooms screen + ledger drill-down (mvp.md AA-9): every TFSA/RRSP/FHSA
// figure expands to the grant/contribution/withdrawal/cra_override entries
// that produced it (app.routes.rooms's `GET /api/rooms`), and a small form
// lets the user pin a CRA My Account number as a `cra_override`
// reconciliation entry — the ledger then shows the delta vs. the computed
// total (app.domain.rooms.engine already writes that note).

const ACCOUNT_LABELS: Record<AccountType, string> = {
  tfsa: "TFSA",
  rrsp: "RRSP",
  fhsa: "FHSA",
};

const KIND_LABELS: Record<string, string> = {
  grant: "Grant",
  contribution: "Contribution",
  withdrawal: "Withdrawal",
  pension_adjustment: "Pension adjustment",
  cra_override: "CRA override",
};

// Room gauges (mvp.md AA-26): a quick visual read of how much of a room is
// used, alongside the exact dollar figures the StatGroup below already
// shows. Blue is the same primary series color the dashboard's charts use;
// red is reserved for an over-contribution (`room_used > room_total`, which
// the ledger can produce if a `cra_override` reconciles below what was
// already contributed) — same reserved-for-liability-shaped-things
// convention `frontend/src/routes/Dashboard.tsx`'s `LIABILITY_COLOR` sets.
const GAUGE_COLOR = "#2a78d6";
const GAUGE_OVER_COLOR = "#e34948";

// `null` when `room_total` is 0 (e.g. an FHSA never opened yet) — not a
// meaningful percentage to draw an arc for.
function percentUsed(breakdown: RoomBreakdown): number | null {
  const total = Number(breakdown.room_total);
  if (total <= 0) return null;
  return (Number(breakdown.room_used) / total) * 100;
}

function RoomGauge({ breakdown }: { breakdown: RoomBreakdown }) {
  const percent = percentUsed(breakdown);

  if (percent === null) {
    return (
      <Center width="110px" height="110px" borderWidth="1px" borderRadius="full" flexShrink={0}>
        <Text fontSize="xs" color="gray.500">
          N/A
        </Text>
      </Center>
    );
  }

  // The arc itself clamps to [0, 100] — an over-contribution still draws a
  // full ring rather than an out-of-range arc — but the label shows the real
  // (unclamped) percent so an over-contribution stays visible.
  const clamped = Math.min(100, Math.max(0, percent));
  const isOver = percent > 100;

  return (
    <Box position="relative" width="110px" height="110px" flexShrink={0}>
      <RadialBarChart
        width={110}
        height={110}
        cx="50%"
        cy="50%"
        innerRadius="75%"
        outerRadius="100%"
        barSize={10}
        data={[{ value: clamped }]}
        startAngle={90}
        endAngle={-270}
      >
        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
        <RadialBar
          background
          dataKey="value"
          cornerRadius={6}
          fill={isOver ? GAUGE_OVER_COLOR : GAUGE_COLOR}
        />
      </RadialBarChart>
      <Center position="absolute" inset={0} flexDirection="column">
        <Text fontSize="sm" fontWeight="bold">
          {percent.toFixed(0)}%
        </Text>
        {isOver && (
          <Text fontSize="10px" color={GAUGE_OVER_COLOR}>
            over
          </Text>
        )}
      </Center>
    </Box>
  );
}

function RoomCard({ accountType, breakdown }: { accountType: AccountType; breakdown: RoomBreakdown }) {
  return (
    <Box borderWidth="1px" borderRadius="md" p={4}>
      <Heading size="sm" mb={3}>
        {ACCOUNT_LABELS[accountType]}
      </Heading>
      <HStack align="center" spacing={4} mb={3}>
        <RoomGauge breakdown={breakdown} />
        <StatGroup flex={1}>
          <Stat>
            <StatLabel>Total room</StatLabel>
            <StatNumber fontSize="lg">${breakdown.room_total}</StatNumber>
          </Stat>
          <Stat>
            <StatLabel>Used</StatLabel>
            <StatNumber fontSize="lg">${breakdown.room_used}</StatNumber>
          </Stat>
          <Stat>
            <StatLabel>Remaining</StatLabel>
            <StatNumber fontSize="lg">${breakdown.room_remaining}</StatNumber>
          </Stat>
        </StatGroup>
      </HStack>

      <Accordion allowToggle>
        <AccordionItem border="none">
          <AccordionButton px={0}>
            <Box as="span" flex="1" textAlign="left" fontSize="sm" color="gray.600">
              {breakdown.ledger.length} ledger entr{breakdown.ledger.length === 1 ? "y" : "ies"}
            </Box>
            <AccordionIcon />
          </AccordionButton>
          <AccordionPanel px={0}>
            <Table size="sm">
              <Thead>
                <Tr>
                  <Th>Year</Th>
                  <Th>Kind</Th>
                  <Th isNumeric>Amount</Th>
                  <Th>Note</Th>
                </Tr>
              </Thead>
              <Tbody>
                {breakdown.ledger.map((entry, index) => (
                  <Tr key={`${entry.year}-${entry.kind}-${index}`}>
                    <Td>{entry.year}</Td>
                    <Td>
                      <Badge colorScheme={entry.kind === "cra_override" ? "purple" : "gray"}>
                        {KIND_LABELS[entry.kind] ?? entry.kind}
                      </Badge>
                    </Td>
                    <Td isNumeric>${entry.amount}</Td>
                    <Td fontSize="xs" color="gray.500">
                      {entry.note}
                      {entry.source_ref && (
                        <Text as="span" ml={1} title={entry.source_ref}>
                          (source: {entry.source_ref})
                        </Text>
                      )}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </AccordionPanel>
        </AccordionItem>
      </Accordion>
    </Box>
  );
}

function CraOverrideForm({ onSubmitted }: { onSubmitted: (rooms: RoomsOut) => void }) {
  const [accountType, setAccountType] = useState<AccountType>("tfsa");
  const [year, setYear] = useState("");
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const rooms = await submitCraOverride({
        account_type: accountType,
        year: Number(year),
        amount,
      });
      onSubmitted(rooms);
      setAmount("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to save the override");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = !submitting && year !== "" && amount !== "";

  return (
    <Box borderWidth="1px" borderRadius="md" p={4}>
      <Heading size="sm" mb={2}>
        Reconcile against CRA My Account
      </Heading>
      <Text color="gray.500" fontSize="sm" mb={3}>
        Pin the room total CRA shows you — the ledger will explain the delta vs. the computed
        number.
      </Text>

      {error && (
        <Alert status="error" mb={3}>
          <AlertIcon />
          {error}
        </Alert>
      )}

      <HStack align="end" spacing={3}>
        <FormControl maxW="140px">
          <FormLabel fontSize="sm">Account</FormLabel>
          <Select value={accountType} onChange={(e) => setAccountType(e.target.value as AccountType)}>
            {(Object.keys(ACCOUNT_LABELS) as AccountType[]).map((type) => (
              <option key={type} value={type}>
                {ACCOUNT_LABELS[type]}
              </option>
            ))}
          </Select>
        </FormControl>
        <FormControl maxW="120px">
          <FormLabel fontSize="sm">Year</FormLabel>
          <Input type="number" value={year} onChange={(e) => setYear(e.target.value)} placeholder="2026" />
        </FormControl>
        <FormControl maxW="160px">
          <FormLabel fontSize="sm">CRA room total</FormLabel>
          <Input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="40000.00" />
        </FormControl>
        <Button colorScheme="teal" isDisabled={!canSubmit} isLoading={submitting} onClick={() => void submit()}>
          Save
        </Button>
      </HStack>
    </Box>
  );
}

export default function Rooms() {
  const [rooms, setRooms] = useState<RoomsOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRooms(await getRooms());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to load contribution rooms");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <Center h="60vh">
        <Spinner />
      </Center>
    );
  }

  if (error || !rooms) {
    return (
      <VStack p={8} align="stretch">
        <Alert status="error">
          <AlertIcon />
          {error ?? "no room data available"}
        </Alert>
      </VStack>
    );
  }

  return (
    <VStack align="stretch" spacing={4} p={8} maxW="720px" mx="auto">
      <Heading size="md">Contribution rooms</Heading>
      <Text color="gray.500">As of {rooms.as_of_year}. Expand a room to see its ledger.</Text>

      <RoomCard accountType="tfsa" breakdown={rooms.tfsa} />
      <RoomCard accountType="rrsp" breakdown={rooms.rrsp} />
      <RoomCard accountType="fhsa" breakdown={rooms.fhsa} />

      <CraOverrideForm onSubmitted={setRooms} />
    </VStack>
  );
}
