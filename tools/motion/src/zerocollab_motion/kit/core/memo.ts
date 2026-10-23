/* Identity caches (28cb5faf, opus b9ef3317): a value computed once per pair of immutable objects, such as a
 * composition and the props it is drawn with. Weak on both keys, so nothing outlives the scene that made it. */

export type Props = Readonly<Record<string, unknown>>;

/** The props a caller passes when it has no overrides: one shared object, so identity caches keep hitting. */
export const NO_PROPS: Props = Object.freeze({});

/** compute(a, b) once per (a, b) identity pair. */
export function pairCache<A extends object, B extends object, V>(compute: (a: A, b: B) => V): (a: A, b: B) => V {
  const outer = new WeakMap<A, WeakMap<B, V>>();
  return (a, b) => {
    let inner = outer.get(a);
    if (!inner) outer.set(a, inner = new WeakMap());
    if (!inner.has(b)) inner.set(b, compute(a, b));
    return inner.get(b)!;
  };
}
