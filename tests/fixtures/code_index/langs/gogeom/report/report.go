// Package report describes figures.
package report

import (
	"fmt"

	"example.com/gogeom/shapes"
)

// Describe two figures.
func Describe(radius, side float64) string {
	disk := shapes.MakeDisk(radius)
	tile := shapes.Tile{Side: side}
	return fmt.Sprintf("%v %v", disk.Surface(), tile.Surface())
}

// Sum the surfaces of figures.
func Sum(figures []shapes.Figure) float64 {
	total := 0.0
	for _, figure := range figures {
		total += figure.Surface()
	}
	return total
}
