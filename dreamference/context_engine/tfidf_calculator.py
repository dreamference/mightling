"""
Code Tokenizer & TF-IDF Vector Index Calculator.

This module provides the TFIDFCalculator class for splitting code identifiers (camelCase, snake_case)
and computing term frequency-inverse document frequency weights across workspace documents.
"""

import math
import re
from collections import Counter
from typing import Dict, List, Set, Tuple

class TFIDFCalculator:
    """
    Tokenizer and vector index calculator for code search queries.
    """

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        """
        Splits source code and natural language text into normalized sub-words and identifiers.

        Handles CamelCase splitting (e.g. 'DreamferenceConfig' -> ['dgx', 'coder', 'config'])
        and snake_case splitting.

        Args:
            text (str): Source text string.

        Returns:
            List[str]: List of normalized lower-case tokens (>1 character).
        """
        words = re.findall(r'[a-zA-Z0-9_]+', text)
        tokens: List[str] = []
        for word in words:
            # Convert CamelCase to spaced sub-words
            sub_words = re.sub('([a-z0-9])([A-Z])', r'\1 \2', word).lower().split()
            tokens.extend(sub_words)
            tokens.append(word.lower())
        return [t for t in tokens if len(t) > 1]

    @classmethod
    def compute_matrix(
        cls, doc_tokens: Dict[str, List[str]], all_tokens_set: Set[str]
    ) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
        """
        Computes IDF table and TF-IDF scores for all tokens across all indexed workspace documents.

        Args:
            doc_tokens (Dict[str, List[str]]): Map of relative file paths to token lists.
            all_tokens_set (Set[str]): Set of all unique tokens across the workspace.

        Returns:
            Tuple[Dict[str, float], Dict[str, Dict[str, float]]]: Tuple of (idf_table, tf_idf_index).
        """
        doc_count = len(doc_tokens)
        idf_table: Dict[str, float] = {}
        tf_idf_index: Dict[str, Dict[str, float]] = {token: {} for token in all_tokens_set}

        # One pass per document. Scanning every document's token list once per token (`in` and
        # `.count` on lists) was quadratic, and took minutes on a workspace of a few thousand files.
        counts = {rel_path: Counter(tokens) for rel_path, tokens in doc_tokens.items()}
        document_frequency: Counter = Counter()
        for counter in counts.values():
            document_frequency.update(counter.keys())

        if doc_count > 0:
            for token in all_tokens_set:
                # Smoothed inverse document frequency
                idf_table[token] = math.log((doc_count + 1) / (document_frequency[token] + 1)) + 1
            for rel_path, counter in counts.items():
                length = len(doc_tokens[rel_path]) or 1
                for token, count in counter.items():
                    if token in tf_idf_index:
                        tf_idf_index[token][rel_path] = (count / length) * idf_table[token]

        return idf_table, tf_idf_index
