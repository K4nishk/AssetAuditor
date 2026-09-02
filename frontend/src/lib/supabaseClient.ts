import { createClient } from "@supabase/supabase-js";

// VITE_-prefixed vars are the only ones Vite inlines into the browser bundle
// (.env.example); the anon key is safe to ship client-side because RLS on
// every user table (app/db/migrations/0001_init.sql) is what actually
// enforces tenant isolation, not secrecy of this key.
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    "VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set — see .env.example",
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
