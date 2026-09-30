"""Reports on shapes."""

from shapes.geometry import Square, make_circle


def summary(radius, side):
    circle = make_circle(radius)
    square = Square(side)
    return circle.area() + square.area()


def largest(shapes):
    return max(shape.area() for shape in shapes)
