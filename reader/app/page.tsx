import type { Metadata } from "next";
import ReaderApp from "../components/ReaderApp";

export const metadata: Metadata = {
  title: "Paper2HTML Reader",
  description: "A responsive reader for Paper2HTML structured document packages.",
};

export default function Home() {
  return <ReaderApp />;
}
