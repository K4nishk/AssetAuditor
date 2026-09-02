import { Button, Center, Heading, Spinner, Text, VStack } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Link as RouterLink, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ApiError } from "./lib/api";
import { type ProfileOut, getProfile } from "./lib/profileApi";
import Login from "./routes/Login";
import ManualEntryAccountBalance from "./routes/ManualEntryAccountBalance";
import ManualEntryPortfolio from "./routes/ManualEntryPortfolio";
import Onboarding from "./routes/Onboarding";
import ParseConfirm from "./routes/ParseConfirm";
import Rooms from "./routes/Rooms";

// Post-auth dashboard screens beyond rooms (net worth, drill-down, ...) land
// per their own issues (mvp.md M1-M3); this is a placeholder shell for
// everything besides the profile and rooms screens, which AA-7/AA-9 own.
function AuthenticatedShell({
  profile,
  onEditProfile,
}: {
  profile: ProfileOut;
  onEditProfile: () => void;
}) {
  const { session, signOut } = useAuth();

  return (
    <VStack align="start" spacing={2} p={8}>
      <Heading size="md">AssetAuditor</Heading>
      <Text color="gray.500">Signed in as {session?.user.email}.</Text>
      {profile.shows_room_widgets ? (
        <Button as={RouterLink} to="/rooms" size="sm" colorScheme="teal" variant="outline">
          Contribution rooms
        </Button>
      ) : (
        <Text color="gray.500">
          Contribution-room tracking is Canada-only — hidden for {profile.holdings_country}.
        </Text>
      )}
      <Button size="sm" variant="outline" onClick={onEditProfile}>
        Edit profile
      </Button>
      <Button size="sm" onClick={() => void signOut()}>
        Log out
      </Button>
    </VStack>
  );
}

// Gates the main shell on a completed profile: a fresh user has no
// `users_profile` row yet (`GET /api/profile` 404s), so this shows the
// onboarding form (wireframe v1 screen 1) instead until one is saved. Also
// doubles as the "edit profile" screen — same form, pre-filled.
function Home() {
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProfile(await getProfile());
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setProfile(null);
      } else {
        setError(err instanceof ApiError ? err.message : "failed to load profile");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <Center h="100vh">
        <Spinner />
      </Center>
    );
  }

  if (error) {
    return (
      <Center h="100vh">
        <Text color="red.500">{error}</Text>
      </Center>
    );
  }

  if (!profile || editing) {
    return (
      <Onboarding
        initialProfile={profile}
        onSaved={(saved) => {
          setProfile(saved);
          setEditing(false);
        }}
        onCancel={profile ? () => setEditing(false) : undefined}
      />
    );
  }

  return <AuthenticatedShell profile={profile} onEditProfile={() => setEditing(true)} />;
}

// Gates direct navigation to /rooms on the same `shows_room_widgets`
// eligibility check Home uses to decide whether to show the link at all
// (non-Canadian profiles have no contribution rooms to compute) — otherwise
// typing the URL bypasses the check entirely.
function RoomsRoute() {
  const [profile, setProfile] = useState<ProfileOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getProfile()
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch(() => {
        if (!cancelled) setProfile(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <Center h="100vh">
        <Spinner />
      </Center>
    );
  }

  if (!profile?.shows_room_widgets) {
    return <Navigate to="/" replace />;
  }

  return <Rooms />;
}

function AppRoutes() {
  const { session, loading } = useAuth();

  if (loading) {
    return (
      <Center h="100vh">
        <Spinner />
      </Center>
    );
  }

  if (!session) {
    return <Login />;
  }

  return (
    <Routes>
      <Route path="/staged/:jobId" element={<ParseConfirm />} />
      <Route path="/manual-entry/portfolio" element={<ManualEntryPortfolio />} />
      <Route path="/manual-entry/account-balance" element={<ManualEntryAccountBalance />} />
      <Route path="/rooms" element={<RoomsRoute />} />
      <Route path="*" element={<Home />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
