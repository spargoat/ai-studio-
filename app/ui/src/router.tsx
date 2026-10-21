// Pattern from AMN Production OS router.tsx: MainLayout + lazy pages. Canvas/billing/admin/auth routes dropped.
import { createBrowserRouter, Navigate } from "react-router-dom";
import { lazy, Suspense } from "react";
import MainLayout from "./layouts/MainLayout";

const Dashboard = lazy(() => import("./pages/DashboardPage"));
const NewJob = lazy(() => import("./pages/NewJobPage"));
const Jobs = lazy(() => import("./pages/RunLogPage"));
const JobDetail = lazy(() => import("./pages/JobDetailPage"));
const LibraryPage = lazy(() => import("./pages/LibraryPage"));
const ExportPage = lazy(() => import("./pages/ExportPage"));
const Settings = lazy(() => import("./pages/SettingsPage"));
const Spend = lazy(() => import("./pages/SpendPage"));
const Board = lazy(() => import("./pages/BoardPage"));

function Wrap({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<div className="py-16 text-center text-zinc-500 text-sm">Loading...</div>}>{children}</Suspense>;
}

export const router = createBrowserRouter([{ element: <MainLayout />, children: [
  { path: "/", element: <Wrap><Dashboard /></Wrap> },
  { path: "/board", element: <Wrap><Board /></Wrap> },
  { path: "/new", element: <Wrap><NewJob /></Wrap> },
  { path: "/jobs", element: <Wrap><Jobs /></Wrap> },
  { path: "/jobs/:id", element: <Wrap><JobDetail /></Wrap> },
  { path: "/library", element: <Wrap><LibraryPage /></Wrap> },
  { path: "/export", element: <Wrap><ExportPage /></Wrap> },
  { path: "/personas", element: <Navigate to="/library?tab=personas" replace /> },
  { path: "/settings", element: <Wrap><Settings /></Wrap> },
  { path: "/spend", element: <Wrap><Spend /></Wrap> },
] }]);
