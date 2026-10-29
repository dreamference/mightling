//! Text detection's post-processing (DBNet's, as RapidOCR's `DBPostProcess` does it, spec §20):
//! the model's probability map becomes the boxes of the text lines.
//!
//! The map is thresholded at 0.3 and dilated by a 2×2 square; each 8-connected region becomes its
//! minimum-area rectangle (OpenCV's `findContours` and `minAreaRect`: the rectangle of a region's
//! convex hull, found here from each row's two ends); a rectangle under 3 pixels on its short side
//! or whose mean probability is under 0.5 is dropped; the rest is grown by `area × 1.6 / perimeter`
//! on every side (`unclip`: for a rectangle, pyclipper's rounded offset followed by `minAreaRect`
//! is exactly the rectangle grown along its own axes, so no polygon library is needed), scaled to
//! the image, ordered clockwise from the top left, clipped, and sorted top to bottom (boxes whose
//! tops are within 10 pixels make a line) then left to right.

/// A box: four corners clockwise from the top left, in image pixels.
pub type Quad = [[f32; 2]; 4];

pub const THRESH: f32 = 0.3;
pub const BOX_THRESH: f32 = 0.5;
pub const UNCLIP_RATIO: f32 = 1.6;
pub const MAX_CANDIDATES: usize = 1000;
const MIN_SIZE: f32 = 3.0;
const LINE_DY: f32 = 10.0;

/// The 8-connected regions of a mask, as each row's leftmost and rightmost pixel.
fn regions(mask: &[bool], width: usize, height: usize) -> Vec<Vec<(i32, i32)>> {
    let mut label = vec![0u32; width * height];
    let mut out = Vec::new();
    let mut stack = Vec::new();
    for start in 0..width * height {
        if !mask[start] || label[start] != 0 {
            continue;
        }
        let id = out.len() as u32 + 1;
        label[start] = id;
        stack.push(start);
        // Per row: (min x, max x).
        let mut rows: std::collections::BTreeMap<usize, (usize, usize)> =
            std::collections::BTreeMap::new();
        while let Some(p) = stack.pop() {
            let (x, y) = (p % width, p / width);
            let row = rows.entry(y).or_insert((x, x));
            row.0 = row.0.min(x);
            row.1 = row.1.max(x);
            for dy in -1i64..=1 {
                for dx in -1i64..=1 {
                    let (nx, ny) = (x as i64 + dx, y as i64 + dy);
                    if (dx, dy) == (0, 0)
                        || nx < 0
                        || ny < 0
                        || nx >= width as i64
                        || ny >= height as i64
                    {
                        continue;
                    }
                    let q = ny as usize * width + nx as usize;
                    if mask[q] && label[q] == 0 {
                        label[q] = id;
                        stack.push(q);
                    }
                }
            }
        }
        let mut points = Vec::with_capacity(rows.len() * 2);
        for (y, (a, b)) in rows {
            points.push((a as i32, y as i32));
            if b != a {
                points.push((b as i32, y as i32));
            }
        }
        out.push(points);
    }
    out
}

