import type { Metadata } from "next";
import { Suspense } from "react";
import { HumanActionsView } from "@/components/views/human-actions-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Human actions" };

export default function HumanActionsPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <HumanActionsView />
    </Suspense>
  );
}
