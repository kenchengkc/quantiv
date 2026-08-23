import katex from "katex";

const renderedFormulaCache = new Map<string, string>();

function renderFormula(math: string, displayMode: boolean): string {
  const cacheKey = `${displayMode ? "display" : "inline"}:${math}`;
  const cached = renderedFormulaCache.get(cacheKey);
  if (cached) return cached;
  const html = katex.renderToString(math, {
    displayMode,
    throwOnError: false,
    output: "html",
    strict: "warn",
  });
  renderedFormulaCache.set(cacheKey, html);
  return html;
}

export function MathFormula({
  math,
  label,
  displayMode = false,
  className,
}: {
  math: string;
  label: string;
  displayMode?: boolean;
  className?: string;
}) {
  const html = renderFormula(math, displayMode);

  return (
    <span
      aria-label={label}
      className={className}
      dangerouslySetInnerHTML={{ __html: html }}
      role="math"
    />
  );
}
