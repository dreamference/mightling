//! The image operations of RapidOCR's pipeline, as OpenCV does them (spec §15.7, §20): the
//! numbers Phase 0 measured came out of `cv2.resize` and `cv2.warpPerspective`, so these follow
//! OpenCV's sampling rules rather than a general image library's.
//!
//! - [`Image::resize`] is `cv2.resize(…, INTER_LINEAR)`: bilinear with half-pixel centres and no
//!   area averaging when shrinking;
//! - [`Image::warp`] is `cv2.warpPerspective(…, INTER_CUBIC, BORDER_REPLICATE)`: bicubic with
//!   a = −0.75, the source pixel of each output pixel found through the inverse transform;
//! - [`Image::rot90`] is `np.rot90` (a quarter turn counter-clockwise), [`Image::rot180`] is
//!   `cv2.rotate(…, ROTATE_180)`.
//!
//! An image is 8-bit, three channels, row-major, in the channel order it was given (RGB here: Phase 0
//! passed PIL's RGB arrays to RapidOCR, which feeds them to the models as they are).

/// An 8-bit, three-channel image.
#[derive(Clone, Debug, PartialEq)]
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub data: Vec<u8>,
}

impl Image {
    pub fn new(width: usize, height: usize) -> Image {
        Image { width, height, data: vec![0; width * height * 3] }
    }

    /// From one 8-bit grey channel, repeated three times (Phase 0 rendered PDF pages grey).
    pub fn from_gray(width: usize, height: usize, gray: &[u8]) -> Image {
        let mut data = Vec::with_capacity(width * height * 3);
        for &g in &gray[..width * height] {
            data.extend([g, g, g]);
        }
        Image { width, height, data }
    }

    #[inline]
    pub fn at(&self, x: usize, y: usize, c: usize) -> u8 {
        self.data[(y * self.width + x) * 3 + c]
    }

    /// The luma of every pixel (BT.601, as `cv2.cvtColor(…, RGB2GRAY)`), for script detection.
    pub fn gray(&self) -> Vec<u8> {
        self.data.chunks_exact(3).map(|p| ((p[0] as u32 * 4899 + p[1] as u32 * 9617 + p[2] as u32 * 1868 + 8192) >> 14) as u8).collect()
    }

    /// `cv2.resize(img, (width, height))` with `INTER_LINEAR`.
    pub fn resize(&self, width: usize, height: usize) -> Image {
        if width == self.width && height == self.height {
            return self.clone();
        }
        let sx = self.width as f64 / width as f64;
        let sy = self.height as f64 / height as f64;
        // Per output column and row: the source index and its weight, OpenCV's clamping.
        let taps = |out: usize, scale: f64, len: usize| -> Vec<(usize, usize, f32)> {
            (0..out)
                .map(|d| {
                    let f = (d as f64 + 0.5) * scale - 0.5;
                    let mut i = f.floor() as isize;
                    let mut w = (f - i as f64) as f32;
                    if i < 0 {
                        i = 0;
                        w = 0.0;
                    }
                    if i as usize >= len - 1 {
                        i = len as isize - 1;
                        w = 0.0;
                    }
                    let i = i as usize;
                    (i, (i + 1).min(len - 1), w)
                })
                .collect()
        };
        let xs = taps(width, sx, self.width);
        let ys = taps(height, sy, self.height);
        let mut out = Image::new(width, height);
        for (y, &(y0, y1, wy)) in ys.iter().enumerate() {
            for (x, &(x0, x1, wx)) in xs.iter().enumerate() {
                for c in 0..3 {
                    let a = self.at(x0, y0, c) as f32 * (1.0 - wx) + self.at(x1, y0, c) as f32 * wx;
                    let b = self.at(x0, y1, c) as f32 * (1.0 - wx) + self.at(x1, y1, c) as f32 * wx;
                    out.data[(y * width + x) * 3 + c] = (a * (1.0 - wy) + b * wy).round().clamp(0.0, 255.0) as u8;
                }
            }
        }
        out
    }

    /// A quarter turn counter-clockwise (`np.rot90`).
    pub fn rot90(&self) -> Image {
        let (w, h) = (self.width, self.height);
        let mut out = Image::new(h, w);
        for y in 0..h {
            for x in 0..w {
                // (x, y) goes to (y, w - 1 - x).
                let (nx, ny) = (y, w - 1 - x);
                for c in 0..3 {
                    out.data[(ny * h + nx) * 3 + c] = self.at(x, y, c);
                }
            }
        }
        out
    }

