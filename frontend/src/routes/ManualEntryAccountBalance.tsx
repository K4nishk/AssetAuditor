import {
  Alert,
  AlertIcon,
  Box,
  Button,
  FormControl,
  FormLabel,
  Heading,
  Input,
  Select,
  Text,
  VStack,
} from "@chakra-ui/react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../lib/api";
import { type AccountField, submitAccountBalance } from "../lib/manualEntryApi";

// AA-20's no-PDF path, account-balance side (mvp.md AA-20): a cash-like
// account (chequing/savings/HISA/...) with no statement to upload, entered
// as a single current balance. Staged the same way as the portfolio form —
// review on the parse-confirm screen (AA-17) before it's confirmed to silver.

const EMPTY_ACCOUNT: AccountField = {
  institution: "",
  account_type: "",
  account_number: "",
  currency: "CAD",
};

export default function ManualEntryAccountBalance() {
  const navigate = useNavigate();
  const [account, setAccount] = useState<AccountField>(EMPTY_ACCOUNT);
  const [balance, setBalance] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const response = await submitAccountBalance({
        account,
        balance,
        currency: account.currency,
      });
      navigate(`/staged/${response.job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to submit account balance");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit =
    !submitting && account.institution && account.account_type && account.account_number && balance;

  return (
    <VStack align="stretch" spacing={6} p={8} maxW="480px">
      <Heading size="md">Add an account balance</Heading>
      <Text color="gray.500">
        For accounts you don't have a statement for — enter the current balance directly.
      </Text>

      {error && (
        <Alert status="error">
          <AlertIcon />
          {error}
        </Alert>
      )}

      <Box borderWidth="1px" borderRadius="md" p={4}>
        <VStack spacing={3}>
          <FormControl isRequired>
            <FormLabel>Institution</FormLabel>
            <Input
              value={account.institution}
              onChange={(e) => setAccount({ ...account, institution: e.target.value })}
              placeholder="Scotiabank"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Account type</FormLabel>
            <Input
              value={account.account_type}
              onChange={(e) => setAccount({ ...account, account_type: e.target.value })}
              placeholder="savings"
            />
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Account number (last 4 digits is enough)</FormLabel>
            <Input
              value={account.account_number}
              onChange={(e) => setAccount({ ...account, account_number: e.target.value })}
              placeholder="4821"
            />
          </FormControl>
          <FormControl>
            <FormLabel>Currency</FormLabel>
            <Select
              value={account.currency}
              onChange={(e) => setAccount({ ...account, currency: e.target.value })}
            >
              <option value="CAD">CAD</option>
              <option value="USD">USD</option>
            </Select>
          </FormControl>
          <FormControl isRequired>
            <FormLabel>Current balance</FormLabel>
            <Input value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="8500.00" />
          </FormControl>
        </VStack>

        <Button
          mt={4}
          colorScheme="teal"
          isDisabled={!canSubmit}
          isLoading={submitting}
          onClick={() => void submit()}
        >
          Review entry
        </Button>
      </Box>
    </VStack>
  );
}
