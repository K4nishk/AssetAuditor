import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Center,
  Code,
  Drawer,
  DrawerBody,
  DrawerCloseButton,
  DrawerContent,
  DrawerHeader,
  DrawerOverlay,
  Heading,
  HStack,
  Select,
  SimpleGrid,
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
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { ApiError } from "../lib/api";
import {
  type DashboardOut,
  type DiversificationCut,
  type DiversificationSlice,
  getDashboard,
} from "../lib/dashboardApi";
import { type LineageSlice, type SliceSelector, getLineageSlice } from "../lib/lineageApi";

// Dashboard screen (mvp.md AA-22): the KPI row + three pies — term-buckets,
// net-worth distribution (assets vs. liabilities), and diversification with
// a cut switcher (app.domain.dashboard.AVAILABLE_CUTS). Every amount here is
// already CAD (worker.gold.rebuild_gold's FX reconciliation, AA-21's price
// layer) — this screen only renders what `GET /api/dashboard` returns.
//
// Chart colors follow the fixed categorical order from the dataviz skill's
// reference palette (skills/dataviz), assigned by entity identity where an
// entity has a stable meaning across snapshots (the term-bucket names, the
// assets/liabilities split) and by rank within a single cut's sorted slice
// list otherwise (switching the diversification cut changes the dimension
// entirely — there is no "surviving entity" to keep a color pinned to).

const PALETTE = [
  "#2a78d6", // blue
  "#eb6834", // orange
  "#1baf7a", // aqua
  "#eda100", // yellow
  "#e87ba4", // magenta
  "#008300", // green
  "#4a3aa7", // violet
  "#e34948", // red
];
const LIABILITY_COLOR = "#e34948"; // red — reserved for anything liability-shaped
const OTHER_COLOR = "#898781"; // muted ink, for the folded "Other" diversification slice

const TERM_BUCKET_LABELS: Record<string, string> = {
  short_term: "Short-term (<1y)",
  medium_term: "Medium-term (1-5y)",
  long_term: "Long-term (5y+)",
  liabilities: "Liabilities",
};

const TERM_BUCKET_COLORS: Record<string, string> = {
  short_term: PALETTE[0],
  medium_term: PALETTE[2],
  long_term: PALETTE[6],
  liabilities: LIABILITY_COLOR,
};

const CUT_LABELS: Record<DiversificationCut, string> = {
  institution: "Institution",
  account_type: "Account type",
  currency: "Currency",
};

const MAX_DIVERSIFICATION_SLICES = 7;

const CAD_FORMATTER = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

function formatCad(amount: string | number): string {
  return CAD_FORMATTER.format(Number(amount));
}

interface ChartSlice {
  key: string;
  label: string;
  value: number;
  color: string;
  // Drill-down target for this wedge (AA-23's /lineage/slice) — `null` for a
  // folded "Other" wedge, which has no single gold row to look up.
  selector: SliceSelector | null;
}

// Folds any diversification cut past the top MAX_DIVERSIFICATION_SLICES into
// "Other" rather than generating an unbounded number of hues (dataviz skill:
// "a 9th series is never a generated hue"). The backend already returns
// slices sorted by amount desc, so this keeps the largest labels visible.
function toDiversificationSlices(
  slices: DiversificationSlice[],
  cut: DiversificationCut,
  snapshotDate: string,
): ChartSlice[] {
  const head = slices.slice(0, MAX_DIVERSIFICATION_SLICES);
  const rest = slices.slice(MAX_DIVERSIFICATION_SLICES);
  const chartSlices: ChartSlice[] = head.map((slice, index) => ({
    key: slice.label,
    label: slice.label,
    value: Number(slice.amount_cad),
    color: PALETTE[index] ?? OTHER_COLOR,
    selector: { kind: "diversification", snapshotDate, cut, label: slice.label },
  }));
  if (rest.length > 0) {
    const otherTotal = rest.reduce((sum, slice) => sum + Number(slice.amount_cad), 0);
    chartSlices.push({
      key: "__other__",
      label: "Other",
      value: otherTotal,
      color: OTHER_COLOR,
      selector: null,
    });
  }
  return chartSlices;
}

