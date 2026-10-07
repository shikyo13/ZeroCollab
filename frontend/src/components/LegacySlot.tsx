import { useLayoutEffect, useRef } from 'react';

export function LegacySlot({ node, className = '' }: { node: HTMLElement; className?: string }) {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    host.current?.append(node);
    // Slots remain mounted when navigating; legacy modules own their children.
  }, [node]);
  return <div ref={host} className={className} />;
}
