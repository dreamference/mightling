// Package shapes holds figures with a surface.
package shapes

import "math"

// Figure is anything with a surface.
type Figure interface {
	Surface() float64
}

// Disk is a circle.
type Disk struct{ Radius float64 }

// Surface of a disk.
func (d Disk) Surface() float64 { return math.Pi * d.Radius * d.Radius }

// Tile is a square.
type Tile struct{ Side float64 }

// Surface of a tile.
func (t Tile) Surface() float64 { return t.Side * t.Side }

// MakeDisk builds a disk.
func MakeDisk(radius float64) Disk { return Disk{Radius: radius} }
