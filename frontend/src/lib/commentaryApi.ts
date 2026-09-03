import { ApiError, apiFetch } from "./api";

// Mirrors app/routes/commentary.py's response model (KCH-62 / AA-25).

export interface CommentaryOut {
  as_of: string;
  observations: string[];
  disclosure: string;
  model_backend: string;
}

export async function getCommentary(): Promise<CommentaryOut | null> {
  try {
    return await apiFetch<CommentaryOut>("/commentary");
  } catch (err) {
    // No card generated yet (worker hasn't run `commentary_loop`/
    // `python -m worker.commentary` for this user) is a normal, expected
    // state, not an error the dashboard should surface — the card section
    // just doesn't render.
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}
