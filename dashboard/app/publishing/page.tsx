import type { Metadata } from "next";
import { Suspense } from "react";
import { PublishingView } from "@/components/views/publishing-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Publishing" };

export default function PublishingPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <PublishingView />
    </Suspense>
  );
}
