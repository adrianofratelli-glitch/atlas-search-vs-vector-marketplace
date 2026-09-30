// Two-tone MongoDB leaf logo
export default function Leaf({ size = 26 }) {
  const h = (size / 26) * 30;
  return (
    <svg width={size} height={h} viewBox="0 0 26 32" fill="none"
         style={{ flexShrink: 0, filter: "drop-shadow(0 0 6px rgba(0,237,100,0.35))" }}>
      <path d="M13 1C13 1 3 11 3 19C3 24.52 7.48 29 13 29L13 1Z" fill="#00ED64" />
      <path d="M13 1C13 1 23 11 23 19C23 24.52 18.52 29 13 29L13 1Z" fill="#00A35C" />
      <rect x="12" y="28" width="2" height="4" rx="1" fill="#00684A" />
    </svg>
  );
}
