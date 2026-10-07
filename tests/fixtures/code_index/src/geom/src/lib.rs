//! A tiny crate with known references, recorded by mling-code's tests.

pub mod shapes;

use shapes::Shape;

/// Sums the areas of a set of shapes through dynamic dispatch.
pub fn total(shapes: &[Box<dyn Shape>]) -> f64 {
    shapes.iter().map(|shape| shape.area()).sum()
}

pub fn unit_total() -> f64 {
    let shapes: Vec<Box<dyn Shape>> = vec![Box::new(shapes::Circle::new(1.0)), Box::new(shapes::Square { side: 1.0 })];
    total(&shapes)
}
