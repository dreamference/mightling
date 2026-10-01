// A description of two figures.

import { Tile, makeDisk } from "./shapes";

export function describe(radius: number, side: number): string {
  const disk = makeDisk(radius);
  const tile = new Tile(side);
  return `${disk.surface()} ${tile.surface()}`;
}

export function sum(figures: { surface(): number }[]): number {
  return figures.reduce((total, figure) => total + figure.surface(), 0);
}
