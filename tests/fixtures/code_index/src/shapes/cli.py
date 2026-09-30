"""Command line entry point."""

from shapes.geometry import make_circle
from shapes.report import summary


def main():
    print(summary(1.0, 2.0))
    print(make_circle(3.0).area())
