import { palette } from "@leafygreen-ui/palette";

// Palette in the five-pillar pitch style (MongoDB Atlas dark)
export const T = {
  bg:        palette.black,            // #001E2B — Atlas dark background
  surface:   palette.gray.dark4,        // #112733 — secondary panels
  surface2:  palette.gray.dark3,        // #1C2D38 — elevated surfaces
  sidebar:   palette.black,
  border:    palette.gray.dark2,        // #3D4F58 — dark-mode divider
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

  font: "'Outfit', 'Helvetica Neue', Arial, sans-serif",
  mono: "'JetBrains Mono', Menlo, monospace",
};

export const fmtCount = (n) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return Math.round(n / 1_000) + "k";
  return String(n ?? 0);
};

export const fmtBRL = (v) =>
  "R$ " + (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
