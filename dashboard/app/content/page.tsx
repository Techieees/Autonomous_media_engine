import type { Metadata } from "next";
import { Suspense } from "react";
import { ContentView } from "@/components/views/content-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Content" };

export default function ContentPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <ContentView />
    </Suspense>
  );
}
