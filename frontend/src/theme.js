import { palette } from "@leafygreen-ui/palette";

// Palette in the five-pillar pitch style (MongoDB Atlas dark)
export const T = {
  bg:        palette.black,            // #061621 — Atlas dark background
  surface:   palette.gray.dark4,        // #001e2b — secondary panels
  surface2:  palette.gray.dark3,        // #0a2633 — elevated surfaces
  sidebar:   palette.black,
  border:    palette.gray.dark2,        // #2a424d — dark-mode divider
  borderSub: palette.gray.dark2,
  borderAcc: "rgba(0,237,100,0.25)",   // border-accent

  green:     palette.green.base,       // #00ED64
  greenDark: palette.green.dark1,
  blue:      palette.blue.light1,
  purple:    palette.purple.base,
  yellow:    palette.yellow.base,
  teal:      palette.green.dark1,
  red:       palette.red.light1,

  text:      palette.gray.light2,
  text2:     palette.gray.light1,
  text3:     palette.gray.base,

  codeBg:    palette.gray.dark4,

  font: "'Special Gothic', 'Helvetica Neue', Arial, sans-serif",
  mono: "'Source Code Pro', Menlo, monospace",
};

export const fmtCount = (n) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return Math.round(n / 1_000) + "k";
  return String(n ?? 0);
};

export const fmtBRL = (v) =>
  "R$ " + (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