function HoverPie({
  title,
  slices,
  onSliceClick,
}: {
  title: string;
  slices: ChartSlice[];
  onSliceClick: (selector: SliceSelector) => void;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);

  if (slices.length === 0 || slices.every((slice) => slice.value === 0)) {
    return (
      <Box borderWidth="1px" borderRadius="md" p={4}>
        <Heading size="sm" mb={3}>
          {title}
        </Heading>
        <Text color="gray.500" fontSize="sm">
          No data for this view yet.
        </Text>
      </Box>
    );
  }

  const total = slices.reduce((sum, slice) => sum + slice.value, 0);

  return (
    <Box borderWidth="1px" borderRadius="md" p={4}>
      <Heading size="sm" mb={3}>
        {title}
      </Heading>
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="label"
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={90}
            paddingAngle={2}
            onMouseEnter={(_, index) => setActiveKey(slices[index]?.key ?? null)}
            onMouseLeave={() => setActiveKey(null)}
            onClick={(_, index) => {
              const selector = slices[index]?.selector;
              if (selector) onSliceClick(selector);
            }}
          >
            {slices.map((slice) => (
              <Cell
                key={slice.key}
                fill={slice.color}
                opacity={activeKey === null || activeKey === slice.key ? 1 : 0.4}
                stroke={activeKey === slice.key ? slice.color : "transparent"}
                strokeWidth={activeKey === slice.key ? 2 : 0}
                cursor={slice.selector ? "pointer" : "default"}
              />
            ))}
          </Pie>
          <Tooltip
            formatter={(value: number) => {
              const pct = total > 0 ? ` (${((value / total) * 100).toFixed(1)}%)` : "";
              return `${formatCad(value)}${pct}`;
            }}
          />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </Box>
  );
}

const ENTITY_LABELS: Record<string, string> = {
  transaction: "Transaction",
  holding: "Holding",
  lot: "Lot",
  liability: "Liability",
  account: "Account",
};

const METHOD_LABELS: Record<string, string> = {
  deterministic: "Parsed",
  llm: "AI-extracted",
  manual_entry: "Manual entry",
  manual_correction: "Manually corrected",
};

