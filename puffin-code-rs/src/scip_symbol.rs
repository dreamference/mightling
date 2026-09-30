//! SCIP symbol strings, parsed far enough to match them against the name the agent typed.
//!
//! `scip-python python shapes 7f5b… \`shapes.geometry\`/Circle#area().` and
//! `rust-analyzer cargo geom 0.1.0 shapes/impl#[Circle][Shape]area().` are opaque identities; the
//! router needs their descriptor names (`shapes`, `geometry`, `Circle`, `area`) to find a
//! definition by name when the graph has none (spec §7.4 step 4).

/// The kind of the last descriptor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Namespace,
    Type,
    Term,
    Method,
    Macro,
    Meta,
    Parameter,
    TypeParameter,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Descriptor {
    name: String,
    kind: Kind,
}

/// A symbol reduced to what name matching needs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SymbolName {
    /// Descriptor names outermost first; module paths split on `.` (`shapes.geometry` → two).
    pub segments: Vec<String>,
    /// For a Rust trait implementation's member, the trait (`Shape` in `impl#[Circle][Shape]area().`).
    pub trait_impl: Option<String>,
    pub kind: Kind,
}

impl SymbolName {
    /// The last segment: the name as written in code.
    pub fn name(&self) -> &str {
        self.segments.last().map(String::as_str).unwrap_or("")
    }

    /// `shapes.geometry.Circle.area`.
    pub fn display(&self) -> String {
        self.segments.join(".")
    }

    /// Whether the query's segments (`Circle.area`, `area`) end this symbol's path, in order.
    ///
    /// The last query segment must equal the symbol's name; earlier ones must appear, in order,
    /// before it (so `geometry.area` matches `shapes.geometry.Circle.area`). A trait's name matches
    /// the trait's own member, not its implementations: `Shape::area` is the trait method, and
    /// `impl Shape::area` lists the implementations.
    pub fn matches(&self, query: &[String]) -> bool {
        let Some((last, rest)) = query.split_last() else { return false };
        if self.name() != last {
            return false;
        }
        let haystack: Vec<&str> = self.segments[..self.segments.len() - 1].iter().map(String::as_str).collect();
        let mut position = haystack.len();
        for want in rest.iter().rev() {
            match haystack[..position].iter().rposition(|seg| seg == want) {
                Some(found) => position = found,
                None => return false,
            }
        }
        true
    }
}

/// Splits what the agent typed (`Config::load`, `Circle.area`, `area`) into segments.
pub fn query_segments(query: &str) -> Vec<String> {
    query
        .split(['.', ':', '/', '#'])
        .map(|s| s.trim_end_matches("()"))
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// Parses a SCIP symbol. Returns `None` for local symbols and for parameters, which are never the
/// target of a query.
pub fn parse(symbol: &str) -> Option<SymbolName> {
    if symbol.starts_with("local ") {
        return None;
    }
    let descriptors = parse_descriptors(skip_header(symbol)?)?;
    let last = descriptors.last()?;
    if matches!(last.kind, Kind::Parameter | Kind::TypeParameter) {
        return None;
    }
    let mut segments = Vec::new();
    let mut trait_impl = None;
    let mut index = 0;
    while index < descriptors.len() {
        let descriptor = &descriptors[index];
        match descriptor.kind {
            // rust-analyzer: `impl#[Type][Trait]member` — the members belong to `Type`.
            Kind::Type if descriptor.name == "impl" => {
                let params: Vec<&Descriptor> =
                    descriptors[index + 1..].iter().take_while(|d| d.kind == Kind::TypeParameter).collect();
                if let Some(owner) = params.first() {
                    segments.push(owner.name.clone());
                }
                if let Some(trait_name) = params.get(1) {
                    trait_impl = Some(trait_name.name.clone());
                }
                index += 1 + params.len();
                continue;
            }
            Kind::TypeParameter | Kind::Parameter => {}
            Kind::Namespace => segments.extend(descriptor.name.split('.').filter(|s| !s.is_empty()).map(str::to_string)),
            _ => segments.push(descriptor.name.clone()),
        }
        index += 1;
    }
    Some(SymbolName { segments, trait_impl, kind: last.kind })
}

/// Skips `<scheme> <manager> <package> <version> `, where a double space is an escaped space.
fn skip_header(symbol: &str) -> Option<&str> {
    let bytes = symbol.as_bytes();
    let mut fields = 0;
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b' ' {
            if bytes.get(i + 1) == Some(&b' ') {
                i += 2;
                continue;
            }
            fields += 1;
            if fields == 4 {
                return Some(&symbol[i + 1..]);
            }
        }
        i += 1;
    }
    None
}

