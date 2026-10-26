import { Sidebar } from "./components/Sidebar";
import { JobBar } from "./components/JobBar";
import { useApp } from "./store";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import Studio from "./pages/Studio";
import Voices from "./pages/Voices";
import Glossary from "./pages/Glossary";
import Models from "./pages/Models";
import Settings from "./pages/Settings";

const pages = { dashboard: Dashboard, projects: Projects, studio: Studio, voices: Voices, glossary: Glossary, models: Models, settings: Settings };

export default function App() {
  const page = useApp((s) => s.page);
  const Page = pages[page];
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6"><Page /></main>
      <JobBar />
    </div>);
}
