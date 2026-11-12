import { lazy, Suspense, useEffect } from "react";
import { Shell } from "./components/Shell";
import { Spinner } from "./components/ui";
import { navigate, usePath } from "./lib/router";
import { connect, useLive } from "./lib/store";
import { Live } from "./pages/Live";

const History = lazy(() => import("./pages/History").then((m) => ({ default: m.History })));
const Inspector = lazy(() => import("./pages/Inspector").then((m) => ({ default: m.Inspector })));
const Chat = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));
const Welcome = lazy(() => import("./pages/Welcome").then((m) => ({ default: m.Welcome })));

function Loading() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted">
      <Spinner />
    </div>
  );
}

export function App() {
  const path = usePath();
  const app = useLive((s) => s.app);
  const loadApp = useLive((s) => s.loadApp);

  useEffect(() => connect(), []);
  useEffect(() => {
    loadApp().then((a) => {
      // First run: everything is set up in the browser before the agent starts.
      if (a && !a.ready && location.pathname !== "/welcome") navigate("/welcome", true);
    });
  }, [loadApp]);

  if (path === "/welcome") {
    return (
      <Suspense fallback={<Loading />}>
        <Welcome />
      </Suspense>
    );
  }
  if (!app) return <Loading />;
  return (
    <Shell>
      <Suspense fallback={<Loading />}>
        {path.startsWith("/chat") ? <Chat /> : path.startsWith("/history") ? <History /> : path.startsWith("/inspector") ? <Inspector /> : path.startsWith("/settings") ? <Settings /> : <Live />}
      </Suspense>
    </Shell>
  );
}