fn parse_descriptors(text: &str) -> Option<Vec<Descriptor>> {
    let chars: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        match chars[i] {
            '[' => {
                let (name, next) = read_until(&chars, i + 1, ']')?;
                out.push(Descriptor { name, kind: Kind::TypeParameter });
                i = next + 1;
            }
            '(' => {
                let (name, next) = read_until(&chars, i + 1, ')')?;
                out.push(Descriptor { name, kind: Kind::Parameter });
                i = next + 1;
            }
            _ => {
                let (name, next) = read_name(&chars, i)?;
                let kind = match chars.get(next)? {
                    '/' => Kind::Namespace,
                    '#' => Kind::Type,
                    '.' => Kind::Term,
                    ':' => Kind::Meta,
                    '!' => Kind::Macro,
                    '(' => {
                        let (_, close) = read_until(&chars, next + 1, ')')?;
                        if chars.get(close + 1) != Some(&'.') {
                            return None;
                        }
                        out.push(Descriptor { name, kind: Kind::Method });
                        i = close + 2;
                        continue;
                    }
                    _ => return None,
                };
                out.push(Descriptor { name, kind });
                i = next + 1;
            }
        }
    }
    Some(out)
}

/// Reads a simple or backtick-escaped identifier starting at `i`.
fn read_name(chars: &[char], i: usize) -> Option<(String, usize)> {
    if chars.get(i) == Some(&'`') {
        let mut name = String::new();
        let mut j = i + 1;
        loop {
            match chars.get(j)? {
                '`' if chars.get(j + 1) == Some(&'`') => {
                    name.push('`');
                    j += 2;
                }
                '`' => return Some((name, j + 1)),
                c => {
                    name.push(*c);
                    j += 1;
                }
            }
        }
    }
    let start = i;
    let mut j = i;
    while j < chars.len() && (chars[j].is_alphanumeric() || "_+-$".contains(chars[j])) {
        j += 1;
    }
    (j > start).then(|| (chars[start..j].iter().collect(), j))
}

fn read_until(chars: &[char], i: usize, close: char) -> Option<(String, usize)> {
    if chars.get(i) == Some(&'`') {
        let (name, next) = read_name(chars, i)?;
        return (chars.get(next) == Some(&close)).then_some((name, next));
    }
    let j = i + chars[i..].iter().position(|c| *c == close)?;
    Some((chars[i..j].iter().collect(), j))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_method() {
        let s = parse("scip-python python shapes 7f5b `shapes.geometry`/Circle#area().").unwrap();
        assert_eq!(s.segments, ["shapes", "geometry", "Circle", "area"]);
        assert_eq!(s.kind, Kind::Method);
        assert!(s.matches(&query_segments("area")));
        assert!(s.matches(&query_segments("Circle.area")));
        assert!(s.matches(&query_segments("geometry.area")));
        assert!(!s.matches(&query_segments("Square.area")));
    }

    #[test]
    fn rust_trait_impl() {
        let s = parse("rust-analyzer cargo geom 0.1.0 shapes/impl#[Circle][Shape]area().").unwrap();
        assert_eq!(s.segments, ["shapes", "Circle", "area"]);
        assert_eq!(s.trait_impl.as_deref(), Some("Shape"));
        assert!(s.matches(&query_segments("Circle::area")));
        assert!(!s.matches(&query_segments("Shape::area")));
        let t = parse("rust-analyzer cargo geom 0.1.0 shapes/Shape#area().").unwrap();
        assert!(t.matches(&query_segments("Shape::area")));
        assert!(!t.matches(&query_segments("Circle::area")));
    }

    #[test]
    fn parameters_and_locals_are_not_targets() {
        assert!(parse("scip-python python shapes 7f5b `shapes.geometry`/Circle#area().(self)").is_none());
        assert!(parse("local 12").is_none());
    }

    #[test]
    fn escaped_spaces_in_the_header() {
        let s = parse("scip-typescript npm my  pkg 1.0 src/`a.ts`/f().").unwrap();
        assert_eq!(s.name(), "f");
    }
}
