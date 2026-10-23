interface Position {
  readonly x: number; readonly y: number;
  readonly width: number; readonly height: number;
  readonly clone: HTMLElement;
}
export type CardPositions = ReadonlyMap<string, Position>;
const LAYOUT_CACHE_LIMIT = 24;

/** Layout coordinates stay local. Viewport rectangles would apply a composition's
 * camera scale twice when their delta becomes a CSS translation. */
export function cardPositions(host: HTMLElement): CardPositions {
  const positions = new Map<string, Position>();
  for (const card of host.querySelectorAll<HTMLElement>('[data-task-id]')) {
    let x = 0, y = 0;
    for (let node: HTMLElement | null = card; node && node !== host;
      node = node.offsetParent instanceof HTMLElement ? node.offsetParent : null) {
      x += node.offsetLeft; y += node.offsetTop;
    }
    positions.set(card.dataset.taskId!, {
      x, y, width: card.offsetWidth, height: card.offsetHeight,
      clone: card.cloneNode(true) as HTMLElement,
    });
  }
  return positions;
}

/** Per-mount bounded cache of settled endpoints. Animated sizes are never cached. */
export class LayoutCache {
  private readonly entries = new Map<string, CardPositions>();
  get(key: string): CardPositions | undefined {
    const value = this.entries.get(key);
    if (value) { this.entries.delete(key); this.entries.set(key, value); }
    return value;
  }
  set(key: string, value: CardPositions): void {
    this.entries.delete(key); this.entries.set(key, value);
    if (this.entries.size > LAYOUT_CACHE_LIMIT) this.entries.delete(this.entries.keys().next().value!);
  }
  clear(): void { this.entries.clear(); }
}
