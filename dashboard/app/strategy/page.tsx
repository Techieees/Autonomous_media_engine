import type { Metadata } from "next";
import { StrategyView } from "@/components/views/strategy-view";

export const metadata: Metadata = { title: "Strategy" };

export default function StrategyPage() {
  return <StrategyView />;
}
