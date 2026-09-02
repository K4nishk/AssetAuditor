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

function RoomCard({ accountType, breakdown }: { accountType: AccountType; breakdown: RoomBreakdown }) {
  return (
    <Box borderWidth="1px" borderRadius="md" p={4}>
      <Heading size="sm" mb={3}>
        {ACCOUNT_LABELS[accountType]}
      </Heading>
      <StatGroup mb={3}>
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
