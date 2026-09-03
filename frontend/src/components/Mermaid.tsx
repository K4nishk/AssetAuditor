import { Box, Text } from "@chakra-ui/react";
import { useEffect, useRef, useState } from "react";

let diagramCounter = 0;

// Renders one mermaid diagram from source text (mvp.md AA-31's "architecture
// story with the mermaid diagrams"). `mermaid` is dynamically imported so it
// never lands in the app's main bundle for users who never open the blog
// page. Diagram source lives in `content/architectureDiagrams.ts`, reused
// from `docs/adr/ADR_v1.1.0.md`'s fenced mermaid blocks (which GitHub
// already renders) so the two never drift into separately-hand-drawn copies.
export default function Mermaid({ chart, title }: { chart: string; title: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const idRef = useRef(`mermaid-diagram-${(diagramCounter += 1)}`);

  useEffect(() => {
    let cancelled = false;
    setError(null);

    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          theme: "base",
          securityLevel: "strict",
          themeVariables: {
            primaryColor: "#eef4fc",
            primaryBorderColor: "#2a78d6",
            primaryTextColor: "#1a202c",
            lineColor: "#2a78d6",
            fontFamily: "inherit",
          },
        });
        const { svg } = await mermaid.render(idRef.current, chart);
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg;
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "diagram failed to render");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [chart]);

  return (
    <Box borderWidth="1px" borderRadius="md" p={4} my={6} overflowX="auto" bg="white">
      <Text fontSize="sm" fontWeight="semibold" color="gray.600" mb={2}>
        {title}
      </Text>
      {error ? (
        <Box>
          <Text color="red.500" fontSize="sm" mb={2}>
            Diagram failed to render ({error}) — source below:
          </Text>
          <Box as="pre" fontSize="xs" whiteSpace="pre-wrap">
            {chart}
          </Box>
        </Box>
      ) : (
        <div ref={containerRef} />
      )}
    </Box>
  );
}
