"use client";

import { ArrowRight, CircleHelp } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type SyntheticEvent,
} from "react";
import Link from "next/link";
import dynamic from "next/dynamic";

import { METRIC_GLOSSARY, type MetricKey } from "@/lib/metricGlossary";

const METRIC_HELP_GROUP = "qv-ticker-metric-help";
const CALCULATION_ROLES = ["Inputs", "Method", "Result"] as const;
const MathFormula = dynamic(
  () => import("@/components/MathFormula").then((module) => module.MathFormula),
  {
    ssr: false,
    loading: () => (
      <span className="qv-metric-help-math-loading">Loading equation…</span>
    ),
  },
);

function closeOtherMetricHelp(event: SyntheticEvent<HTMLDetailsElement>) {
  const current = event.currentTarget;
  if (!current.open) return;

  document
    .querySelectorAll<HTMLDetailsElement>(
      `details[name="${METRIC_HELP_GROUP}"][open]`,
    )
    .forEach((details) => {
      if (details !== current) details.open = false;
    });
}
export function MetricHelp({
  metric,
  align = "right",
}: {
  metric: MetricKey;
  align?: "left" | "right";
}) {
  const definition = METRIC_GLOSSARY[metric];
  const [isOpen, setIsOpen] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const outsidePointerListenerRef = useRef<
    ((event: PointerEvent) => void) | null
  >(null);

  const removeOutsidePointerListener = () => {
    const listener = outsidePointerListenerRef.current;
    if (!listener) return;
    document.removeEventListener("pointerdown", listener);
    outsidePointerListenerRef.current = null;
  };

  useEffect(
    () => () => {
      const listener = outsidePointerListenerRef.current;
      if (listener) document.removeEventListener("pointerdown", listener);
    },
    [],
  );

  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    closeOtherMetricHelp(event);
    removeOutsidePointerListener();
    setIsOpen(event.currentTarget.open);
    if (!event.currentTarget.open) return;

    const details = event.currentTarget;
    const closeOnOutsidePointer = (pointerEvent: PointerEvent) => {
      if (
        pointerEvent.target instanceof Node &&
        !details.contains(pointerEvent.target)
      ) {
        details.open = false;
      }
    };
    outsidePointerListenerRef.current = closeOnOutsidePointer;
    document.addEventListener("pointerdown", closeOnOutsidePointer);
  };

  return (
    <details
      ref={detailsRef}
      className={`qv-metric-help qv-metric-help-${align}`}
      name={METRIC_HELP_GROUP}
      onToggle={handleToggle}
    >
      <summary
        aria-label={`Explain ${definition.label}`}
        title={`Explain ${definition.label}`}
      >
        <CircleHelp aria-hidden="true" size={14} strokeWidth={1.8} />
      </summary>
      {isOpen ? (
        <div className="qv-metric-help-popover">
          <div className="qv-metric-help-eyebrow">Metric guide</div>
          <div className="qv-metric-help-title">{definition.label}</div>
          <div className="qv-metric-help-definition">
            {definition.definition}
          </div>
          <div
            className="qv-metric-help-flow"
            role="img"
            aria-label={`${definition.definition} Inputs: ${definition.calculation[0]}. Method: ${definition.calculation[1]}. Result: ${definition.calculation[2]}.`}
          >
            {definition.calculation.map((node, index) => (
              <div className="qv-metric-help-flow-step" key={node}>
                {index > 0 ? (
                  <ArrowRight
                    className="qv-metric-help-flow-arrow"
                    aria-hidden="true"
                    size={13}
                    strokeWidth={1.8}
                  />
                ) : null}
                <span className="qv-metric-help-flow-role">
                  {CALCULATION_ROLES[index]}
                </span>
                <strong>{node}</strong>
                {definition.calculationDetails?.[index] ? (
                  <span className="qv-metric-help-flow-detail">
                    {definition.calculationDetails[index]}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
          <div className="qv-metric-help-formula">
            <span>Formula</span>
            <MathFormula
              className="qv-metric-help-math"
              displayMode
              label={definition.formula}
              math={definition.formulaTex}
            />
          </div>
          <div className="qv-metric-help-notes">
            <div>
              <span>Use</span>
              <strong>{definition.use}</strong>
            </div>
            <div>
              <span>Watch</span>
              <strong>{definition.caution}</strong>
            </div>
          </div>
          <Link
            className="qv-metric-help-methodology"
            href={definition.methodologyHref}
          >
            Full methodology <ArrowRight aria-hidden="true" size={12} />
          </Link>
        </div>
      ) : null}
    </details>
  );
}
