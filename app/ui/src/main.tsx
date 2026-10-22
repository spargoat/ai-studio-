import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { router } from "./router";
import "./index.css";

// Vite rejects a lazy route when its CSS preload fails, and nothing catches that,
// so the first visit to /board or /library unmounts to a blank page. There is no
// service worker. Swallow one preload miss and reload that path a single time.
const CHUNK_RELOAD = "studio:chunk-reload";
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  const path = window.location.pathname;
  let used: Record<string, number> = {};
  try {
    used = JSON.parse(sessionStorage.getItem(CHUNK_RELOAD) || "{}");
  } catch {
    used = {};
  }
  if (used[path]) return;
  used[path] = Date.now();
  try {
    sessionStorage.setItem(CHUNK_RELOAD, JSON.stringify(used));
  } catch {
    return;
  }
  window.location.reload();
});

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
createRoot(document.getElementById("root")!).render(
  <StrictMode><QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider></StrictMode>,
);
