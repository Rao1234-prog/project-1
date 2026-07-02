import React from "react";
import { createRoot } from "react-dom/client";
import "./fonts.js";        // self-hosted IBM Plex (vendored woff2, no external fetch)
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
