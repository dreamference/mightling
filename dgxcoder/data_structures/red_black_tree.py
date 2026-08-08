"""
Red-Black Tree implementation in Python.
"""
from __future__ import annotations

from typing import Iterator, Optional


class RBNode:
    """Represents a node in the Red-Black Tree."""
    def __init__(self, key: int, color: str = 'RED'):
        self.key = key
        self.color = color  # 'RED' or 'BLACK'
        self.left: Optional[RBNode] = None
        self.right: Optional[RBNode] = None
        self.parent: Optional[RBNode] = None

    def __repr__(self) -> str:
        return f"RBNode(key={self.key}, color={self.color})"


class RedBlackTree:
    """
    A Red-Black Tree data structure providing O(log n) insert, delete, and search operations.
    Maintains balance through color properties and rotations.
    """
    def __init__(self):
        self.root: Optional[RBNode] = None
        self._size: int = 0

    def insert(self, key: int) -> None:
        if self.root is None:
            self.root = RBNode(key, 'BLACK')
            self._size += 1
            return

        node = RBNode(key, 'RED')
        parent = None
        current = self.root

        while current is not None:
            parent = current
            if key < current.key:
                current = current.left
            elif key > current.key:
                current = current.right
            else:
                # Duplicate keys are ignored to maintain strict ordering
                return

        node.parent = parent
        if key < parent.key:
            parent.left = node
        else:
            parent.right = node

        self._fix_insert(node)
        self._size += 1

    def _fix_insert(self, k: RBNode) -> None:
        while k != self.root and k.color != 'BLACK' and k.parent.color == 'RED':
            if k == k.parent.left:
                y = k.parent.parent.right
                if y and y.color == 'RED':
                    k.parent.color = 'BLACK'
                    y.color = 'BLACK'
                    k.parent.parent.color = 'RED'
                    k = k.parent.parent
                else:
                    if k == k.parent.right:
                        k = k.parent
                        self._left_rotate(k)
                    k.parent.color = 'BLACK'
                    k.parent.parent.color = 'RED'
                    self._right_rotate(k.parent.parent)
            else:
                y = k.parent.parent.left
                if y and y.color == 'RED':
                    k.parent.color = 'BLACK'
                    y.color = 'BLACK'
                    k.parent.parent.color = 'RED'
                    k = k.parent.parent
                else:
                    if k == k.parent.left:
                        k = k.parent
                        self._right_rotate(k)
                    k.parent.color = 'BLACK'
                    k.parent.parent.color = 'RED'
                    self._left_rotate(k.parent.parent)
        self.root.color = 'BLACK'

    def _left_rotate(self, x: RBNode) -> None:
        y = x.right
        x.right = y.left
        if y.left:
            y.left.parent = x
        y.parent = x.parent
        if x.parent is None:
            self.root = y
        elif x == x.parent.left:
            x.parent.left = y
        else:
            x.parent.right = y
        y.left = x
        x.parent = y

    def _right_rotate(self, x: RBNode) -> None:
        y = x.left
        x.left = y.right
        if y.right:
            y.right.parent = x
        y.parent = x.parent
        if x.parent is None:
            self.root = y
        elif x == x.parent.right:
            x.parent.right = y
        else:
            x.parent.left = y
        y.right = x
        x.parent = y

    def delete(self, key: int) -> None:
        node = self.search(key)
        if node is None:
            return
        self._delete_node_impl(node)
        self._size -= 1

    def _delete_node_impl(self, z: RBNode) -> None:
        y = z
        y_original_color = y.color
        if z.left is None:
            x = z.right
            self._rb_transplant(z, z.right)
        elif z.right is None:
            x = z.left
            self._rb_transplant(z, z.left)
        else:
            y = z.right
            while y.left is not None:
                y = y.left
            if y.parent != z:
                x = y.right
                self._rb_transplant(y, y.right)
                y.right = z.right
                if y.right:
                    y.right.parent = y
            else:
                x = y.right
            y.color = z.color
            self._rb_transplant(z, y)
            y.left = z.left
            if y.left:
                y.left.parent = y
            y.right = z.right
            if y.right:
                y.right.parent = y

        if y_original_color == 'BLACK':
            self._fix_delete(x)

    def _rb_transplant(self, u: RBNode, v: Optional[RBNode]) -> None:
        if u.parent is None:
            self.root = v
        elif u == u.parent.left:
            u.parent.left = v
        else:
            u.parent.right = v
        if v:
            v.parent = u.parent

    def _fix_delete(self, x: Optional[RBNode]) -> None:
        while x != self.root and x and x.color == 'BLACK':
            if x == x.parent.left:
                w = x.parent.right
                if w and w.color == 'RED':
                    w.color = 'BLACK'
                    x.parent.color = 'RED'
                    self._left_rotate(x.parent)
                    w = x.parent.right
                if (w.left is None or w.left.color == 'BLACK') and (w.right is None or w.right.color == 'BLACK'):
                    w.color = 'RED'
                    x = x.parent
                else:
                    if w.right is None or w.right.color == 'BLACK':
                        if w.left:
                            w.left.color = 'BLACK'
                        w.color = 'RED'
                        self._right_rotate(w)
                        w = x.parent.right
                    w.color = x.parent.color
                    x.parent.color = 'BLACK'
                    if w.right:
                        w.right.color = 'BLACK'
                    self._left_rotate(x.parent)
                    x = self.root
            else:
                w = x.parent.left
                if w and w.color == 'RED':
                    w.color = 'BLACK'
                    x.parent.color = 'RED'
                    self._right_rotate(x.parent)
                    w = x.parent.left
                if (w.right is None or w.right.color == 'BLACK') and (w.left is None or w.left.color == 'BLACK'):
                    w.color = 'RED'
                    x = x.parent
                else:
                    if w.left is None or w.left.color == 'BLACK':
                        if w.right:
                            w.right.color = 'BLACK'
                        w.color = 'RED'
                        self._left_rotate(w)
                        w = x.parent.left
                    w.color = x.parent.color
                    x.parent.color = 'BLACK'
                    if w.left:
                        w.left.color = 'BLACK'
                    self._right_rotate(x.parent)
                    x = self.root
        if x:
            x.color = 'BLACK'

    def search(self, key: int) -> Optional[RBNode]:
        current = self.root
        while current is not None:
            if key == current.key:
                return current
            elif key < current.key:
                current = current.left
            else:
                current = current.right
        return None

    def __contains__(self, key: int) -> bool:
        return self.search(key) is not None

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[int]:
        return self._inorder_iter(self.root)

    def _inorder_iter(self, node: Optional[RBNode]) -> Iterator[int]:
        if node:
            yield from self._inorder_iter(node.left)
            yield node.key
            yield from self._inorder_iter(node.right)

    def inorder(self) -> list[int]:
        return list(self)

    def __repr__(self) -> str:
        return f"RedBlackTree(size={self._size}, keys={self.inorder()})"
