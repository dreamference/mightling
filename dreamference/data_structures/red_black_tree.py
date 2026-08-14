"""
Red-Black Tree implementation in Python.

A Red-Black Tree is a self-balancing binary search tree where each node has an extra bit for denoting the color of the node, either red or black.
The tree maintains balance through a set of properties that ensure O(log n) operations for search, insert, and delete.

Properties:
1. Every node is either red or black.
2. The root is black.
3. All leaves (NIL nodes) are black.
4. If a node is red, then both its children are black.
5. Every path from a node to its descendant leaves contains the same number of black nodes.
"""

from enum import Enum
from typing import Optional, Generic, TypeVar, List, Callable

T = TypeVar('T')


class Color(Enum):
    """Represents the color of a Red-Black Tree node."""
    RED = 0
    BLACK = 1


class RedBlackTreeNode(Generic[T]):
    """
    A node in the Red-Black Tree.
    
    Attributes:
        key: The key value stored in the node.
        value: The value associated with the key.
        color: The color of the node (RED or BLACK).
        left: Reference to the left child node.
        right: Reference to the right child node.
        parent: Reference to the parent node.
    """
    
    def __init__(self, key: T, value: T, color: Color = Color.RED):
        self.key = key
        self.value = value
        self.color = color
        self.left: Optional['RedBlackTreeNode[T]'] = None
        self.right: Optional['RedBlackTreeNode[T]'] = None
        self.parent: Optional['RedBlackTreeNode[T]'] = None
    
    def __repr__(self) -> str:
        color_str = "R" if self.color == Color.RED else "B"
        return f"Node({self.key}:{self.value}, {color_str})"