/// The convex hull (Andrew's monotone chain), counter-clockwise in image coordinates.
fn hull(points: &[(i32, i32)]) -> Vec<(f64, f64)> {
    let mut p: Vec<(i64, i64)> = points.iter().map(|&(x, y)| (x as i64, y as i64)).collect();
    p.sort();
    p.dedup();
    if p.len() < 3 {
        return p.into_iter().map(|(x, y)| (x as f64, y as f64)).collect();
    }
    let cross = |o: (i64, i64), a: (i64, i64), b: (i64, i64)| {
        (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
    };
    let mut lower: Vec<(i64, i64)> = Vec::new();
    for &q in &p {
        while lower.len() >= 2 && cross(lower[lower.len() - 2], lower[lower.len() - 1], q) <= 0 {
            lower.pop();
        }
        lower.push(q);
    }
    let mut upper: Vec<(i64, i64)> = Vec::new();
    for &q in p.iter().rev() {
        while upper.len() >= 2 && cross(upper[upper.len() - 2], upper[upper.len() - 1], q) <= 0 {
            upper.pop();
        }
        upper.push(q);
    }
    lower.pop();
    upper.pop();
    lower.extend(upper);
    lower
        .into_iter()
        .map(|(x, y)| (x as f64, y as f64))
        .collect()
}

/// A rotated rectangle: its corners (in no particular order) and its two side lengths.
#[derive(Debug, Clone, Copy)]
pub struct Rect {
    pub corners: [(f64, f64); 4],
    pub w: f64,
    pub h: f64,
}

/// The minimum-area rectangle of a point set, by rotating calipers over its hull's edges.
pub fn min_area_rect(points: &[(f64, f64)]) -> Option<Rect> {
    if points.is_empty() {
        return None;
    }
    let mut best: Option<(f64, (f64, f64), [f64; 4])> = None;
    let n = points.len();
    let edges: Vec<(f64, f64)> = if n < 2 {
        vec![(1.0, 0.0)]
    } else {
        (0..n)
            .map(|i| {
                (
                    points[(i + 1) % n].0 - points[i].0,
                    points[(i + 1) % n].1 - points[i].1,
                )
            })
            .filter(|e| e.0 != 0.0 || e.1 != 0.0)
            .collect()
    };
    for (ex, ey) in edges {
        let len = (ex * ex + ey * ey).sqrt();
        let (ux, uy) = (ex / len, ey / len);
        let (mut a0, mut a1, mut b0, mut b1) = (f64::MAX, f64::MIN, f64::MAX, f64::MIN);
        for &(x, y) in points {
            let a = x * ux + y * uy;
            let b = -x * uy + y * ux;
            a0 = a0.min(a);
            a1 = a1.max(a);
            b0 = b0.min(b);
            b1 = b1.max(b);
        }
        let area = (a1 - a0) * (b1 - b0);
        if best.as_ref().is_none_or(|b| area < b.0 - 1e-9) {
            best = Some((area, (ux, uy), [a0, a1, b0, b1]));
        }
    }
    let (_, (ux, uy), [a0, a1, b0, b1]) = best?;
    let at = |a: f64, b: f64| (a * ux - b * uy, a * uy + b * ux);
    Some(Rect {
        corners: [at(a0, b0), at(a1, b0), at(a1, b1), at(a0, b1)],
        w: a1 - a0,
        h: b1 - b0,
    })
}

/// `get_mini_boxes`: the rectangle's corners ordered as RapidOCR orders them, and its short side.
fn mini_box(rect: &Rect) -> ([(f64, f64); 4], f64) {
    let mut p = rect.corners;
    p.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let (i1, i4) = if p[1].1 > p[0].1 { (0, 1) } else { (1, 0) };
    let (i2, i3) = if p[3].1 > p[2].1 { (2, 3) } else { (3, 2) };
    ([p[i1], p[i2], p[i3], p[i4]], rect.w.min(rect.h))
}

/// The mean probability inside a box (`box_score_fast`: the box's polygon, its vertices truncated
/// to whole pixels, filled inside its bounding rectangle).
fn box_score(pred: &[f32], width: usize, height: usize, quad: &[(f64, f64); 4]) -> f32 {
    let xs = quad.iter().map(|p| p.0);
    let ys = quad.iter().map(|p| p.1);
    let clip = |v: f64, hi: usize| (v as i64).clamp(0, hi as i64 - 1) as usize;
    let xmin = clip(xs.clone().fold(f64::MAX, f64::min).floor(), width);
    let xmax = clip(xs.fold(f64::MIN, f64::max).ceil(), width);
    let ymin = clip(ys.clone().fold(f64::MAX, f64::min).floor(), height);
    let ymax = clip(ys.fold(f64::MIN, f64::max).ceil(), height);
    let poly: Vec<(f64, f64)> = quad
        .iter()
        .map(|p| ((p.0 - xmin as f64).trunc(), (p.1 - ymin as f64).trunc()))
        .collect();
    let (mut sum, mut n) = (0f64, 0usize);
    for y in ymin..=ymax {
        for x in xmin..=xmax {
            if inside(&poly, (x - xmin) as f64, (y - ymin) as f64) {
                sum += pred[y * width + x] as f64;
                n += 1;
            }
        }
    }
    if n == 0 {
        0.0
    } else {
        (sum / n as f64) as f32
    }
}

/// Whether a point lies in a convex polygon, edges included.
fn inside(poly: &[(f64, f64)], x: f64, y: f64) -> bool {
    let mut sign = 0f64;
    for i in 0..poly.len() {
        let (a, b) = (poly[i], poly[(i + 1) % poly.len()]);
        let c = (b.0 - a.0) * (y - a.1) - (b.1 - a.1) * (x - a.0);
        if c.abs() < 1e-9 {
            continue;
        }
        if sign == 0.0 {
            sign = c.signum();
        } else if c.signum() != sign {
            return false;
        }
    }
    true
}

/// `unclip` then `get_mini_boxes`: the rectangle grown by `area × ratio / perimeter` on each side.
fn unclip(quad: &[(f64, f64); 4], ratio: f64) -> Option<([(f64, f64); 4], f64)> {
    let mut area = 0f64;
    let mut perimeter = 0f64;
    for i in 0..4 {
        let (a, b) = (quad[i], quad[(i + 1) % 4]);
        area += a.0 * b.1 - b.0 * a.1;
        perimeter += ((b.0 - a.0).powi(2) + (b.1 - a.1).powi(2)).sqrt();
    }
    let area = area.abs() / 2.0;
    if perimeter <= 0.0 {
        return None;
    }
    let d = area * ratio / perimeter;
    // pyclipper offsets whole-pixel vertices.
    let truncated: Vec<(f64, f64)> = quad.iter().map(|p| (p.0.trunc(), p.1.trunc())).collect();
    let rect = min_area_rect(&hull_f(&truncated))?;
    // Grow along the rectangle's own axes.
    let (c0, c1, c3) = (rect.corners[0], rect.corners[1], rect.corners[3]);
    let ulen = ((c1.0 - c0.0).powi(2) + (c1.1 - c0.1).powi(2))
        .sqrt()
        .max(1e-12);
    let vlen = ((c3.0 - c0.0).powi(2) + (c3.1 - c0.1).powi(2))
        .sqrt()
        .max(1e-12);
    let u = ((c1.0 - c0.0) / ulen, (c1.1 - c0.1) / ulen);
    let v = ((c3.0 - c0.0) / vlen, (c3.1 - c0.1) / vlen);
    let (u, v) = if rect.w > 0.0 && rect.h > 0.0 {
        (u, v)
    } else {
        ((1.0, 0.0), (0.0, 1.0))
    };
    let grown = |p: (f64, f64), su: f64, sv: f64| {
        (
            p.0 + d * (su * u.0 + sv * v.0),
            p.1 + d * (su * u.1 + sv * v.1),
        )
    };
    let corners = [
        grown(rect.corners[0], -1.0, -1.0),
        grown(rect.corners[1], 1.0, -1.0),
        grown(rect.corners[2], 1.0, 1.0),
        grown(rect.corners[3], -1.0, 1.0),
    ];
    Some(mini_box(&Rect {
        corners,
        w: rect.w + 2.0 * d,
        h: rect.h + 2.0 * d,
    }))
}

fn hull_f(points: &[(f64, f64)]) -> Vec<(f64, f64)> {
    let ints: Vec<(i32, i32)> = points.iter().map(|p| (p.0 as i32, p.1 as i32)).collect();
    hull(&ints)
}

/// The boxes of a probability map of `pw` × `ph`, scaled to an image of `width` × `height`.
pub fn boxes(pred: &[f32], pw: usize, ph: usize, width: usize, height: usize) -> Vec<Quad> {
    let raw: Vec<bool> = pred.iter().map(|p| *p > THRESH).collect();
    // Dilation by a 2×2 square anchored at its centre: a pixel is set when it or its left, upper
    // or upper-left neighbour is.
    let mut mask = raw.clone();
    for y in 0..ph {
        for x in 0..pw {
            if raw[y * pw + x] {
                continue;
            }
            let left = x > 0 && raw[y * pw + x - 1];
            let up = y > 0 && raw[(y - 1) * pw + x];
            let diag = x > 0 && y > 0 && raw[(y - 1) * pw + x - 1];
            mask[y * pw + x] = left || up || diag;
        }
    }
    let mut out: Vec<Quad> = Vec::new();
    for region in regions(&mask, pw, ph).into_iter().take(MAX_CANDIDATES) {
        let Some(rect) = min_area_rect(&hull(&region)) else {
            continue;
        };
        let (quad, short) = mini_box(&rect);
        if short < MIN_SIZE as f64 {
            continue;
        }
        if box_score(pred, pw, ph, &quad) < BOX_THRESH {
            continue;
        }
        let Some((grown, short)) = unclip(&quad, UNCLIP_RATIO as f64) else {
            continue;
        };
        if short < MIN_SIZE as f64 + 2.0 {
            continue;
        }
        let scale = |v: f64, from: usize, to: usize| {
            crate::ocr::image::round_half_even(v / from as f64 * to as f64).clamp(0.0, to as f64)
                as f32
        };
        let mut q: Quad = [[0.0; 2]; 4];
        for (i, p) in grown.iter().enumerate() {
            q[i] = [scale(p.0, pw, width), scale(p.1, ph, height)];
        }
        let q = clockwise(q);
        let q = q.map(|p| {
            [
                p[0].clamp(0.0, width as f32 - 1.0).trunc(),
                p[1].clamp(0.0, height as f32 - 1.0).trunc(),
            ]
        });
        let dist = |a: [f32; 2], b: [f32; 2]| {
            ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt() as i32
        };
        if dist(q[0], q[1]) <= 3 || dist(q[0], q[3]) <= 3 {
            continue;
        }
        out.push(q);
    }
    sorted(out)
}

/// `order_points_clockwise`: top-left, top-right, bottom-right, bottom-left.
fn clockwise(q: Quad) -> Quad {
    let mut p = q.to_vec();
    p.sort_by(|a, b| a[0].partial_cmp(&b[0]).unwrap_or(std::cmp::Ordering::Equal));
    let mut left = [p[0], p[1]];
    let mut right = [p[2], p[3]];
    left.sort_by(|a, b| a[1].partial_cmp(&b[1]).unwrap_or(std::cmp::Ordering::Equal));
    right.sort_by(|a, b| a[1].partial_cmp(&b[1]).unwrap_or(std::cmp::Ordering::Equal));
    [left[0], right[0], right[1], left[1]]
}

/// `sorted_boxes`: by the top-left corner's y; tops within 10 pixels of the previous make a line,
/// read left to right.
fn sorted(mut boxes: Vec<Quad>) -> Vec<Quad> {
    boxes.sort_by(|a, b| {
        a[0][1]
            .partial_cmp(&b[0][1])
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut line = 0usize;
    let mut keyed: Vec<(usize, f32, Quad)> = Vec::with_capacity(boxes.len());
    for (i, b) in boxes.iter().enumerate() {
        if i > 0 && b[0][1] - boxes[i - 1][0][1] >= LINE_DY {
            line += 1;
        }
        keyed.push((line, b[0][0], *b));
    }
    keyed.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
    });
    keyed.into_iter().map(|(_, _, b)| b).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rotated_rectangle_is_found_with_its_sides() {
        // A 10 × 4 block of points tilted by 45°.
        let mut points = Vec::new();
        for i in 0..20 {
            for j in 0..8 {
                let (a, b) = (i as f64 * 0.5, j as f64 * 0.5);
                points.push((
                    (a - b) * std::f64::consts::FRAC_1_SQRT_2,
                    (a + b) * std::f64::consts::FRAC_1_SQRT_2,
                ));
            }
        }
        let rect = min_area_rect(&points).unwrap();
        let (long, short) = (rect.w.max(rect.h), rect.w.min(rect.h));
        assert!(
            (long - 9.5).abs() < 1e-6 && (short - 3.5).abs() < 1e-6,
            "{rect:?}"
        );
    }

    #[test]
    fn two_text_lines_become_two_sorted_grown_boxes() {
        // A 100 × 40 map with two bands of high probability: rows 5-11 (x 10-80) and 25-31 (x 20-60).
        let (w, h) = (100usize, 40usize);
        let mut pred = vec![0f32; w * h];
        for (y0, y1, x0, x1) in [(25, 31, 20, 60), (5, 11, 10, 80)] {
            for y in y0..=y1 {
                for x in x0..=x1 {
                    pred[y * w + x] = 0.9;
                }
            }
        }
        // A speck, too small, and a faint band, under the box threshold.
        pred[2 * w + 95] = 0.9;
        for x in 10..60 {
            for y in 35..38 {
                pred[y * w + x] = 0.35;
            }
        }
        // The image is twice the map's size.
        let quads = boxes(&pred, w, h, 200, 80);
        assert_eq!(quads.len(), 2, "{quads:?}");
        let first = quads[0];
        assert!(first[0][1] < quads[1][0][1], "top to bottom");
        // Grown by area × 1.6 / perimeter ≈ 4.6 map pixels on each side, then doubled.
        assert!((first[0][0] - 2.0 * (10.0 - 4.6)).abs() <= 2.0, "{first:?}");
        assert!((first[2][0] - 2.0 * (81.0 + 4.6)).abs() <= 3.0, "{first:?}");
        assert!(
            first[0][1] < first[3][1] && first[0][0] < first[1][0],
            "clockwise from the top left"
        );
    }

    #[test]
    fn boxes_on_one_line_read_left_to_right() {
        let b = |x: f32, y: f32| [[x, y], [x + 10.0, y], [x + 10.0, y + 5.0], [x, y + 5.0]];
        let out = sorted(vec![b(50.0, 3.0), b(10.0, 8.0), b(0.0, 40.0)]);
        assert_eq!(
            out.iter().map(|q| q[0][0]).collect::<Vec<_>>(),
            [10.0, 50.0, 0.0]
        );
    }
}

#[cfg(test)]
mod probe {
    /// A probability map saved by the reference pipeline (`MIGHTLING_DOCS_TEST_PRED=<file> <w> <h>`):
    /// prints how many boxes come out, for comparing the two post-processings by hand.
    #[test]
    fn a_saved_map_gives_its_boxes() {
        let Ok(spec) = std::env::var("MIGHTLING_DOCS_TEST_PRED") else {
            return;
        };
        let parts: Vec<&str> = spec.split_whitespace().collect();
        let (w, h): (usize, usize) = (parts[1].parse().unwrap(), parts[2].parse().unwrap());
        let bytes = std::fs::read(parts[0]).unwrap();
        let pred: Vec<f32> = bytes
            .chunks_exact(4)
            .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
            .collect();
        let quads = super::boxes(&pred, w, h, w, h);
        eprintln!("boxes: {}", quads.len());
        for q in quads.iter().take(5) {
            eprintln!("{q:?}");
        }
    }
}
