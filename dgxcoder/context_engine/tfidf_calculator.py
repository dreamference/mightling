import math
import re
from typing import Dict, List, Set, Tuple

class TFIDFCalculator:
    """Tokenization and Term Frequency-Inverse Document Frequency calculator."""

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        words = re.findall(r'[a-zA-Z0-9_]+', text)
        tokens: List[str] = []
        for word in words:
            sub_words = re.sub('([a-z0-9])([A-Z])', r'\1 \2', word).lower().split()
            tokens.extend(sub_words)
            tokens.append(word.lower())
        return [t for t in tokens if len(t) > 1]

    @classmethod
    def compute_matrix(
        cls, doc_tokens: Dict[str, List[str]], all_tokens_set: Set[str]
    ) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
        doc_count = len(doc_tokens)
        idf_table: Dict[str, float] = {}
        tf_idf_index: Dict[str, Dict[str, float]] = {}

        if doc_count > 0:
            for token in all_tokens_set:
                df = sum(1 for tokens in doc_tokens.values() if token in tokens)
                idf = math.log((doc_count + 1) / (df + 1)) + 1
                idf_table[token] = idf
                
                tf_idf_index[token] = {}
                for rel_path, tokens in doc_tokens.items():
                    tf = tokens.count(token) / (len(tokens) or 1)
                    if tf > 0:
                        tf_idf_index[token][rel_path] = tf * idf

        return idf_table, tf_idf_index
