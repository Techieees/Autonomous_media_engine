import type { Metadata } from "next";
import { BootstrapView } from "@/components/views/bootstrap-view";

export const metadata: Metadata = { title: "Bootstrap" };

export default function BootstrapPage() {
  return <BootstrapView />;
}
