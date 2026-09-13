/** The ring's geometry, apart from the drawing. */

export interface Segment {
  /** How much of the whole this part is, from 0 to 1. */
  readonly fraction: number;
  readonly colour: string;
}

export interface Arc {
  readonly colour: string;
  /** The stroke drawn, in the units of the circle's circumference. */
  readonly length: number;
  /** Where along the circumference it starts, going clockwise from the top. */
  readonly offset: number;
}

/** Lays segments end to end around a circle, clipping at the whole and skipping empty parts. */
export function arcsFor(
  segments: readonly Segment[],
  circumference: number,
): Arc[] {
  const arcs: Arc[] = [];
  let used = 0;
  for (const segment of segments) {
    const fraction = Math.max(0, Math.min(segment.fraction, 1 - used));
    if (fraction <= 0) {
      continue;
    }
    arcs.push({
      colour: segment.colour,
      length: fraction * circumference,
      offset: used * circumference,
    });
    used += fraction;
  }
  return arcs;
}
