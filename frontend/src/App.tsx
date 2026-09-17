import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AuthGate } from './components/AuthGate'
import { ReaderShell } from './components/ReaderShell'
import { AuthPage } from './pages/AuthPage'
import { DashboardPage } from './pages/DashboardPage'
import { LibraryPage } from './pages/LibraryPage'
import { ModelsPage } from './pages/ModelsPage'
import { PaperReadingPage } from './pages/PaperReadingPage'
import { ReaderPage } from './pages/ReaderPage'
import { RunsPage } from './pages/RunsPage'
import { SettingsPage } from './pages/SettingsPage'
import { TopicsPage } from './pages/TopicsPage'
import './App.css'

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="login" element={<AuthPage />} />

        <Route element={<AuthGate />}>
          <Route path="/" element={<ReaderShell />}>
            <Route index element={<ReaderPage />} />
            <Route path="paper/:paperId" element={<PaperReadingPage />} />
          </Route>
        </Route>

        <Route element={<AuthGate adminOnly />}>
          <Route path="admin" element={<AppShell />}>
            <Route index element={<Navigate to="/admin/daily" replace />} />
            <Route path="daily" element={<DashboardPage />} />
            <Route path="library" element={<LibraryPage />} />
            <Route path="topics" element={<TopicsPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="runs" element={<RunsPage />} />
          </Route>
        </Route>

        <Route path="admin/login" element={<Navigate to="/login" replace />} />
        <Route path="read" element={<Navigate to="/" replace />} />
        <Route path="daily" element={<Navigate to="/admin/daily" replace />} />
        <Route path="library" element={<Navigate to="/admin/library" replace />} />
        <Route path="topics" element={<Navigate to="/admin/topics" replace />} />
        <Route path="models" element={<Navigate to="/admin/models" replace />} />
        <Route path="settings" element={<Navigate to="/admin/settings" replace />} />
        <Route path="runs" element={<Navigate to="/admin/runs" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  )
}
