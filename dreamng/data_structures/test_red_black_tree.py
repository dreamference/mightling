"""Tests for Red-Black Tree implementation."""
import pytest
import random
from dreamng.data_structures.red_black_tree import RedBlackTree


class TestRedBlackTree:
    @pytest.fixture
    def tree(self):
        return RedBlackTree()

    def test_insert_single(self, tree):
        tree.insert(10)
        assert 10 in tree
        assert tree.search(10).color == 'BLACK'
        assert len(tree) == 1

    def test_insert_multiple(self, tree):
        for key in [20, 10, 30]:
            tree.insert(key)
        assert len(tree) == 3
        assert tree.search(10) is not None
        assert tree.search(20) is not None
        assert tree.search(30) is not None

    def test_insert_duplicate_ignored(self, tree):
        tree.insert(10)
        tree.insert(10)
        assert len(tree) == 1
        assert tree.inorder() == [10]

    def test_search_existing(self, tree):
        tree.insert(10)
        tree.insert(20)
        assert tree.search(10) is not None
        assert tree.search(20) is not None

    def test_search_non_existing(self, tree):
        tree.insert(10)
        assert tree.search(30) is None
        assert 30 not in tree

    def test_inorder_traversal(self, tree):
        keys = [50, 30, 20, 40, 70, 60, 80]
        for key in keys:
            tree.insert(key)
        assert tree.inorder() == [20, 30, 40, 50, 60, 70, 80]

    def test_delete_single(self, tree):
        tree.insert(10)
        tree.delete(10)
        assert 10 not in tree
        assert len(tree) == 0

    def test_delete_multiple(self, tree):
        tree.insert(20)
        tree.insert(10)
        tree.insert(30)
        tree.delete(10)
        assert 10 not in tree
        assert 20 in tree
        assert 30 in tree
        assert tree.inorder() == [20, 30]
        assert len(tree) == 2

    def test_delete_root(self, tree):
        tree.insert(10)
        tree.insert(20)
        tree.insert(5)
        tree.delete(10)
        assert 10 not in tree
        assert 20 in tree
        assert 5 in tree
        assert len(tree) == 2

    def test_delete_all_elements(self, tree):
        for i in range(10):
            tree.insert(i)
        for i in range(10):
            tree.delete(i)
        assert len(tree) == 0
        assert tree.root is None

    def test_red_black_properties(self, tree):
        # Insert a sequence that typically causes rotations
        keys = [41, 38, 31, 20, 33, 34, 32, 35, 36, 37, 39, 40]
        for key in keys:
            tree.insert(key)
        
        assert self._is_red_black(tree.root)
        assert self._is_balanced(tree.root)

    def test_random_operations(self, tree):
        for _ in range(1000):
            test_tree = RedBlackTree()
            num_keys = random.randint(10, 100)
            keys = random.sample(range(-500, 500), num_keys)
            for key in keys:
                test_tree.insert(key)
            
            assert self._is_red_black(test_tree.root)
            assert self._is_balanced(test_tree.root)
            assert test_tree.inorder() == sorted(keys)
            assert len(test_tree) == num_keys

            # Test deletion during random operations
            if test_tree.root:
                del_key = random.choice(keys)
                test_tree.delete(del_key)
                assert del_key not in test_tree
                assert self._is_red_black(test_tree.root)
                assert self._is_balanced(test_tree.root)

    def _is_red_black(self, node):
        if node is None:
            return True
        if node.color == 'RED':
            if node.left and node.left.color == 'RED':
                return False
            if node.right and node.right.color == 'RED':
                return False
        return self._is_red_black(node.left) and self._is_red_black(node.right)

    def _is_balanced(self, node):
        if node is None:
            return 1
        left_black = self._black_height(node.left)
        right_black = self._black_height(node.right)
        if left_black != right_black:
            return False
        return True

    def _black_height(self, node):
        if node is None:
            return 0
        if node.color == 'BLACK':
            return 1 + self._black_height(node.left)
        return self._black_height(node.left)
