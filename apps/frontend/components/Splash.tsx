"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  markSplashPlayed,
  shouldSkipSplash,
  SPLASH_SKIP_ATTRIBUTE,
} from "@/lib/splashSession";

// The visual sequence stays paused until both server-rendered image layers have
// decoded. This keeps the first frame eligible for FCP without letting a cold
// image request consume the reveal before the animation actually starts.
const TOTAL_MS = 1_800;
const ASSET_READY_TIMEOUT_MS = 1_500;

function decodeImage(image: HTMLImageElement): Promise<void> {
  const decode = () => {
    if (typeof image.decode !== "function") return Promise.resolve();
    return image.decode().then(
      () => undefined,
      () => undefined,
    );
  };

  if (image.complete) return decode();

  return new Promise((resolve) => {
    let settled = false;
    const finish = (decodeAfterLoad: boolean) => {
      if (settled) return;
      settled = true;
      image.removeEventListener("load", onLoad);
      image.removeEventListener("error", onError);
      if (decodeAfterLoad) {
        void decode().then(resolve, resolve);
      } else {
        resolve();
      }
    };
    const onLoad = () => finish(true);
    const onError = () => finish(false);
    image.addEventListener("load", onLoad, { once: true });
    image.addEventListener("error", onError, { once: true });
  });
}

export function Splash() {
  const [done, setDone] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const splashRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (shouldSkipSplash()) {
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, "1");
      setDone(true);
    }
  }, []);

  useEffect(() => {
    if (done || shouldSkipSplash()) return;

    let cancelled = false;
    const images = Array.from(
      splashRef.current?.querySelectorAll<HTMLImageElement>("img") ?? [],
    );
    const start = () => {
      if (!cancelled) setIsPlaying(true);
    };
    const readyTimeoutId = window.setTimeout(start, ASSET_READY_TIMEOUT_MS);

    void Promise.all(images.map(decodeImage)).then(() => {
      window.clearTimeout(readyTimeoutId);
      start();
    });

    return () => {
      cancelled = true;
      window.clearTimeout(readyTimeoutId);
    };
  }, [done]);

  useEffect(() => {
    if (!isPlaying) return;

    const timeoutId = window.setTimeout(() => {
      markSplashPlayed();
      document.documentElement.setAttribute(SPLASH_SKIP_ATTRIBUTE, "1");
      setDone(true);
    }, TOTAL_MS);

    return () => window.clearTimeout(timeoutId);
  }, [isPlaying]);

  if (done) return null;

  return (
    <div
      ref={splashRef}
      role="status"
      aria-label="Opening Quantiv"
      className={`quantiv-splash quantiv-splash--play${isPlaying ? " quantiv-splash--ready" : ""}`}
    >
      <div className="quantiv-splash-mark" aria-hidden="true">
        {/* These source WebPs are already compact. `priority` emits resource
            hints from the server HTML; `unoptimized` avoids a cold image-
            transformation request in the high percentile. */}
        <Image
          src="/brand/QuantivSplashQClosed.webp"
          alt=""
          width={4096}
          height={4096}
          priority
          unoptimized
          className="quantiv-splash-layer quantiv-splash-closed-ring"
          draggable={false}
        />
        <div className="quantiv-splash-slit-arc" />
        <div className="quantiv-splash-tail-clip">
          <Image
            src="/brand/QuantivSplashTail.webp"
            alt=""
            width={4096}
            height={4096}
            priority
            unoptimized
            className="quantiv-splash-layer"
            draggable={false}
          />
        </div>
      </div>
      <span className="quantiv-splash-wordmark" aria-hidden="true">
        QUANTIV
      </span>
    </div>
  );
}
