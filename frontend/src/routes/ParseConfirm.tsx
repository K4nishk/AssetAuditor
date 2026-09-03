import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Center,
  Heading,
  HStack,
  Spinner,
  Table,
  Tbody,
  Td,
  Text,
  Textarea,
  Th,
  Thead,
  Tr,
  VStack,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { track } from "../lib/analytics";
import { ApiError } from "../lib/api";
import {
  type ConfirmResponse,
  type StagedRow,
  confirmStagedRows,
  editStagedRow,
  fetchStagedRows,
} from "../lib/stagedRowsApi";

// Wireframe v1 screen 2 (mvp.md AA-17): staged rows from the most recent
// upload's extraction, low-confidence rows highlighted, inline edit (logged
// server-side as `manual_correction`), and a single "confirm all" action that
// resolves every row into silver.

const METHOD_LABELS: Record<StagedRow["method"], string> = {
  deterministic: "Parsed",
  llm: "AI-extracted",
  manual_entry: "Manual entry",
  manual_correction: "Corrected",
};

function summarizePayload(payload: Record<string, unknown>): string {
  return Object.entries(payload)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => {
      const shown = typeof value === "object" ? JSON.stringify(value) : String(value);
      return `${key}: ${shown}`;
    })
    .join("  ·  ");
}

export default function ParseConfirm() {
  const { jobId } = useParams<{ jobId: string }>();
  const [rows, setRows] = useState<StagedRow[]>([]);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingRowId, setEditingRowId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmResult, setConfirmResult] = useState<ConfirmResponse | null>(null);

  const load = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetchStagedRows(jobId);
      setRows(response.rows);
      setJobStatus(response.job_status);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to load staged rows");
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  function startEdit(row: StagedRow) {
    setEditingRowId(row.id);
    setEditDraft(JSON.stringify(row.payload, null, 2));
    setEditError(null);
  }

  function cancelEdit() {
    setEditingRowId(null);
    setEditError(null);
  }

  async function saveEdit(rowId: string) {
    if (!jobId) return;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(editDraft) as Record<string, unknown>;
    } catch {
      setEditError("not valid JSON");
      return;
    }
    try {
      const updated = await editStagedRow(jobId, rowId, parsed);
      setRows((prev) => prev.map((row) => (row.id === rowId ? updated : row)));
      setEditingRowId(null);
      setEditError(null);
    } catch (err) {
      setEditError(err instanceof ApiError ? err.message : "failed to save correction");
    }
  }

  async function confirmAll() {
    if (!jobId) return;
    setConfirming(true);
    setError(null);
    try {
      const result = await confirmStagedRows(jobId);
      setConfirmResult(result);
      const corrections = rows.filter((row) => row.method === "manual_correction").length;
      track("parse_confirmed", { row_count: result.confirmed_row_count, corrections });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to confirm rows");
    } finally {
      setConfirming(false);
    }
  }

  if (loading) {
    return (
      <Center h="60vh">
        <Spinner />
      </Center>
    );
  }

  const canConfirm = jobStatus === "needs_user" && rows.length > 0 && !confirming;

  return (
    <VStack align="stretch" spacing={4} p={8}>
      <Heading size="md">Review parsed statement</Heading>
      <Text color="gray.500">
        {rows.length} row{rows.length === 1 ? "" : "s"} staged — status: {jobStatus ?? "unknown"}
      </Text>

      {error && (
        <Alert status="error">
          <AlertIcon />
          {error}
        </Alert>
      )}

      {confirmResult && (
        <Alert status="success">
          <AlertIcon />
          Confirmed {confirmResult.confirmed_row_count} row
          {confirmResult.confirmed_row_count === 1 ? "" : "s"} to silver.
        </Alert>
      )}

      <Box overflowX="auto">
        <Table size="sm">
          <Thead>
            <Tr>
              <Th>Entity</Th>
              <Th>Details</Th>
              <Th>Source</Th>
              <Th />
            </Tr>
          </Thead>
          <Tbody>
            {rows.map((row) => {
              const isEditing = editingRowId === row.id;
              return (
                <Tr key={row.id} bg={row.is_low_confidence ? "orange.50" : undefined}>
                  <Td verticalAlign="top">
                    <Badge>{row.entity}</Badge>
                  </Td>
                  <Td whiteSpace="normal">
                    {isEditing ? (
                      <VStack align="stretch" spacing={2}>
                        <Textarea
                          value={editDraft}
                          onChange={(event) => setEditDraft(event.target.value)}
                          fontFamily="mono"
                          fontSize="sm"
                          rows={6}
                        />
                        {editError && (
                          <Text color="red.500" fontSize="sm">
                            {editError}
                          </Text>
                        )}
                      </VStack>
                    ) : (
                      <Text fontSize="sm">{summarizePayload(row.payload)}</Text>
                    )}
                  </Td>
                  <Td verticalAlign="top">
                    <VStack align="start" spacing={1}>
                      <Badge colorScheme={row.is_low_confidence ? "orange" : "gray"}>
                        {METHOD_LABELS[row.method]}
                        {row.confidence !== null ? ` · ${Math.round(row.confidence * 100)}%` : ""}
                      </Badge>
                      {row.is_low_confidence && (
                        <Text fontSize="xs" color="orange.600">
                          low confidence — please review
                        </Text>
                      )}
                    </VStack>
                  </Td>
                  <Td verticalAlign="top">
                    {jobStatus === "needs_user" &&
                      (isEditing ? (
                        <HStack>
                          <Button size="xs" colorScheme="teal" onClick={() => void saveEdit(row.id)}>
                            Save
                          </Button>
                          <Button size="xs" variant="ghost" onClick={cancelEdit}>
                            Cancel
                          </Button>
                        </HStack>
                      ) : (
                        <Button size="xs" variant="outline" onClick={() => startEdit(row)}>
                          Edit
                        </Button>
                      ))}
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
      </Box>

      <HStack>
        <Button colorScheme="teal" isDisabled={!canConfirm} isLoading={confirming} onClick={() => void confirmAll()}>
          Confirm all
        </Button>
        {jobStatus !== "needs_user" && jobStatus !== null && (
          <Text color="gray.500" fontSize="sm">
            This job is {jobStatus} — nothing left to confirm.
          </Text>
        )}
      </HStack>
    </VStack>
  );
}