    /// A half turn.
    pub fn rot180(&self) -> Image {
        let mut out = self.clone();
        let pixels: Vec<[u8; 3]> = self.data.chunks_exact(3).rev().map(|p| [p[0], p[1], p[2]]).collect();
        for (i, p) in pixels.iter().enumerate() {
            out.data[i * 3..i * 3 + 3].copy_from_slice(p);
        }
        out
    }

    /// Black bands above and below (`cv2.copyMakeBorder`, `BORDER_CONSTANT`).
    pub fn pad_vertical(&self, top: usize, bottom: usize) -> Image {
        let mut out = Image::new(self.width, self.height + top + bottom);
        let row = self.width * 3;
        out.data[top * row..(top + self.height) * row].copy_from_slice(&self.data);
        out
    }

    /// The quadrilateral `quad` (top-left, top-right, bottom-right, bottom-left) straightened to a
    /// `width` × `height` image: `cv2.warpPerspective` with `INTER_CUBIC` and `BORDER_REPLICATE`.
    pub fn warp(&self, quad: &[[f32; 2]; 4], width: usize, height: usize) -> Image {
        let dst = [[0.0, 0.0], [width as f64, 0.0], [width as f64, height as f64], [0.0, height as f64]];
        let src: Vec<[f64; 2]> = quad.iter().map(|p| [p[0] as f64, p[1] as f64]).collect();
        // The map from an output pixel to its source point (OpenCV inverts the forward map; this
        // solves for the inverse directly).
        let Some(m) = homography(&dst, &[src[0], src[1], src[2], src[3]]) else { return Image::new(width.max(1), height.max(1)) };
        let mut out = Image::new(width, height);
        for y in 0..height {
            for x in 0..width {
                let (fx, fy) = (x as f64, y as f64);
                let w = m[6] * fx + m[7] * fy + m[8];
                let w = if w.abs() > f64::EPSILON { 1.0 / w } else { 0.0 };
                let sx = (m[0] * fx + m[1] * fy + m[2]) * w;
                let sy = (m[3] * fx + m[4] * fy + m[5]) * w;
                let pixel = self.cubic(sx, sy);
                out.data[(y * width + x) * 3..(y * width + x) * 3 + 3].copy_from_slice(&pixel);
            }
        }
        out
    }

    /// Bicubic sampling at a source point, edges replicated.
    fn cubic(&self, sx: f64, sy: f64) -> [u8; 3] {
        let x0 = sx.floor();
        let y0 = sy.floor();
        let wx = weights((sx - x0) as f32);
        let wy = weights((sy - y0) as f32);
        let clampx = |i: isize| i.clamp(0, self.width as isize - 1) as usize;
        let clampy = |i: isize| i.clamp(0, self.height as isize - 1) as usize;
        let mut out = [0u8; 3];
        for (c, value) in out.iter_mut().enumerate() {
            let mut sum = 0f32;
            for (j, wyj) in wy.iter().enumerate() {
                let yy = clampy(y0 as isize - 1 + j as isize);
                let mut row = 0f32;
                for (i, wxi) in wx.iter().enumerate() {
                    row += self.at(clampx(x0 as isize - 1 + i as isize), yy, c) as f32 * wxi;
                }
                sum += row * wyj;
            }
            *value = sum.round().clamp(0.0, 255.0) as u8;
        }
        out
    }

    /// The model input: `(x / 255 − 0.5) / 0.5`, channels first, `width` wide (the rest zeros).
    pub fn normalized_chw(&self, width: usize) -> Vec<f32> {
        let mut out = vec![0f32; 3 * self.height * width];
        let plane = self.height * width;
        for y in 0..self.height {
            for x in 0..self.width.min(width) {
                for c in 0..3 {
                    out[c * plane + y * width + x] = self.at(x, y, c) as f32 / 127.5 - 1.0;
                }
            }
        }
        out
    }
}

