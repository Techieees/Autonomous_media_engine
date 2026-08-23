import type { Metadata } from "next";
import { Suspense } from "react";
import { TrendsView } from "@/components/views/trends-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Trends" };

export default function TrendsPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <TrendsView />
    </Suspense>
  );
}
