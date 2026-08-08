"""
Red-Black Tree implementation in Python.
"""
from typing import Optional


class RBNode:
    """Represents a node in the Red-Black Tree."""
    def __init__(self, key: int, color: str = 'RED'):
        self.key = key
        self.color = color  # 'RED' or 'BLACK'
        self.left: Optional['RBNode'] = None
        self.right: Optional['RBNode'] = None
        self.parent: Optional['RBNode'] = None


class RedBlackTree:
    """
    A Red-Black Tree data structure providing O(log n) insert, delete, and search operations.
    Uses a sentinel node (TNULL) to simplify boundary conditions.
    """
    def __init__(self):
        self.TNULL = RBNode(None, 'BLACK')  # Sentinel node
        self.TNULL.parent = None
        self.root: Optional[RBNode] = self.TNULL

    def insert(self, key: int) -> None:
        node = RBNode(key, 'RED')
        node.parent = None
        node.left = self.TNULL
        node.right = self.TNULL
        node.color = 'RED'

        y: Optional[RBNode] = None
        x: Optional[RBNode] = self.root

        while x != self.TNULL:
            y = x
            if node.key < x.key:
                x = x.left
            else:
                x = x.right

        node.parent = y
        if y is None:
            self.root = node
        elif node.key < y.key:
            y.left = node
        else:
            y.right = node

        if node.parent is None:
            node.color = 'BLACK'
            return

        if node.parent.parent is None:
            return

        self._fix_insert(node)

    def _fix_insert(self, k: RBNode) -> None:
        while k != self.root and k.color != 'BLACK' and k.parent.color == 'RED':
            if k == k.parent.parent.left:
                y = k.parent.parent.right
                if y.color == 'RED':
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
                if y.color == 'RED':
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
        if y.left != self.TNULL:
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
        if y.right != self.TNULL:
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
        self._delete_node(self.root, key)

    def _delete_node(self, node: Optional[RBNode], key: int) -> None:
        if node == self.TNULL:
            return
        if key == node.key:
            self._delete_node_impl(node)
        elif key < node.key:
            self._delete_node(node.left, key)
        else:
            self._delete_node(node.right, key)

    def _delete_node_impl(self, z: RBNode) -> None:
        y = z
        y_original_color = y.color
        if z.left == self.TNULL:
            x = z.right
            self._rb_transplant(z, z.right)
        elif z.right == self.TNULL:
            x = z.left
            self._rb_transplant(z, z.left)
        else:
            y = z.right
            while y.left != self.TNULL:
                y = y.left
            if y.parent != z:
                x = y.right
                self._rb_transplant(y, y.right)
                y.right = z.right
                y.right.parent = y
            else:
                x = y.right
            y.color = z.color
            self._rb_transplant(z, y)
            y.left = z.left
            y.left.parent = y
            y.right = z.right
            y.right.parent = y

        if y_original_color == 'BLACK':
            self._fix_delete(x)

    def _rb_transplant(self, u: RBNode, v: RBNode) -> None:
        if u.parent is None:
            self.root = v
        elif u == u.parent.left:
            u.parent.left = v
        else:
            u.parent.right = v
        if v != self.TNULL:
            v.parent = u.parent

    def _fix_delete(self, x: RBNode) -> None:
        while x != self.root and x.color == 'BLACK':
            if x == x.parent.left:
                w = x.parent.right
                if w.color == 'RED':
                    w.color = 'BLACK'
                    x.parent.color = 'RED'
                    self._left_rotate(x.parent)
                    w = x.parent.right
                if w.left.color == 'BLACK' and w.right.color == 'BLACK':
                    w.color = 'RED'
                    x = x.parent
                else:
                    if w.right.color == 'BLACK':
                        w.left.color = 'BLACK'
                        w.color = 'RED'
                        self._right_rotate(w)
                        w = x.parent.right
                    w.color = x.parent.color
                    x.parent.color = 'BLACK'
                    w.right.color = 'BLACK'
                    self._left_rotate(x.parent)
                    x = self.root
            else:
                w = x.parent.left
                if w.color == 'RED':
                    w.color = 'BLACK'
                    x.parent.color = 'RED'
                    self._right_rotate(x.parent)
                    w = x.parent.left
                if w.right.color == 'BLACK' and w.left.color == 'BLACK':
                    w.color = 'RED'
                    x = x.parent
                else:
                    if w.left.color == 'BLACK':
                        w.right.color = 'BLACK'
                        w.color = 'RED'
                        self._left_rotate(w)
                        w = x.parent.left
                    w.color = x.parent.color
                    x.parent.color = 'BLACK'
                    w.left.color = 'BLACK'
                    self._right_rotate(x.parent)
                    x = self.root
        x.color = 'BLACK'

    def search(self, key: int) -> Optional[RBNode]:
        return self._search(self.root, key)

    def _search(self, node: Optional[RBNode], key: int) -> Optional[RBNode]:
        if node == self.TNULL or key == node.key:
            return node
        if key < node.key:
            return self._search(node.left, key)
        return self._search(node.right, key)

    def inorder(self) -> list[int]:
        result = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node: Optional[RBNode], result: list[int]) -> None:
        if node != self.TNULL:
            self._inorder(node.left, result)
            result.append(node.key)
            self._inorder(node.right, result)
