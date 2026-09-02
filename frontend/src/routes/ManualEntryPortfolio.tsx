import {
  Alert,
  AlertIcon,
  Box,
  Button,
  Checkbox,
  Divider,
  FormControl,
  FormLabel,
  HStack,
  Heading,
  IconButton,
  Input,
  Select,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../lib/api";
import {
  type AccountField,
  type LotField,
  importYahooFinancePortfolio,
  submitPortfolioEntry,
} from "../lib/manualEntryApi";

// AA-20's no-PDF path, portfolio side (mvp.md AA-20): ticker/shares/avg-cost
// with optional per-lot detail, plus a Yahoo Finance export bulk import.
// Either submission stages the same silver shapes a parsed statement would
// (app.domain.manual_entry) and lands on the same parse-confirm screen
// (AA-17) for review before it's confirmed into silver.

const EMPTY_ACCOUNT: AccountField = {
  institution: "",
  account_type: "",
  account_number: "",
  currency: "CAD",
};

const EMPTY_LOT: LotField = { quantity: "", unit_cost: "", acquired_at: "", vested: null };

export default function ManualEntryPortfolio() {
  const navigate = useNavigate();
  const [account, setAccount] = useState<AccountField>(EMPTY_ACCOUNT);
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [currency, setCurrency] = useState("CAD");
  const [lots, setLots] = useState<LotField[]>([]);
  const [csvText, setCsvText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function updateLot(index: number, patch: Partial<LotField>) {
    setLots((prev) => prev.map((lot, i) => (i === index ? { ...lot, ...patch } : lot)));
  }

  function removeLot(index: number) {
    setLots((prev) => prev.filter((_, i) => i !== index));
  }

  async function submitPortfolio() {
    setSubmitting(true);
    setError(null);
    try {
      const response = await submitPortfolioEntry({
        account,
        ticker,
        quantity,
        avg_cost: avgCost || null,
        currency,
        lots: lots.filter((lot) => lot.quantity),
      });
      navigate(`/staged/${response.job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to submit portfolio entry");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitYahooImport() {
    setSubmitting(true);
    setError(null);
    try {
      const response = await importYahooFinancePortfolio({ account, csv_text: csvText, currency });
      navigate(`/staged/${response.job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to import Yahoo Finance export");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmitPortfolio =
    !submitting &&
    account.institution &&
    account.account_type &&
    account.account_number &&
    ticker &&
    quantity &&
    (avgCost || lots.some((lot) => lot.quantity));
  const canImportYahoo =
    !submitting && account.institution && account.account_type && account.account_number && csvText;

  return (
    <VStack align="stretch" spacing={6} p={8} maxW="640px">
      <Heading size="md">Add a portfolio holding</Heading>
      <Text color="gray.500">
        No statement to upload? Enter the position directly — it goes through the same review
        screen as a parsed statement before it's saved.
      </Text>

      {error && (
        <Alert status="error">
          <AlertIcon />
          {error}
        </Alert>
      )}

      <Box borderWidth="1px" borderRadius="md" p={4}>
        <Heading size="sm" mb={3}>
          Account
        </Heading>
        <VStack spacing={3}>
          <FormControl isRequired>
            <FormLabel>Institution</FormLabel>
            <Input
              value={account.institution}
              onChange={(e) => setAccount({ ...account, institution: e.target.value })}
              placeholder="Questrade"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Account type</FormLabel>
            <Input
              value={account.account_type}
              onChange={(e) => setAccount({ ...account, account_type: e.target.value })}
              placeholder="TFSA"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Account number (last 4 digits is enough)</FormLabel>
            <Input
              value={account.account_number}
              onChange={(e) => setAccount({ ...account, account_number: e.target.value })}
              placeholder="1234"
            />
          </FormControl>
          <FormControl>
            <FormLabel>Account currency</FormLabel>
            <Select
              value={account.currency}
              onChange={(e) => setAccount({ ...account, currency: e.target.value })}
            >
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </Select>
          </FormControl>
        </VStack>
      </Box>

      <Box borderWidth="1px" borderRadius="md" p={4}>
        <Heading size="sm" mb={3}>
          Position
        </Heading>
        <VStack spacing={3}>
          <FormControl isRequired>
            <FormLabel>Ticker</FormLabel>
            <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Shares</FormLabel>
            <Input value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="10" />
          </FormControl>
          <FormControl>
            <FormLabel>Average cost (leave blank to derive from lots below)</FormLabel>
            <Input
              value={avgCost}
              onChange={(e) => setAvgCost(e.target.value)}
              placeholder="150.00"
            />
          </FormControl>
          <FormControl>
            <FormLabel>Position currency</FormLabel>
            <Select value={currency} onChange={(e) => setCurrency(e.target.value)}>
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </Select>
          </FormControl>
        </VStack>

        <Divider my={4} />

        <Heading size="sm" mb={3}>
          Lots (optional)
        </Heading>
        <VStack align="stretch" spacing={3}>
          {lots.map((lot, index) => (
            <HStack key={index} align="flex-end">
              <FormControl>
                <FormLabel fontSize="sm">Quantity</FormLabel>
                <Input
                  size="sm"
                  value={lot.quantity}
                  onChange={(e) => updateLot(index, { quantity: e.target.value })}
                />
              </FormControl>
              <FormControl>
                <FormLabel fontSize="sm">Unit cost</FormLabel>
                <Input
                  size="sm"
                  value={lot.unit_cost ?? ""}
                  onChange={(e) => updateLot(index, { unit_cost: e.target.value })}
                />
              </FormControl>
              <FormControl>
                <FormLabel fontSize="sm">Acquired (YYYY-MM-DD)</FormLabel>
                <Input
                  size="sm"
                  value={lot.acquired_at ?? ""}
                  onChange={(e) => updateLot(index, { acquired_at: e.target.value })}
                />
              </FormControl>
              <Checkbox
                isChecked={lot.vested === true}
                onChange={(e) => updateLot(index, { vested: e.target.checked })}
              >
                Vested
              </Checkbox>
              <IconButton
                aria-label="Remove lot"
                size="sm"
                variant="ghost"
                onClick={() => removeLot(index)}
              >
                &times;
              </IconButton>
            </HStack>
          ))}
          <Button size="sm" variant="outline" onClick={() => setLots([...lots, { ...EMPTY_LOT }])}>
            Add lot
          </Button>
        </VStack>

        <Button
          mt={4}
          colorScheme="teal"
          isDisabled={!canSubmitPortfolio}
          isLoading={submitting}
          onClick={() => void submitPortfolio()}
        >
          Review entry
        </Button>
      </Box>

      <Box borderWidth="1px" borderRadius="md" p={4}>
        <Heading size="sm" mb={2}>
          Or import lots from a Yahoo Finance portfolio export
        </Heading>
        <Text color="gray.500" fontSize="sm" mb={3}>
          Paste the CSV exported from Yahoo Finance's portfolio manager. Every row becomes a lot
          against the account above, grouped into one holding per ticker.
        </Text>
        <Textarea
          value={csvText}
          onChange={(e) => setCsvText(e.target.value)}
          fontFamily="mono"
          fontSize="sm"
          rows={6}
          placeholder="Symbol,Current Price,Date,Time,Change,Open,High,Low,Volume,Trade Date,Purchase Price,Quantity,..."
        />
        <Button
          mt={3}
          colorScheme="teal"
          variant="outline"
          isDisabled={!canImportYahoo}
          isLoading={submitting}
          onClick={() => void submitYahooImport()}
        >
          Review import
        </Button>
      </Box>
    </VStack>
  );
}
