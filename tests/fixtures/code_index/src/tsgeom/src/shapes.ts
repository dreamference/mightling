// Figures with a surface.

export interface Figure {
  surface(): number;
}

export class Disk implements Figure {
  constructor(public radius: number) {}

  surface(): number {
    return Math.PI * this.radius * this.radius;
  }
}

export class Tile implements Figure {
  constructor(public side: number) {}

  surface(): number {
    return this.side * this.side;
  }
}

export function makeDisk(radius: number): Disk {
  return new Disk(radius);
}
