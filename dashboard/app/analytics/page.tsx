import type { Metadata } from "next";
import { Suspense } from "react";
import { AnalyticsView } from "@/components/views/analytics-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Analytics" };

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <AnalyticsView />
    </Suspense>
  );
}
