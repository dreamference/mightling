//! Recognition's decoding (RapidOCR's `CTCLabelDecode`, spec §20): per time step the most likely
//! class; repeats collapsed, blanks (class 0) dropped; the line's confidence the mean of the kept
//! steps' probabilities (0 when none is kept). The classes are the model's own `character`
//! metadata, one per line as Python's `splitlines` splits it, with the blank before them and a
//! space after.

/// The class list of a recognition model: `blank`, its characters, then a space.
pub fn classes(metadata: &str) -> Vec<String> {
    let mut out = vec![String::new()];
    out.extend(splitlines(metadata));
    out.push(" ".to_string());
    out
}

/// `str.splitlines()`: Python's line boundaries, not only `\n`.
fn splitlines(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut line = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                out.push(std::mem::take(&mut line));
            }
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => out.push(std::mem::take(&mut line)),
            c => line.push(c),
        }
    }
    if !line.is_empty() {
        out.push(line);
    }
    out
}

/// One line's text and confidence from its `steps` × `classes` probabilities.
pub fn decode(probs: &[f32], steps: usize, classes: &[String]) -> (String, f32) {
    let n = classes.len();
    let mut text = String::new();
    let mut confs: Vec<f32> = Vec::new();
    let mut previous = usize::MAX;
    for t in 0..steps {
        let row = &probs[t * n..(t + 1) * n];
        let (best, p) =
            row.iter().enumerate().fold(
                (0usize, f32::MIN),
                |acc, (i, &v)| if v > acc.1 { (i, v) } else { acc },
            );
        if best != 0 && best != previous {
            text.push_str(&classes[best]);
            confs.push(p);
        }
        previous = best;
    }
    let conf = if confs.is_empty() {
        0.0
    } else {
        confs.iter().sum::<f32>() / confs.len() as f32
    };
    (text, conf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeats_collapse_blanks_drop_and_the_confidence_is_the_mean() {
        let classes = classes("a\nb\u{2028}c");
        assert_eq!(classes, ["", "a", "b", "c", " "]);
        // Steps: a, a, blank, a, b, space.
        let one_hot = |i: usize, p: f32| {
            let mut row = vec![(1.0 - p) / 4.0; 5];
            row[i] = p;
            row
        };
        let probs: Vec<f32> = [(1, 0.9), (1, 0.8), (0, 0.99), (1, 0.7), (2, 0.6), (4, 0.5)]
            .iter()
            .flat_map(|&(i, p)| one_hot(i, p))
            .collect();
        let (text, conf) = decode(&probs, 6, &classes);
        assert_eq!(text, "aab ");
        assert!((conf - (0.9 + 0.7 + 0.6 + 0.5) / 4.0).abs() < 1e-6);
        assert_eq!(decode(&one_hot(0, 0.9), 1, &classes), (String::new(), 0.0));
    }
}
