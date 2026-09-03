// Illustrative-only figures for the AA-31 blog page. These mirror
// `data/samples/README.md`'s published reference totals for the fixture-only
// mock user "Alex Mock" (the same numbers AA-32's demo-seed button loads) —
// never a live account. Kept as a standalone module, not imported from
// `routes/Dashboard.tsx`/`routes/Rooms.tsx`, so a real screen's API-backed
// shape can never accidentally leak into a page that must stay static.

export const MOCK_SNAPSHOT_DATE = "2026-07-31";

export const MOCK_TERM_BUCKETS = [
  { key: "short_term", label: "Short-term (<1y)", amount: 27700 },
  { key: "medium_term", label: "Medium-term (1-5y)", amount: 66799 },
  { key: "long_term", label: "Long-term (5y+)", amount: 523760 },
];

export const MOCK_NET_WORTH = {
  totalAssets: 618259,
  totalLiabilities: 421800,
  netWorth: 196459,
};

export const MOCK_ROOMS = [
  { accountType: "TFSA", roomTotal: 51500, roomRemaining: 41200 },
  { accountType: "RRSP", roomTotal: 14760, roomRemaining: 10660 },
  { accountType: "FHSA", roomTotal: 24000, roomRemaining: 12000 },
] as const;
