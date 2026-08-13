"""
Code Tokenizer & TF-IDF Vector Index Calculator.

This module provides the TFIDFCalculator class for splitting code identifiers (camelCase, snake_case)
and computing term frequency-inverse document frequency weights across workspace documents.
"""

import math
import re
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
        tf_idf_index: Dict[str, Dict[str, float]] = {}

        if doc_count > 0:
            for token in all_tokens_set:
                # Document frequency: number of documents containing token
                df = sum(1 for tokens in doc_tokens.values() if token in tokens)
                # Smoothed inverse document frequency
                idf = math.log((doc_count + 1) / (df + 1)) + 1
                idf_table[token] = idf
                
                tf_idf_index[token] = {}
                for rel_path, tokens in doc_tokens.items():
                    tf = tokens.count(token) / (len(tokens) or 1)
                    if tf > 0:
                        tf_idf_index[token][rel_path] = tf * idf

        return idf_table, tf_idf_index
