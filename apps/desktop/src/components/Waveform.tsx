import { useEffect, useRef } from "react";
import WaveSurfer from "wavesurfer.js";

export function Waveform({ url, onReady }: { url: string; onReady?: (ws: WaveSurfer) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const ws = WaveSurfer.create({ container: ref.current, url, height: 70, waveColor: "#3B82F6", progressColor: "#60A5FA", cursorColor: "#F8FAFC" });
    ws.on("ready", () => onReady?.(ws));
    ws.on("error", () => undefined); // sin audio todavía
    return () => ws.destroy();
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={ref} className="bg-bg rounded" />;
}
