/* Bedrock design tokens — the single source of truth for palette + type roles.
   Ported verbatim from bedrock-app.jsx (ledger-paper direction). Do not inline
   colors or fonts elsewhere; import from here. */
export const T = {
  paper: "#F7F5EE",
  card: "#FDFCF8",
  ink: "#17241C",
  inkSoft: "#41544A",
  green: "#2F5D4A",
  greenDeep: "#1E3C30",
  rule: "#DCD5C4",
  ruleSoft: "#E9E4D6",
  red: "#A63B25",
  redSoft: "#F6E5E0",
  brass: "#8F7326",
  brassSoft: "#F3ECD8",
  mono: "'IBM Plex Mono', ui-monospace, monospace",
  sans: "'IBM Plex Sans', system-ui, sans-serif",
  serif: "'IBM Plex Serif', Georgia, serif",
};

/* All amounts travel as integer minor units (cents). Format ONLY at render. */
export const fmt = (c) =>
  (c < 0 ? "−$" : "$") +
  Math.abs(c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtK = (c) => "$" + Math.round(c / 100).toLocaleString("en-US");
