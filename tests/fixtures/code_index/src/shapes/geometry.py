"""Shapes with an area."""

import math


class Circle:
    """A circle."""

    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return math.pi * self.radius * self.radius


class Square:
    """A square."""

    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side * self.side


def make_circle(radius):
    return Circle(radius)