class RedBlackTree(Generic[T]):
    """
    A Red-Black Tree implementation supporting generic comparable keys.
    
    This implementation uses a sentinel NIL node to simplify boundary conditions.
    All leaf nodes point to this sentinel, which is always black.
    
    Attributes:
        root: The root node of the tree.
        nil: The sentinel NIL node used as a leaf placeholder.
        key_comparator: Optional custom comparator function for keys.
    """
    
    def __init__(self, key_comparator: Optional[Callable[[T, T], int]] = None):
        """
        Initialize an empty Red-Black Tree.
        
        Args:
            key_comparator: Optional function that compares two keys.
                           Should return negative if a < b, zero if a == b, positive if a > b.
                           If None, uses default comparison operators.
        """
        self.nil = RedBlackTreeNode[T](None, None, Color.BLACK)
        self.nil.left = self.nil
        self.nil.right = self.nil
        self.nil.parent = None
        self.root = self.nil
        self.key_comparator = key_comparator
    
    def _compare(self, a: T, b: T) -> int:
        """Compare two keys using the custom comparator or default comparison."""
        if self.key_comparator:
            return self.key_comparator(a, b)
        if a < b:
            return -1
        elif a > b:
            return 1
        return 0
    
    def _is_red(self, node: Optional[RedBlackTreeNode[T]]) -> bool:
        """Check if a node is red."""
        return node is not None and node.color == Color.RED
    
    def _is_black(self, node: Optional[RedBlackTreeNode[T]]) -> bool:
        """Check if a node is black."""
        return node is None or node.color == Color.BLACK or node == self.nil
    
    def _rotate_left(self, x: RedBlackTreeNode[T]) -> None:
        """
        Perform a left rotation on node x.
        
        This operation maintains the binary search tree property while restructuring
        the tree to help maintain Red-Black properties.
        
        Args:
            x: The node to rotate around.
        """
        y = x.right
        x.right = y.left
        
        if y.left != self.nil:
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
    
    def _rotate_right(self, y: RedBlackTreeNode[T]) -> None:
        """
        Perform a right rotation on node y.
        
        This operation maintains the binary search tree property while restructuring
        the tree to help maintain Red-Black properties.
        
        Args:
            y: The node to rotate around.
        """
        x = y.left
        y.left = x.right
        
        if x.right != self.nil:
            x.right.parent = y
        
        x.parent = y.parent
        
        if y.parent is None:
            self.root = x
        elif y == y.parent.right:
            y.parent.right = x
        else:
            y.parent.left = x
        
        x.right = y
        y.parent = x
    
    def _insert_fixup(self, z: RedBlackTreeNode[T]) -> None:
        """
        Restore Red-Black properties after insertion.
        
        This method fixes violations of Red-Black properties that may occur
        after inserting a new red node. It handles three cases:
        1. Uncle is red - recolor
        2. Uncle is black and z is a right child - left rotate
        3. Uncle is black and z is a left child - right rotate
        
        Args:
            z: The newly inserted node.
        """
        while z.parent and z.parent.color == Color.RED:
            if z.parent == z.parent.parent.left:
                y = z.parent.parent.right  # Uncle
                
                if y.color == Color.RED:
                    # Case 1: Uncle is red
                    z.parent.color = Color.BLACK
                    y.color = Color.BLACK
                    z.parent.parent.color = Color.RED
                    z = z.parent.parent
                else:
                    if z == z.parent.right:
                        # Case 2: Uncle is black, z is right child
                        z = z.parent
                        self._rotate_left(z)
                    # Case 3: Uncle is black, z is left child
                    z.parent.color = Color.BLACK
                    z.parent.parent.color = Color.RED
                    self._rotate_right(z.parent.parent)
            else:
                y = z.parent.parent.left  # Uncle
                
                if y.color == Color.RED:
                    # Case 1: Uncle is red
                    z.parent.color = Color.BLACK
                    y.color = Color.BLACK
                    z.parent.parent.color = Color.RED
                    z = z.parent.parent
                else:
                    if z == z.parent.left:
                        # Case 2: Uncle is black, z is left child
                        z = z.parent
                        self._rotate_right(z)
                    # Case 3: Uncle is black, z is right child
                    z.parent.color = Color.BLACK
                    z.parent.parent.color = Color.RED
                    self._rotate_left(z.parent.parent)
        
        self.root.color = Color.BLACK
    
    def insert(self, key: T, value: T) -> None:
        """
        Insert a key-value pair into the tree.
        
        Args:
            key: The key to insert.
            value: The value associated with the key.
        """
        z = RedBlackTreeNode[T](key, value, Color.RED)
        z.left = self.nil
        z.right = self.nil
        
        y: Optional[RedBlackTreeNode[T]] = None
        x = self.root
        
        # Find the correct position for insertion
        while x != self.nil:
            y = x
            if self._compare(key, x.key) < 0:
                x = x.left
            else:
                x = x.right
        
        z.parent = y
        
        if y is None:
            self.root = z
        elif self._compare(key, y.key) < 0:
            y.left = z
        else:
            y.right = z
        
        self._insert_fixup(z)
    
    def search(self, key: T) -> Optional[T]:
        """
        Search for a key in the tree and return its value.
        
        Args:
            key: The key to search for.
            
        Returns:
            The value associated with the key, or None if not found.
        """
        node = self._search_node(key)
        return node.value if node else None
    
    def _search_node(self, key: T) -> Optional[RedBlackTreeNode[T]]:
        """
        Search for a node with the given key.
        
        Args:
            key: The key to search for.
            
        Returns:
            The node containing the key, or None if not found.
        """
        node = self.root
        while node != self.nil:
            cmp = self._compare(key, node.key)
            if cmp == 0:
                return node
            elif cmp < 0:
                node = node.left
            else:
                node = node.right
        return None
    
    def delete(self, key: T) -> bool:
        """
        Delete a key from the tree.
        
        Args:
            key: The key to delete.
            
        Returns:
            True if the key was found and deleted, False otherwise.
        """
        z = self._search_node(key)
        if z is None:
            return False
        
        self._delete_node(z)
        return True
    
    def _delete_node(self, z: RedBlackTreeNode[T]) -> None:
        """
        Delete a node from the tree while maintaining Red-Black properties.
        
        Args:
            z: The node to delete.
        """
        y = z
        y_original_color = y.color
        
        if z.left == self.nil:
            x = z.right
            self._transplant(z, z.right)
        elif z.right == self.nil:
            x = z.left
            self._transplant(z, z.left)
        else:
            y = self._minimum(z.right)
            y_original_color = y.color
            x = y.right
            
            if y.parent == z:
                x.parent = y
            else:
                self._transplant(y, y.right)
                y.right = z.right
                y.right.parent = y
            
            self._transplant(z, y)
            y.left = z.left
            y.left.parent = y
            y.color = z.color
        
        if y_original_color == Color.BLACK:
            self._delete_fixup(x)
    
    def _transplant(self, u: RedBlackTreeNode[T], v: RedBlackTreeNode[T]) -> None:
        """
        Replace subtree rooted at u with subtree rooted at v.
        
        Args:
            u: The node to be replaced.
            v: The node to replace u with.
        """
        if u.parent is None:
            self.root = v
        elif u == u.parent.left:
            u.parent.left = v
        else:
            u.parent.right = v
        v.parent = u.parent
    
    def _delete_fixup(self, x: RedBlackTreeNode[T]) -> None:
        """
        Restore Red-Black properties after deletion.
        
        This method handles four cases to fix violations that may occur
        after deleting a black node.
        
        Args:
            x: The node that replaced the deleted node.
        """
        while x != self.root and self._is_black(x):
            if x == x.parent.left:
                w = x.parent.right
                
                if w.color == Color.RED:
                    w.color = Color.BLACK
                    x.parent.color = Color.RED
                    self._rotate_left(x.parent)
                    w = x.parent.right
                
                if self._is_black(w.left) and self._is_black(w.right):
                    w.color = Color.RED
                    x = x.parent
                else:
                    if self._is_black(w.right):
                        w.left.color = Color.BLACK
                        w.color = Color.RED
                        self._rotate_right(w)
                        w = x.parent.right
                    
                    w.color = x.parent.color
                    x.parent.color = Color.BLACK
                    w.right.color = Color.BLACK
                    self._rotate_left(x.parent)
                    x = self.root
            else:
                w = x.parent.left
                
                if w.color == Color.RED:
                    w.color = Color.BLACK
                    x.parent.color = Color.RED
                    self._rotate_right(x.parent)
                    w = x.parent.left
                
                if self._is_black(w.right) and self._is_black(w.left):
                    w.color = Color.RED
                    x = x.parent
                else:
                    if self._is_black(w.left):
                        w.right.color = Color.BLACK
                        w.color = Color.RED
                        self._rotate_left(w)
                        w = x.parent.left
                    
                    w.color = x.parent.color
                    x.parent.color = Color.BLACK
                    w.left.color = Color.BLACK
                    self._rotate_right(x.parent)
                    x = self.root
        
        x.color = Color.BLACK
    
    def _minimum(self, node: RedBlackTreeNode[T]) -> RedBlackTreeNode[T]:
        """
        Find the node with the minimum key in the subtree rooted at node.
        
        Args:
            node: The root of the subtree.
            
        Returns:
            The node with the minimum key.
        """
        while node.left != self.nil:
            node = node.left
        return node
    
    def _maximum(self, node: RedBlackTreeNode[T]) -> RedBlackTreeNode[T]:
        """
        Find the node with the maximum key in the subtree rooted at node.
        
        Args:
            node: The root of the subtree.
            
        Returns:
            The node with the maximum key.
        """
        while node.right != self.nil:
            node = node.right
        return node
    
    def minimum(self) -> Optional[T]:
        """
        Get the minimum key in the tree.
        
        Returns:
            The minimum key, or None if the tree is empty.
        """
        if self.root == self.nil:
            return None
        return self._minimum(self.root).key
    
    def maximum(self) -> Optional[T]:
        """
        Get the maximum key in the tree.
        
        Returns:
            The maximum key, or None if the tree is empty.
        """
        if self.root == self.nil:
            return None
        return self._maximum(self.root).key
    
    def successor(self, node: RedBlackTreeNode[T]) -> Optional[RedBlackTreeNode[T]]:
        """
        Find the successor of a node (the node with the next larger key).
        
        Args:
            node: The node to find the successor for.
            
        Returns:
            The successor node, or None if no successor exists.
        """
        if node.right != self.nil:
            return self._minimum(node.right)
        
        y = node.parent
        while y is not None and node == y.right:
            node = y
            y = y.parent
        
        return y
    
    def predecessor(self, node: RedBlackTreeNode[T]) -> Optional[RedBlackTreeNode[T]]:
        """
        Find the predecessor of a node (the node with the next smaller key).
        
        Args:
            node: The node to find the predecessor for.
            
        Returns:
            The predecessor node, or None if no predecessor exists.
        """
        if node.left != self.nil:
            return self._maximum(node.left)
        
        y = node.parent
        while y is not None and node == y.left:
            node = y
            y = y.parent
        
        return y
    
    def inorder_traversal(self) -> List[Tuple[T, T]]:
        """
        Perform an inorder traversal of the tree.
        
        Returns:
            A list of (key, value) tuples in sorted order.
        """
        result: List[Tuple[T, T]] = []
        self._inorder_helper(self.root, result)
        return result
    
    def _inorder_helper(self, node: RedBlackTreeNode[T], result: List[Tuple[T, T]]) -> None:
        """Helper method for inorder traversal."""
        if node != self.nil:
            self._inorder_helper(node.left, result)
            result.append((node.key, node.value))
            self._inorder_helper(node.right, result)
    
    def preorder_traversal(self) -> List[Tuple[T, T]]:
        """
        Perform a preorder traversal of the tree.
        
        Returns:
            A list of (key, value) tuples.
        """
        result: List[Tuple[T, T]] = []
        self._preorder_helper(self.root, result)
        return result
    
    def _preorder_helper(self, node: RedBlackTreeNode[T], result: List[Tuple[T, T]]) -> None:
        """Helper method for preorder traversal."""
        if node != self.nil:
            result.append((node.key, node.value))
            self._preorder_helper(node.left, result)
            self._preorder_helper(node.right, result)
    
    def postorder_traversal(self) -> List[Tuple[T, T]]:
        """
        Perform a postorder traversal of the tree.
        
        Returns:
            A list of (key, value) tuples.
        """
        result: List[Tuple[T, T]] = []
        self._postorder_helper(self.root, result)
        return result
    
    def _postorder_helper(self, node: RedBlackTreeNode[T], result: List[Tuple[T, T]]) -> None:
        """Helper method for postorder traversal."""
        if node != self.nil:
            self._postorder_helper(node.left, result)
            self._postorder_helper(node.right, result)
            result.append((node.key, node.value))
    
    def is_valid(self) -> bool:
        """
        Verify that the tree satisfies all Red-Black properties.
        
        Returns:
            True if the tree is valid, False otherwise.
        """
        if self.root == self.nil:
            return True
        
        # Property 2: Root must be black
        if self.root.color != Color.BLACK:
            return False
        
        # Check properties 1, 4, and 5 recursively
        return self._is_valid_helper(self.root)
    
    def _is_valid_helper(self, node: RedBlackTreeNode[T]) -> bool:
        """Helper method to validate Red-Black properties."""
        if node == self.nil:
            return True
        
        # Property 4: Red nodes must have black children
        if node.color == Color.RED:
            if node.left.color == Color.RED or node.right.color == Color.RED:
                return False
        
        # Check black height property
        left_height = self._count_black_height(node.left)
        right_height = self._count_black_height(node.right)
        
        if left_height != right_height:
            return False
        
        return self._is_valid_helper(node.left) and self._is_valid_helper(node.right)
    
    def _count_black_height(self, node: RedBlackTreeNode[T]) -> int:
        """Count the number of black nodes on paths to leaves."""
        if node == self.nil:
            return 1
        
        count = 1 if node.color == Color.BLACK else 0
        return count + self._count_black_height(node.left)
    
    def height(self) -> int:
        """
        Get the height of the tree.
        
        Returns:
            The height of the tree (number of edges on longest path to a leaf).
        """
        return self._height_helper(self.root)
    
    def _height_helper(self, node: RedBlackTreeNode[T]) -> int:
        """Helper method to calculate tree height."""
        if node == self.nil:
            return -1
        
        left_height = self._height_helper(node.left)
        right_height = self._height_helper(node.right)
        
        return 1 + max(left_height, right_height)
    
    def size(self) -> int:
        """
        Get the number of nodes in the tree.
        
        Returns:
            The number of nodes.
        """
        return self._size_helper(self.root)
    
    def _size_helper(self, node: RedBlackTreeNode[T]) -> int:
        """Helper method to count nodes."""
        if node == self.nil:
            return 0
        
        return 1 + self._size_helper(node.left) + self._size_helper(node.right)
    
    def clear(self) -> None:
        """Remove all nodes from the tree."""
        self.root = self.nil
    
    def __len__(self) -> int:
        """Return the number of nodes in the tree."""
        return self.size()
    
    def __contains__(self, key: T) -> bool:
        """Check if a key exists in the tree."""
        return self._search_node(key) is not None
    
    def __getitem__(self, key: T) -> T:
        """Get the value for a key using bracket notation."""
        value = self.search(key)
        if value is None:
            raise KeyError(key)
        return value
    
    def __setitem__(self, key: T, value: T) -> None:
        """Set a key-value pair using bracket notation."""
        self.insert(key, value)
    
    def __delitem__(self, key: T) -> None:
        """Delete a key using del statement."""
        if not self.delete(key):
            raise KeyError(key)
    
    def __iter__(self):
        """Iterate over keys in sorted order."""
        for key, _ in self.inorder_traversal():
            yield key
    
    def __repr__(self) -> str:
        """Return a string representation of the tree."""
        return f"RedBlackTree(size={self.size()}, height={self.height()})"
