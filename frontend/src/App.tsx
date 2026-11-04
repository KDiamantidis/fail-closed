import { Shell } from "./components/Shell";
import { StoreProvider, useStore } from "./lib/store";
import { BreakerPage } from "./views/BreakerPage";
import { Containment } from "./views/Containment";
import { Overview } from "./views/Overview";
import { Policies } from "./views/Policies";
import { Requests } from "./views/Requests";
import { Review } from "./views/Review";
import { Simulation } from "./views/Simulation";

function Page() {
  const { route } = useStore();
  const base = route.split("?")[0].split("/")[1] ?? "overview";
  switch (base) {
    case "requests": return <Requests />;
    case "review": return <Review />;
    case "breaker": return <BreakerPage />;
    case "containment": return <Containment />;
    case "simulation": return <Simulation />;
    case "policies": return <Policies />;
    default: return <Overview />;
  }
}

export default function App() {
  return (
    <StoreProvider>
      <Shell><Page /></Shell>
    </StoreProvider>
  );
}
