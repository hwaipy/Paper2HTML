import { createRoot } from "react-dom/client";
import "katex/dist/katex.min.css";
import "./app/globals.css";
import ReaderApp from "./components/ReaderApp";

const target = document.getElementById("paper2html-reader");

if (!target) {
  throw new Error("Paper2HTML Reader requires #paper2html-reader");
}

createRoot(target).render(<ReaderApp />);
