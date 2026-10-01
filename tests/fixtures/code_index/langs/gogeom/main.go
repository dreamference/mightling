// Command gogeom prints two figures.
package main

import (
	"fmt"

	"example.com/gogeom/report"
	"example.com/gogeom/shapes"
)

func main() {
	fmt.Println(report.Describe(1, 2))
	fmt.Println(shapes.MakeDisk(3).Surface())
}