// The drill-down panel itself (mvp.md AA-23): click a wedge -> underlying
// staged rows with their run, source file (or purge tombstone), extraction
// method, and confirmation timestamp — CLAUDE.md's "every dashboard number
// must drill down to sources" surfaced as a UI.
function DrillDownPanel({
  isOpen,
  onClose,
  loading,
  error,
  slice,
}: {
  isOpen: boolean;
  onClose: () => void;
  loading: boolean;
  error: string | null;
  slice: LineageSlice | null;
}) {
  const [expandedRowId, setExpandedRowId] = useState<string | null>(null);
  const onToggleRow = (rowId: string) =>
    setExpandedRowId((current) => (current === rowId ? null : rowId));

  return (
    <Drawer isOpen={isOpen} placement="right" onClose={onClose} size="md">
      <DrawerOverlay />
      <DrawerContent>
        <DrawerCloseButton />
        <DrawerHeader>Where this number comes from</DrawerHeader>
        <DrawerBody>
          {loading && (
            <Center py={8}>
              <Spinner />
            </Center>
          )}
          {!loading && error && (
            <Alert status="error">
              <AlertIcon />
              {error}
            </Alert>
          )}
          {!loading && !error && slice && (
            <VStack align="stretch" spacing={4}>
              <Box>
                <Text fontSize="xs" color="gray.500">
                  Run
                </Text>
                <Text fontFamily="mono" fontSize="sm">
                  {slice.run_id}
                </Text>
              </Box>

              <Box>
                <Text fontSize="xs" color="gray.500" mb={1}>
                  Source file
                </Text>
                {slice.source_file === null && slice.job_id === null && (
                  <Text fontSize="sm" color="gray.500">
                    No job is linked to this run yet.
                  </Text>
                )}
                {slice.source_file === null && slice.job_id !== null && (
                  <Text fontSize="sm" color="gray.500">
                    Source file is unavailable for this job.
                  </Text>
                )}
                {slice.source_file !== null && slice.source_file.is_purged && (
                  <HStack>
                    <Badge colorScheme="orange">Purged</Badge>
                    <Text fontSize="sm">
                      {slice.source_file.institution ?? "unknown institution"} —{" "}
                      {slice.source_file.period ?? "unknown period"} (deleted per the 14-day
                      retention policy{slice.source_file.purged_at ? ` on ${slice.source_file.purged_at}` : ""})
                    </Text>
                  </HStack>
                )}
                {slice.source_file !== null && !slice.source_file.is_purged && (
                  <Text fontSize="sm">
                    {slice.source_file.institution ?? "unknown institution"} —{" "}
                    {slice.source_file.period ?? "unknown period"}
                  </Text>
                )}
              </Box>

              <Box>
                <Text fontSize="xs" color="gray.500" mb={1}>
                  Underlying rows ({slice.rows.length})
                </Text>
                {slice.rows.length === 0 && (
                  <Text fontSize="sm" color="gray.500">
                    No staged rows are linked to this run.
                  </Text>
                )}
                {slice.rows.length > 0 && (
                  <Table size="sm">
                    <Thead>
                      <Tr>
                        <Th>Entity</Th>
                        <Th>Method</Th>
                        <Th>Confirmed</Th>
                        <Th />
                      </Tr>
                    </Thead>
                    <Tbody>
                      {slice.rows.map((row) => {
                        const isExpanded = expandedRowId === row.id;
                        return (
                          <Fragment key={row.id}>
                            <Tr>
                              <Td>{ENTITY_LABELS[row.entity] ?? row.entity}</Td>
                              <Td>{METHOD_LABELS[row.method] ?? row.method}</Td>
                              <Td fontSize="xs">{row.confirmed_at ?? "unconfirmed"}</Td>
                              <Td>
                                <Button
                                  size="xs"
                                  variant="ghost"
                                  onClick={() => onToggleRow(row.id)}
                                >
                                  {isExpanded ? "Hide" : "Details"}
                                </Button>
                              </Td>
                            </Tr>
                            {isExpanded && (
                              <Tr>
                                <Td colSpan={4}>
                                  <Code
                                    display="block"
                                    whiteSpace="pre-wrap"
                                    fontSize="xs"
                                    p={2}
                                    borderRadius="md"
                                  >
                                    {JSON.stringify(row.payload, null, 2)}
                                  </Code>
                                </Td>
                              </Tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </Tbody>
                  </Table>
                )}
              </Box>
            </VStack>
          )}
        </DrawerBody>
      </DrawerContent>
    </Drawer>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardOut | null>(null);
  const [cut, setCut] = useState<DiversificationCut>("institution");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drillDownOpen, setDrillDownOpen] = useState(false);
  const [drillDownLoading, setDrillDownLoading] = useState(false);
  const [drillDownError, setDrillDownError] = useState<string | null>(null);
  const [drillDownSlice, setDrillDownSlice] = useState<LineageSlice | null>(null);
  const drillDownRequestId = useRef(0);

  const load = useCallback(async (nextCut: DiversificationCut) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getDashboard(nextCut));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(cut);
  }, [load, cut]);

  const openDrillDown = useCallback(async (selector: SliceSelector) => {
    const requestId = ++drillDownRequestId.current;
    setDrillDownOpen(true);
    setDrillDownLoading(true);
    setDrillDownError(null);
    setDrillDownSlice(null);
    try {
      const result = await getLineageSlice(selector);
      if (drillDownRequestId.current === requestId) {
        setDrillDownSlice(result);
      }
    } catch (err) {
      if (drillDownRequestId.current === requestId) {
        setDrillDownError(err instanceof ApiError ? err.message : "failed to load this slice's lineage");
      }
    } finally {
      if (drillDownRequestId.current === requestId) {
        setDrillDownLoading(false);
      }
    }
  }, []);

  if (loading && !data) {
    return (
      <Center h="60vh">
        <Spinner />
      </Center>
    );
  }

  if (error || !data) {
    return (
      <VStack p={8} align="stretch">
        <Alert status="error">
          <AlertIcon />
          {error ?? "no dashboard data available"}
        </Alert>
      </VStack>
    );
  }

  const termBucketSlices: ChartSlice[] = data.term_buckets.map((bucket) => ({
    key: bucket.bucket,
    label: TERM_BUCKET_LABELS[bucket.bucket] ?? bucket.bucket,
    value: Number(bucket.amount_cad),
    color: TERM_BUCKET_COLORS[bucket.bucket] ?? OTHER_COLOR,
    selector: { kind: "term_bucket", snapshotDate: data.as_of, bucket: bucket.bucket },
  }));

  const netWorthSlices: ChartSlice[] = [
    {
      key: "assets",
      label: "Assets",
      value: Number(data.kpis.total_assets_cad),
      color: PALETTE[0],
      selector: { kind: "net_worth" as const, snapshotDate: data.as_of },
    },
    {
      key: "liabilities",
      label: "Liabilities",
      value: Number(data.kpis.total_liabilities_cad),
      color: LIABILITY_COLOR,
      selector: { kind: "net_worth" as const, snapshotDate: data.as_of },
    },
  ].filter((slice) => slice.value !== 0);

  const diversificationSlices = toDiversificationSlices(
    data.diversification,
    data.diversification_cut,
    data.as_of,
  );

  return (
    <VStack align="stretch" spacing={6} p={8} maxW="1100px" mx="auto">
      <Box>
        <Heading size="md">Dashboard</Heading>
        <Text color="gray.500">As of {data.as_of}.</Text>
      </Box>

      <StatGroup borderWidth="1px" borderRadius="md" p={4}>
        <Stat>
          <StatLabel>Total assets</StatLabel>
          <StatNumber fontSize="lg">{formatCad(data.kpis.total_assets_cad)}</StatNumber>
        </Stat>
        <Stat>
          <StatLabel>Total liabilities</StatLabel>
          <StatNumber fontSize="lg">{formatCad(data.kpis.total_liabilities_cad)}</StatNumber>
        </Stat>
        <Stat>
          <StatLabel>Net worth</StatLabel>
          <StatNumber fontSize="lg">{formatCad(data.kpis.net_worth_cad)}</StatNumber>
        </Stat>
      </StatGroup>

      <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} spacing={4}>
        <HoverPie
          title="Term buckets"
          slices={termBucketSlices}
          onSliceClick={(selector) => void openDrillDown(selector)}
        />
        <HoverPie
          title="Net worth distribution"
          slices={netWorthSlices}
          onSliceClick={(selector) => void openDrillDown(selector)}
        />
        <Box>
          <HStack justify="space-between" mb={2}>
            <Text fontSize="sm" color="gray.500">
              Diversification cut
            </Text>
            <Select
              size="sm"
              width="180px"
              value={cut}
              onChange={(e) => setCut(e.target.value as DiversificationCut)}
            >
              {data.available_cuts.map((available) => (
                <option key={available} value={available}>
                  {CUT_LABELS[available]}
                </option>
              ))}
            </Select>
          </HStack>
          <HoverPie
            title={`Diversification by ${CUT_LABELS[data.diversification_cut].toLowerCase()}`}
            slices={diversificationSlices}
            onSliceClick={(selector) => void openDrillDown(selector)}
          />
        </Box>
      </SimpleGrid>

      <DrillDownPanel
        isOpen={drillDownOpen}
        onClose={() => setDrillDownOpen(false)}
        loading={drillDownLoading}
        error={drillDownError}
        slice={drillDownSlice}
      />
    </VStack>
  );
}
