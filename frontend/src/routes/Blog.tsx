import {
  Box,
  Container,
  Divider,
  Heading,
  Link as ChakraLink,
  ListItem,
  OrderedList,
  Text,
  UnorderedList,
} from "@chakra-ui/react";
import { MDXProvider } from "@mdx-js/react";
import { Link as RouterLink } from "react-router-dom";

import ArchitectureStory from "../content/architecture-story.mdx";

// AA-31: the in-app architecture story. Public (mounted outside the auth
// gate in App.tsx) — a showcase page has to be reachable by someone who has
// not signed up yet. These overrides route every MDX-emitted heading,
// paragraph and list through Chakra so the post matches the rest of the
// app's typography instead of unstyled browser defaults.
const mdxComponents = {
  h1: (props: JSX.IntrinsicElements["h1"]) => <Heading as="h1" size="xl" mt={8} mb={4} {...props} />,
  h2: (props: JSX.IntrinsicElements["h2"]) => <Heading as="h2" size="lg" mt={8} mb={3} {...props} />,
  h3: (props: JSX.IntrinsicElements["h3"]) => <Heading as="h3" size="md" mt={6} mb={2} {...props} />,
  p: (props: JSX.IntrinsicElements["p"]) => <Text mb={4} lineHeight="tall" {...props} />,
  ul: (props: JSX.IntrinsicElements["ul"]) => <UnorderedList mb={4} pl={4} {...props} />,
  ol: (props: JSX.IntrinsicElements["ol"]) => <OrderedList mb={4} pl={4} {...props} />,
  li: (props: JSX.IntrinsicElements["li"]) => <ListItem {...props} />,
  a: (props: JSX.IntrinsicElements["a"]) => <ChakraLink color="teal.600" isExternal {...props} />,
  hr: () => <Divider my={8} />,
};

export default function Blog() {
  return (
    <Container maxW="720px" py={10}>
      <ChakraLink as={RouterLink} to="/" fontSize="sm" color="gray.500">
        ← AssetAuditor
      </ChakraLink>
      <MDXProvider components={mdxComponents}>
        <Box mt={4}>
          <ArchitectureStory />
        </Box>
      </MDXProvider>
    </Container>
  );
}