/// OpenCV's bicubic weights (a = −0.75) for a fractional offset.
fn weights(t: f32) -> [f32; 4] {
    const A: f32 = -0.75;
    let w0 = ((A * (t + 1.0) - 5.0 * A) * (t + 1.0) + 8.0 * A) * (t + 1.0) - 4.0 * A;
    let w1 = ((A + 2.0) * t - (A + 3.0)) * t * t + 1.0;
    let w2 = ((A + 2.0) * (1.0 - t) - (A + 3.0)) * (1.0 - t) * (1.0 - t) + 1.0;
    [w0, w1, w2, 1.0 - w0 - w1 - w2]
}

/// The projective map taking `from[i]` to `to[i]`, as nine coefficients (the last 1).
pub fn homography(from: &[[f64; 2]; 4], to: &[[f64; 2]; 4]) -> Option<[f64; 9]> {
    let mut a = [[0f64; 9]; 8];
    for i in 0..4 {
        let ([x, y], [u, v]) = (from[i], to[i]);
        a[2 * i] = [x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y, u];
        a[2 * i + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y, v];
    }
    // Gaussian elimination with partial pivoting on the 8×8 system.
    for col in 0..8 {
        let pivot = (col..8).max_by(|&i, &j| a[i][col].abs().partial_cmp(&a[j][col].abs()).unwrap_or(std::cmp::Ordering::Equal))?;
        if a[pivot][col].abs() < 1e-12 {
            return None;
        }
        a.swap(col, pivot);
        for row in 0..8 {
            if row != col {
                let f = a[row][col] / a[col][col];
                for k in col..9 {
                    a[row][k] -= f * a[col][k];
                }
            }
        }
    }
    let mut m = [0f64; 9];
    for i in 0..8 {
        m[i] = a[i][8] / a[i][i];
    }
    m[8] = 1.0;
    Some(m)
}

/// Python's `round` (half to even), which RapidOCR's sizes go through.
pub fn round_half_even(x: f64) -> f64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 && r % 2.0 != 0.0 { r - x.signum() } else { r }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ramp(w: usize, h: usize) -> Image {
        let mut img = Image::new(w, h);
        for y in 0..h {
            for x in 0..w {
                let v = (x * 10 + y) as u8;
                img.data[(y * w + x) * 3..(y * w + x) * 3 + 3].copy_from_slice(&[v, v, v]);
            }
        }
        img
    }

    #[test]
    fn resizing_follows_opencvs_half_pixel_bilinear() {
        // A 4-pixel row doubled: cv2.resize gives 0, 2.5→3, 7.5→8, 12.5→13 … (halves round up); the ends clamp.
        let img = ramp(4, 1);
        let out = img.resize(8, 1);
        let row: Vec<u8> = (0..8).map(|x| out.at(x, 0, 0)).collect();
        assert_eq!(row, [0, 3, 8, 13, 18, 23, 28, 30]);
        // Halving takes the mean of each pair (no wider filter).
        let half = ramp(8, 2).resize(4, 1);
        assert_eq!(half.at(0, 0, 0), 6);
        assert_eq!(img.resize(4, 1), img);
    }

    #[test]
    fn turns_and_padding() {
        let img = ramp(3, 2);
        let turned = img.rot90();
        assert_eq!((turned.width, turned.height), (2, 3));
        // The top-right pixel ends at the top-left.
        assert_eq!(turned.at(0, 0, 0), img.at(2, 0, 0));
        assert_eq!(img.rot180().at(0, 0, 0), img.at(2, 1, 0));
        let padded = img.pad_vertical(2, 1);
        assert_eq!(padded.height, 5);
        assert_eq!(padded.at(1, 2, 0), img.at(1, 0, 0));
        assert_eq!(padded.at(1, 0, 0), 0);
        assert_eq!(round_half_even(2.5), 2.0);
        assert_eq!(round_half_even(3.5), 4.0);
        assert_eq!(round_half_even(2.4), 2.0);
    }

    #[test]
    fn a_straight_quad_warps_to_the_same_pixels() {
        let img = ramp(20, 10);
        let quad = [[5.0, 2.0], [15.0, 2.0], [15.0, 8.0], [5.0, 8.0]];
        let out = img.warp(&quad, 10, 6);
        for y in 0..6 {
            for x in 0..10 {
                assert_eq!(out.at(x, y, 0), img.at(x + 5, y + 2, 0), "({x},{y})");
            }
        }
        let m = homography(&[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], &[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]).unwrap();
        assert!((m[0] - 2.0).abs() < 1e-9 && (m[4] - 2.0).abs() < 1e-9);
    }
}
