import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

try { document.documentElement.dataset.theme = localStorage.getItem("r2g-theme") || "dark"; }
catch { document.documentElement.dataset.theme = "dark"; }

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
