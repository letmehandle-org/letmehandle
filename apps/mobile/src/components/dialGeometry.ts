/**
 * The ring's geometry, apart from the drawing.
 *
 * Kept pure so that what the ring claims is testable: a dial that drew 24 handled calls as a
 * third of the ring would be the one place in the app lying with a picture.
 */

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

/**
 * Lay segments end to end around a circle.
 *
 * Fractions that add to more than the whole are clipped at the whole rather than wrapping past
 * the top, and empty or negative ones draw nothing: a ring with more in it than a day holds is
 * a bug upstream, and drawing it twice round would hide that.
 */
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
