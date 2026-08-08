"""Tests for Red-Black Tree implementation."""
import pytest
from dgxcoder.data_structures.red_black_tree import RedBlackTree


class TestRedBlackTree:
    def setup_method(self):
        self.tree = RedBlackTree()

    def test_insert_single(self):
        self.tree.insert(10)
        assert self.tree.search(10) is not None
        assert self.tree.search(10).color == 'BLACK'

    def test_insert_multiple(self):
        self.tree.insert(20)
        self.tree.insert(10)
        self.tree.insert(30)
        assert self.tree.search(10) is not None
        assert self.tree.search(20) is not None
        assert self.tree.search(30) is not None

    def test_insert_duplicate(self):
        self.tree.insert(10)
        self.tree.insert(10)
        assert len(self.tree.inorder()) == 1

    def test_search_existing(self):
        self.tree.insert(10)
        self.tree.insert(20)
        assert self.tree.search(10) is not None
        assert self.tree.search(20) is not None

    def test_search_non_existing(self):
        self.tree.insert(10)
        assert self.tree.search(30) is None

    def test_inorder_traversal(self):
        keys = [50, 30, 20, 40, 70, 60, 80]
        for key in keys:
            self.tree.insert(key)
        assert self.tree.inorder() == [20, 30, 40, 50, 60, 70, 80]

    def test_delete_single(self):
        self.tree.insert(10)
        self.tree.delete(10)
        assert self.tree.search(10) is None

    def test_delete_multiple(self):
        self.tree.insert(20)
        self.tree.insert(10)
        self.tree.insert(30)
        self.tree.delete(10)
        assert self.tree.search(10) is None
        assert self.tree.search(20) is not None
        assert self.tree.search(30) is not None
        assert self.tree.inorder() == [20, 30]

    def test_delete_root(self):
        self.tree.insert(10)
        self.tree.insert(20)
        self.tree.insert(5)
        self.tree.delete(10)
        assert self.tree.search(10) is None
        assert self.tree.search(20) is not None
        assert self.tree.search(5) is not None

    def test_red_black_properties(self):
        # Insert a sequence that typically causes rotations
        keys = [41, 38, 31, 20, 33, 34, 32, 35, 36, 37, 39, 40]
        for key in keys:
            self.tree.insert(key)
        
        assert self._is_red_black(self.tree.root)
        assert self._is_balanced(self.tree.root)

    def _is_red_black(self, node):
        if node is None or node == self.tree.TNULL:
            return True
        if node.color == 'RED':
            if node.left != self.tree.TNULL and node.left.color == 'RED':
                return False
            if node.right != self.tree.TNULL and node.right.color == 'RED':
                return False
        return self._is_red_black(node.left) and self._is_red_black(node.right)

    def _is_balanced(self, node):
        if node is None or node == self.tree.TNULL:
            return 1
        left_black = self._black_height(node.left)
        right_black = self._black_height(node.right)
        if left_black != right_black:
            return False
        return True

    def _black_height(self, node):
        if node is None or node == self.tree.TNULL:
            return 0
        if node.color == 'BLACK':
            return 1 + self._black_height(node.left)
        return self._black_height(node.left)
